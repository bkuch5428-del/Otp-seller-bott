import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import parse_qs, urlsplit
from unittest.mock import ANY, AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test-hash")
os.environ.setdefault("BOT_TOKEN", "1:test-token")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("FAMAPP_UPI_ID", "famapp@example")
os.environ.setdefault("FAMAPP_PAYEE_NAME", "Test Payee")
os.environ.setdefault("IMAP_USERNAME", "test@example.com")
os.environ.setdefault("IMAP_APP_PASSWORD", "test-password")

from test_mongo_persistence import FakeDatabase  # noqa: E402
import mongo_persistence  # noqa: E402
from mongo_persistence import MongoRepository, MongoRuntimeStore  # noqa: E402


class FakeMongoClient:
    def __init__(self):
        self.database = FakeDatabase()
        self.admin = self

    def command(self, _name):
        return {"ok": 1}

    def __getitem__(self, _name):
        return self.database


with patch.object(mongo_persistence, "create_mongo_client", return_value=FakeMongoClient()):
    import james  # noqa: E402


class FakeImap:
    raw_email = b"""From: no-reply@famapp.in
Subject: You received \xe2\x82\xb920 in your FamX account
Date: Fri, 04 Sep 2026 12:00:00 +0000
Content-Type: text/plain; charset=utf-8

You have successfully received \xe2\x82\xb920
Purpose :
FAP20260904ABC123
"""

    def __init__(self, *_args):
        self.closed = False

    def login(self, *_args):
        return "OK", []

    def select(self, _mailbox):
        return "OK", []

    def search(self, *_args):
        return "OK", [b"1"]

    def fetch(self, *_args):
        return "OK", [(b"header", self.raw_email)]

    def close(self):
        self.closed = True
        return "OK", []

    def logout(self):
        return "BYE", []


class AutomaticPaymentTests(unittest.TestCase):
    def test_uri_and_qr_contain_amount_and_hidden_purpose(self):
        purpose = "FAP20260904ABC123"
        uri = james.build_automatic_upi_uri(20, purpose)
        params = parse_qs(urlsplit(uri).query)
        self.assertEqual(params["pa"], ["famapp@example"])
        self.assertEqual(params["am"], ["20"])
        self.assertEqual(params["tn"], [purpose])
        self.assertNotIn(james.FAMAPP_UPI_ID, "🏦 AUTOMATIC PAYMENT (UPI) Amount: ₹20")
        qr = james.generate_automatic_qr(20, purpose)
        self.assertEqual(qr.name, "automatic-payment.png")
        self.assertGreater(len(qr.getvalue()), 100)
        payment_screen = james.automatic_payment_message(20, "ORD-20260904-ABC12345")
        self.assertNotIn(james.FAMAPP_UPI_ID, payment_screen)
        self.assertNotIn(purpose, payment_screen)
        self.assertIn("₹20", payment_screen)
        self.assertIn("ORD-20260904-ABC12345", payment_screen)

    def test_email_verification_requires_received_amount_and_purpose(self):
        order = {
            "amount_inr": 20,
            "payment_purpose": "FAP20260904ABC123",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        }
        with patch.object(james.imaplib, "IMAP4_SSL", FakeImap):
            self.assertEqual(james.verify_automatic_payment(order), "success")
        wrong_amount = dict(order, amount_inr=21)
        with patch.object(james.imaplib, "IMAP4_SSL", FakeImap):
            self.assertEqual(james.verify_automatic_payment(wrong_amount), "pending")
        expired = dict(order, expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        with patch.object(james.imaplib, "IMAP4_SSL", FakeImap):
            self.assertEqual(james.verify_automatic_payment(expired), "expired")

    def test_mongodb_order_persists_and_credit_is_idempotent(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        store = MongoRuntimeStore(repository)
        store.ensure_user(7)
        store.create_automatic_payment(
            "ORD-20260904-ABC12345",
            7,
            20,
            "FAP20260904ABC123",
            "upi://pay?tn=FAP20260904ABC123",
            datetime.now(timezone.utc).isoformat(),
            (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        )
        restarted = MongoRuntimeStore(MongoRepository(database))
        order = restarted.get_automatic_payment("ORD-20260904-ABC12345")
        self.assertEqual(order["status"], "pending")
        first = restarted.credit_balance(
            7, 20, "automatic_payment:ORD-20260904-ABC12345", "automatic_payment_credit",
            extra_inc={"total_deposited": 20},
        )
        second = restarted.credit_balance(
            7, 20, "automatic_payment:ORD-20260904-ABC12345", "automatic_payment_credit",
            extra_inc={"total_deposited": 20},
        )
        self.assertTrue(first["applied"])
        self.assertTrue(second["already_applied"])
        self.assertEqual(restarted.get_balance(7), 20)
        self.assertTrue(restarted.update_automatic_payment_status(
            "ORD-20260904-ABC12345", "pending", "verified", "verified"
        ))
        self.assertEqual(restarted.get_automatic_payment("ORD-20260904-ABC12345")["status"], "verified")

    def test_deposit_menu_and_callbacks_keep_existing_paths(self):
        source = open("james.py", encoding="utf-8").read()
        self.assertIn('Button.inline("⚡ Automatic Payment", "automatic_payment")', source)
        self.assertIn('data == "automatic_payment"', source)
        self.assertIn('data.startswith("automatic_check|")', source)
        self.assertIn('data.startswith("automatic_cancel|")', source)
        self.assertIn('data == "dep_upi"', source)
        self.assertIn('casefold() == "cwallet"', source)
        self.assertNotIn('Button.inline(f"👛 Cwallet (+5%)", "depm_Cwallet")', source)
        self.assertIn('if keypad_step == "automatic_keypad":', source)
        self.assertIn('keypad_step = keypad_state.get("step", "upi_keypad")', source)
        self.assertIn("deposit_input[uid] = {'step': keypad_step, 'val': curr}", source)
        self.assertIn("return await show_automatic_payment_qr(event, amt)", source)
        self.assertNotIn(
            "deposit_input[uid] = {'step': 'upi_keypad', 'val': curr}",
            source,
        )

    def test_amount_keypad_preserves_automatic_workflow_on_confirm(self):
        class KeypadEvent:
            sender_id = 55
            chat_id = 55

            def __init__(self, action):
                self.data = action.encode()
                self.edits = []

            async def edit(self, message, buttons=None):
                self.edits.append((message, buttons))

            async def answer(self, *args, **kwargs):
                return None

        async def exercise():
            james.deposit_input[55] = {"step": "automatic_keypad", "val": "0"}
            digit_event = KeypadEvent("kp_2")
            await james.keypad_logic(digit_event)
            self.assertEqual(james.deposit_input[55], {"step": "automatic_keypad", "val": "2"})
            with (
                patch.object(james, "show_automatic_payment_qr", new=AsyncMock()) as automatic_qr,
                patch.object(james, "show_upi_qr", new=AsyncMock()) as manual_qr,
            ):
                await james.keypad_logic(KeypadEvent("kp_done"))
            automatic_qr.assert_awaited_once()
            manual_qr.assert_not_awaited()
            automatic_qr.assert_awaited_once_with(ANY, 2)

        asyncio.run(exercise())

    def test_support_screen_has_exact_layout_and_persistent_owner_destination(self):
        buttons = james.get_support_buttons()
        self.assertEqual(len(buttons), 3)
        self.assertEqual([button.text for button in buttons[0]], ["🆘 Support", "👑 Owner"])
        self.assertEqual([button.text for button in buttons[1]], ["📢 Channel"])
        self.assertEqual([button.text for button in buttons[2]], ["📜 Terms & Conditions"])
        self.assertEqual(buttons[-1][0].text, "📜 Terms & Conditions")

        source = open("james.py", encoding="utf-8").read()
        self.assertIn('"🟢 <b>Support</b>\\n\\n⚠️ For support, contact admin."', source)
        self.assertIn('"owner_username"', source)
        self.assertIn('"adm_setting_edit|owner_username"', source)
        james.set_setting("owner_username", "@updated_owner")
        try:
            self.assertEqual(james.get_owner_url(), "https://t.me/updated_owner")
            updated_buttons = james.get_support_buttons()
            self.assertEqual(updated_buttons[0][1].url, "https://t.me/updated_owner")
        finally:
            james.delete_setting("owner_username")

    def test_payment_method_visibility_defaults_and_persists_independently(self):
        james.delete_setting("payment_method_automatic_enabled")
        james.delete_setting("payment_method_manual_enabled")
        self.assertTrue(james.is_payment_method_enabled("automatic"))
        self.assertTrue(james.is_payment_method_enabled("manual"))

        james.set_setting("payment_method_automatic_enabled", "off")
        self.assertFalse(james.is_payment_method_enabled("automatic"))
        self.assertTrue(james.is_payment_method_enabled("manual"))
        james.set_setting("payment_method_manual_enabled", "off")
        self.assertFalse(james.is_payment_method_enabled("manual"))
        self.assertFalse(james.is_payment_method_enabled("automatic"))

        restarted = james.MongoRuntimeStore(james.mongo_repository)
        self.assertEqual(restarted.get_setting("payment_method_automatic_enabled"), "off")
        self.assertEqual(restarted.get_setting("payment_method_manual_enabled"), "off")

        james.set_setting("payment_method_automatic_enabled", "on")
        self.assertTrue(james.is_payment_method_enabled("automatic"))
        self.assertFalse(james.is_payment_method_enabled("manual"))
        james.delete_setting("payment_method_automatic_enabled")
        james.delete_setting("payment_method_manual_enabled")

    def test_payment_method_visibility_ui_and_server_guards_are_present(self):
        source = open("james.py", encoding="utf-8").read()
        self.assertIn('"payment_method_automatic_enabled"', source)
        self.assertIn('"payment_method_manual_enabled"', source)
        self.assertIn('"⚠️ No payment methods are currently available.', source)
        self.assertIn('data == "automatic_payment"', source)
        self.assertIn('data == "dep_upi"', source)
        self.assertIn('Automatic Payment is currently unavailable.', source)
        self.assertIn('Manual Payment is currently unavailable.', source)
        self.assertIn('"adm_payment_toggle|automatic"', source)
        self.assertIn('"adm_payment_toggle|manual"', source)


if __name__ == "__main__":
    unittest.main()
