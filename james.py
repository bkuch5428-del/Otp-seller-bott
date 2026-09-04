import os
import re
import asyncio
import time
import logging
import email
import imaplib
import secrets
import string
import aiohttp
from aiohttp import web
import csv
import zipfile
import shutil
import html
import json
import hashlib
import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import parsedate_to_datetime
from io import BytesIO
from urllib.parse import quote, urlencode
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from mongo_persistence import MongoRepository, MongoRuntimeStore, create_mongo_client

from telethon import TelegramClient, events, Button
from telethon.errors import (
    SessionPasswordNeededError, 
    MessageNotModifiedError,
    FloodWaitError,
    UserNotParticipantError,
    ChatAdminRequiredError
)
from telethon.tl.types import ReplyKeyboardMarkup, KeyboardButtonRow, KeyboardButton, InputPhoto
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.functions.account import GetPasswordRequest
from telethon.sessions import StringSession
from cryptography.fernet import Fernet, InvalidToken

# ================= CONFIGURATION =================
def load_env_file(path=".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except Exception as ex:
        print(f"Failed to load {path}: {ex}")

load_env_file()

def env_int(name, default=0):
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default

def env_list(name, default_csv=""):
    raw = os.getenv(name, default_csv)
    return [item.strip() for item in raw.split(",") if item.strip()]

def get_session_cipher():
    seed = SESSION_ENCRYPTION_KEY or f"{BOT_TOKEN}:{API_HASH}:telethon-sessions"
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
    return Fernet(key)

def encrypt_session_string(session_string):
    if not session_string:
        return None
    return get_session_cipher().encrypt(session_string.encode()).decode()

def decrypt_session_string(value):
    if not value:
        return None
    try:
        return get_session_cipher().decrypt(str(value).encode()).decode()
    except (InvalidToken, TypeError, ValueError):
        logger.warning("Stored Telegram session could not be decrypted")
        return None

API_ID = env_int("API_ID")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ========== BOTH ADMINS ==========
ADMIN_ID = env_int("ADMIN_ID")
ADMIN_IDS = [ADMIN_ID] if ADMIN_ID else []

# ========== CHANNELS ==========
LOG_CHANNEL_ID = env_int("LOG_CHANNEL_ID", -1004359100536)
REQUIRED_CHANNELS = env_list("REQUIRED_CHANNELS", os.getenv("CHECK_CHANNELS", "-1004359100536"))
JOIN_URLS = env_list("JOIN_URLS", "https://t.me/moviesmasterupdates")

# ========== LINKS & MEDIA ==========
TERMS_URL = os.getenv(
    "TERMS_URL",
    "https://james-xdd.github.io/Terms-And-Conditions/James.html"
)

# ========== UPI DETAILS ==========
UPI_ID = os.getenv("UPI_ID", "bobbyahirwar@fam")
UPI_QR = os.getenv("UPI_QR", "https://files.catbox.moe/m5c01u.jpg")

# ========== SUPPORT CONTACTS ==========
SUPPORT_USERNAME_1 = os.getenv("SUPPORT_USERNAME_1", "Your_cuteexd")
SUPPORT_USERNAME_2 = os.getenv("SUPPORT_USERNAME_2", "Know_Your_Papa")

OTP_REGEX = os.getenv("OTP_REGEX", r"\b\d{4,8}\b")
AUTO_CANCEL_SECONDS = env_int("AUTO_CANCEL_SECONDS", 600)
DEFAULT_USDT_RATE = os.getenv("DEFAULT_USDT_RATE", "94.0")
DEFAULT_SUPPORT_URL = os.getenv("DEFAULT_SUPPORT_URL", "https://t.me/tgtelehelpbot")
OWNER_USERNAME_SETTING = "owner_username"
FAMAPP_UPI_ID = os.getenv("FAMAPP_UPI_ID", "").strip()
FAMAPP_PAYEE_NAME = os.getenv("FAMAPP_PAYEE_NAME", "FreshTgStore").strip() or "FreshTgStore"
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com").strip() or "imap.gmail.com"
IMAP_PORT = env_int("IMAP_PORT", 993)
IMAP_USERNAME = os.getenv("IMAP_USERNAME", "").strip()
IMAP_APP_PASSWORD = os.getenv("IMAP_APP_PASSWORD", "").strip()
IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX").strip() or "INBOX"
IMAP_SENDER_FILTER = os.getenv("IMAP_SENDER_FILTER", "no-reply@famapp.in").strip()
IMAP_LOOKBACK_HOURS = env_int("IMAP_LOOKBACK_HOURS", env_int("GMAIL_LOOKBACK_HOURS", 24))
AUTOMATIC_PAYMENT_EXPIRY_MINUTES = env_int("AUTOMATIC_PAYMENT_EXPIRY_MINUTES", 10)
FAMAPP_PURPOSE_PREFIX = os.getenv("FAMAPP_PURPOSE_PREFIX", "FAP").strip().upper() or "FAP"
SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY", "").strip()

# ================= PREMIUM EMOJIS =================
USE_PREMIUM_EMOJIS = os.getenv("USE_PREMIUM_EMOJIS", "1").strip().lower() not in {"0", "false", "no", "off"}
PREMIUM_EMOJIS = {
    "heart_fire": os.getenv("PREMIUM_EMOJI_HEART_FIRE", "5042225965518816316"),
    "lightning": os.getenv("PREMIUM_EMOJI_LIGHTNING", "5042334757040423886"),
    "location": os.getenv("PREMIUM_EMOJI_LOCATION", "5039775669496579510"),
    "flower": os.getenv("PREMIUM_EMOJI_FLOWER", "6073117703965511893"),
    "check": os.getenv("PREMIUM_EMOJI_CHECK", "6147460667281511517"),
    "crown": os.getenv("PREMIUM_EMOJI_CROWN", "6235252066554484059"),
    "kiss": os.getenv("PREMIUM_EMOJI_KISS", "6116282026506065674"),
    "skull": os.getenv("PREMIUM_EMOJI_SKULL", "6089128873893563936"),
    "xmas": os.getenv("PREMIUM_EMOJI_XMAS", "6267071898702583835"),
    "monkey": os.getenv("PREMIUM_EMOJI_MONKEY", "6273627839862411998"),
    "gift": os.getenv("PREMIUM_EMOJI_GIFT", "5893175870096414393"),
    "angel": os.getenv("PREMIUM_EMOJI_ANGEL", "5893411041030707544"),
    "devil": os.getenv("PREMIUM_EMOJI_DEVIL", "5893079628469246474"),
}

def tg_emoji(name, fallback):
    emoji_id = PREMIUM_EMOJIS.get(name)
    if USE_PREMIUM_EMOJIS and emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback

PE_HEART = tg_emoji("heart_fire", "❤️‍🔥")
PE_LIGHTNING = tg_emoji("lightning", "⚡")
PE_LOCATION = tg_emoji("location", "📍")
PE_FLOWER = tg_emoji("flower", "🌸")
PE_CHECK = tg_emoji("check", "✅")
PE_CROWN = tg_emoji("crown", "👑")
PE_KISS = tg_emoji("kiss", "😘")
PE_SKULL = tg_emoji("skull", "💀")
PE_XMAS = tg_emoji("xmas", "🎄")
PE_MONKEY = tg_emoji("monkey", "🐵")
PE_GIFT = tg_emoji("gift", "🎁")
PE_ANGEL = tg_emoji("angel", "😇")
PE_DEVIL = tg_emoji("devil", "😈")

# ================= UI ICONS =================
P_YES = PE_CHECK
P_NO = '❌'
P_PKG = '📦'
P_MONEY = '💰'
P_USDT = '💲'
P_INR = '₹'
P_TG = '✈️'
P_GIFT = PE_GIFT
P_STATS = '📊'
P_CARD = '💳'
P_USERS = '👥'
P_CAL = '📅'
P_PC = '💻'
P_EYE = '👁️'
P_UPI = '🏦'
P_CW = '👛'
P_ON = '🟢'
P_OFF = '🔴'
P_ID = '🆔'
P_KEY = '⌨️'
P_GLOBE = PE_LOCATION
P_CART = '🛒'
P_STORE = '🏬'
P_OTP = '🔢'
P_2FA = '🔐'
P_FLAG = '🏳️'
P_PHONE = '📱'
P_WAIT = '⏳'
P_TIME = '⏰'
P_WARN = '⚠️'
P_DOC = '📃'
P_SOS = '🆘'
P_ASST = '🤖'
P_ACC = '👤'
P_SCREEN = '🖼️'
P_UTR = '🧾'

# ========== URL HELPER ==========
def fix_url(url):
    if not url:
        return DEFAULT_SUPPORT_URL
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        if url.startswith("t.me/") or url.startswith("@") or "t.me" in url:
            if url.startswith("@"):
                url = "t.me/" + url[1:]
            elif not url.startswith("t.me/"):
                url = "t.me/" + url
            url = "https://" + url
        else:
            url = "https://" + url
    return url

# ========== VALIDATE CONFIG ==========
def validate_config():
    missing = []
    # Telegram credentials and the primary administrator are required to start.
    if API_ID <= 0:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_ID")

    # Premium emoji IDs are only needed when premium emoji rendering is enabled.
    if USE_PREMIUM_EMOJIS:
        for name, value in PREMIUM_EMOJIS.items():
            if not value:
                missing.append(f"PREMIUM_EMOJI_{name.upper()}")
    if missing:
        raise RuntimeError("Missing/invalid environment variables: " + ", ".join(missing))
    if not REQUIRED_CHANNELS:
        logger.warning("REQUIRED_CHANNELS is empty; join verification will be ineffective.")
    if not JOIN_URLS:
        logger.warning("JOIN_URLS is empty; users will not see join buttons.")
    if REQUIRED_CHANNELS and JOIN_URLS and len(REQUIRED_CHANNELS) != len(JOIN_URLS):
        logger.warning(
            "REQUIRED_CHANNELS (%s) and JOIN_URLS (%s) lengths differ.",
            len(REQUIRED_CHANNELS), len(JOIN_URLS)
        )

# ================= INITIALIZATION =================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "otp_seller_bot")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "inventory")

mongo_client = None
mongo_repository = None
mongo_store = None


def initialize_mongo_runtime():
    """Connect to MongoDB before starting the bot; never fall back silently."""
    global mongo_client, mongo_repository, mongo_store
    try:
        if not MONGODB_URI:
            raise RuntimeError("MONGODB_URI is required for MongoDB runtime")
        mongo_client = create_mongo_client(MONGODB_URI)
        mongo_client.admin.command("ping")
        mongo_repository = MongoRepository(
            mongo_client[MONGODB_DB],
            inventory_collection_name=MONGODB_COLLECTION,
        )
        prepare_report = mongo_repository.prepare()
        prepare_errors = []
        for collection_name, details in prepare_report["collections"].items():
            if details.get("error"):
                prepare_errors.append(f"{collection_name}: {details['error']}")
        for collection_name, details in prepare_report["indexes"].items():
            for error in details.get("errors", []):
                prepare_errors.append(f"{collection_name}: {error}")
        if prepare_errors:
            raise RuntimeError(
                "MongoDB collection/index preparation failed: "
                + "; ".join(prepare_errors)
            )
        mongo_store = MongoRuntimeStore(mongo_repository)
        logger.info("MongoDB runtime initialized for database %s", MONGODB_DB)
    except Exception as exc:
        raise RuntimeError(f"MongoDB runtime initialization failed: {exc}") from exc


validate_config()
initialize_mongo_runtime()

os.makedirs("sessions", exist_ok=True)
os.makedirs("screenshots", exist_ok=True)

session_name = f"bot_session_{BOT_TOKEN.split(':')[0]}"
stored_bot_session = decrypt_session_string(mongo_store.get_setting("bot_session_string"))
bot = TelegramClient(StringSession(stored_bot_session) if stored_bot_session else session_name, API_ID, API_HASH)
bot.parse_mode = 'html'

active_orders = {}      
waiting_proof = {}      
deposit_input = {} 
admin_dep_state = {}    
admin_content_state = {}
admin_user_state = {}
user_spam_cooldown = {} 
session_buy_state = {}  
account_product_state = {}
custom_dep_amt = {}     
pending_utr = {}        
broadcast_drafts = {}
broadcast_jobs = {}
purchase_attempts = {}

user_locks = {}

def get_user_lock(uid):
    if uid not in user_locks:
        user_locks[uid] = asyncio.Lock()
    return user_locks[uid]

def get_inventory_session_string(document):
    return decrypt_session_string(document.get("session_string"))

def export_session_string(client):
    """Export an authenticated Telethon session without exposing its secret."""
    source = client.session
    exported = StringSession()
    exported.set_dc(source.dc_id, source.server_address, source.port)
    exported.auth_key = source.auth_key
    return exported.save() or None

def save_inventory_session(document, client):
    session_string = export_session_string(client)
    if not session_string:
        return
    mongo_store.save_inventory({
        **document,
        "session_string": encrypt_session_string(session_string),
        "session_storage": "encrypted_string_session",
        "session_migrated_at": datetime.now(timezone.utc).isoformat(),
    })

async def migrate_inventory_sessions():
    """Copy legacy local sessions into Mongo without changing the local files."""
    migrated = 0
    skipped = 0
    for document in mongo_store.list_inventory({}):
        if get_inventory_session_string(document):
            continue

        session_file = document.get("session_file")
        if not session_file:
            skipped += 1
            continue
        session_path = str(session_file)
        if not os.path.exists(session_path) and not os.path.exists(f"{session_path}.session"):
            skipped += 1
            logger.warning(
                "Inventory session unavailable for phone=%s; re-login required",
                document.get("phone"),
            )
            continue

        client = None
        try:
            client = get_inventory_client(document)
            await client.connect()
            if not await client.is_user_authorized():
                skipped += 1
                logger.warning(
                    "Inventory session is not authorized for phone=%s",
                    document.get("phone"),
                )
                continue
            save_inventory_session(document, client)
            migrated += 1
        except Exception:
            skipped += 1
            logger.exception(
                "Could not migrate inventory session for phone=%s",
                document.get("phone"),
            )
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.exception(
                        "Could not disconnect inventory migration client for phone=%s",
                        document.get("phone"),
                    )

def get_inventory_client(document):
    session_string = get_inventory_session_string(document)
    if session_string:
        return TelegramClient(StringSession(session_string), API_ID, API_HASH)
    return TelegramClient(
        document.get("session_file", "").removesuffix(".session"),
        API_ID,
        API_HASH,
    )

def persist_bot_session():
    """Persist the bot's authenticated session while retaining the local fallback."""
    session_string = export_session_string(bot)
    if session_string:
        mongo_store.set_setting(
            "bot_session_string",
            encrypt_session_string(session_string),
        )

# ================= HELPER FUNCTIONS =================
def is_bot_online():
    value = get_setting("bot_status")
    return value == 'on' if value is not None else True

def is_maintenance_mode():
    return get_setting("maintenance_enabled", "off") == "on"

def get_maintenance_message():
    return get_setting(
        "maintenance_message",
        "🛠 <b>Maintenance Mode</b>\n\nPlease try again later."
    )

def is_admin(uid):
    if uid in ADMIN_IDS:
        return True
    return mongo_store.is_admin(uid)

def has_perm(uid, perm):
    if uid in ADMIN_IDS:
        return True
    return mongo_store.has_permission(uid, perm)

def ensure_user(uid):
    mongo_store.ensure_user(uid)

def get_usdt_rate():
    value = get_setting("usdt_rate")
    try: return float(value) if value is not None else float(DEFAULT_USDT_RATE)
    except: return float(DEFAULT_USDT_RATE)

def get_auto_cancel_seconds():
    try:
        value = int(get_setting("auto_cancel_seconds", str(AUTO_CANCEL_SECONDS)))
        return value if value >= 1 else AUTO_CANCEL_SECONDS
    except (TypeError, ValueError):
        return AUTO_CANCEL_SECONDS

def get_terms_url():
    return fix_url(get_setting("terms_url", TERMS_URL))

def get_support_url():
    value = get_setting("support_url")
    url = value if value else DEFAULT_SUPPORT_URL
    return fix_url(url)

def get_owner_url():
    value = get_setting(OWNER_USERNAME_SETTING, SUPPORT_USERNAME_2)
    return fix_url(value)

PAYMENT_METHOD_SETTINGS = {
    "automatic": "payment_method_automatic_enabled",
    "manual": "payment_method_manual_enabled",
}

def is_payment_method_enabled(method):
    return str(get_setting(PAYMENT_METHOD_SETTINGS[method], "on")).strip().lower() not in {
        "0", "false", "off", "no"
    }

def get_setting(key, default=None):
    return mongo_store.get_setting(key, default)

def set_setting(key, value):
    mongo_store.set_setting(key, value)

def delete_setting(key):
    mongo_store.delete_setting(key)

async def send_broadcast_content(recipient_id, draft):
    message = draft["message"]
    kwargs = {"parse_mode": "html"}
    if message.media:
        kwargs["file"] = message.media
        return await bot.send_message(recipient_id, draft["caption"], buttons=draft["buttons"], **kwargs)
    return await bot.send_message(recipient_id, draft["text"], buttons=draft["buttons"], **kwargs)

async def send_broadcast_preview(admin_id, draft):
    message = draft["message"]
    buttons = [
        [Button.inline("✅ Confirm Send", f"adm_bcast_confirm|{admin_id}"), Button.inline("❌ Cancel", f"adm_bcast_cancel|{admin_id}")]
    ]
    if message.media:
        return await bot.send_message(admin_id, draft["caption"], file=message.media, buttons=buttons, parse_mode="html")
    return await bot.send_message(admin_id, draft["text"], buttons=buttons, parse_mode="html")

async def run_broadcast(admin_id, chat_id, draft):
    users = [(user_id,) for user_id in mongo_store.list_user_ids()]
    total = len(users)
    sent = failed = blocked = 0
    progress_message = await bot.send_message(
        chat_id,
        f"{P_TG} <b>Broadcast in progress...</b>\n\n👥 Total: {total}\n✅ Sent: 0\n❌ Failed: 0\n🚫 Blocked/Deactivated: 0\n⏳ Remaining: {total}",
        buttons=[[Button.inline("🛑 Cancel Broadcast", f"adm_bcast_cancel|{admin_id}")]]
    )
    job = {"cancelled": False, "progress_message": progress_message}
    broadcast_jobs[admin_id] = job
    last_update = 0.0
    try:
        for index, (user_id,) in enumerate(users, start=1):
            if job["cancelled"]:
                break
            try:
                await send_broadcast_content(int(user_id), draft)
                sent += 1
            except FloodWaitError as error:
                await asyncio.sleep(error.seconds)
                try:
                    await send_broadcast_content(int(user_id), draft)
                    sent += 1
                except Exception as retry_error:
                    failed += 1
                    if retry_error.__class__.__name__ in {"UserBlockedError", "PeerIdInvalidError", "ChatWriteForbiddenError"}:
                        blocked += 1
            except Exception as error:
                failed += 1
                if error.__class__.__name__ in {"UserBlockedError", "PeerIdInvalidError", "ChatWriteForbiddenError"}:
                    blocked += 1

            now = time.monotonic()
            if now - last_update >= 2 or index == total:
                last_update = now
                try:
                    await progress_message.edit(
                        f"{P_TG} <b>Broadcast in progress...</b>\n\n👥 Total: {total}\n✅ Sent: {sent}\n❌ Failed: {failed}\n🚫 Blocked/Deactivated: {blocked}\n⏳ Remaining: {total - index}",
                        buttons=[[Button.inline("🛑 Cancel Broadcast", f"adm_bcast_cancel|{admin_id}")]]
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.1)
    finally:
        broadcast_jobs.pop(admin_id, None)
        broadcast_drafts.pop(admin_id, None)
        cancelled = job["cancelled"]
        title = "Broadcast Cancelled" if cancelled else "Broadcast Complete"
        await bot.send_message(
            chat_id,
            f"{P_TG} <b>{title}</b>\n\n👥 Total: {total}\n✅ Sent: {sent}\n❌ Failed: {failed}\n🚫 Blocked/Deactivated: {blocked}",
            buttons=[[Button.inline("◀️ Back", "adm_adminmain")]]
        )

def get_default_welcome(uid, pct, bot_username):
    ref_line = (
        f"{P_GLOBE} <code>https://t.me/{bot_username}?start=ref_{uid}</code>"
        if bot_username else
        f"{P_GLOBE} <i>Set a public bot username to enable referral links.</i>"
    )
    return (f"{PE_HEART} <b>Welcome to Fresh Tg Store!</b>\n\n"
            f"{PE_GIFT} <b>Premium services:</b> Buy accounts, sessions, and top up instantly.\n"
            f"{P_GIFT} <b>Refer & Earn:</b>\nInvite friends and earn {pct}% of their deposits!\n"
            f"{ref_line}\n\n"
            f"👨‍💻 <b>Developers:</b>\n@{SUPPORT_USERNAME_1} & @{SUPPORT_USERNAME_2}")

def get_welcome_message(uid, pct, bot_username):
    saved = get_setting("welcome_message")
    return saved if saved is not None else get_default_welcome(uid, pct, bot_username)

def get_banner_media():
    raw = get_setting("banner_photo")
    return get_banner_reference(raw)

def get_banner_reference(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return InputPhoto(
            id=int(data["id"]),
            access_hash=int(data["access_hash"]),
            file_reference=bytes.fromhex(data["file_reference"])
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

BUY_ACCOUNT_BANNER_SETTING = "buy_account_banner_file_id"
MY_STATS_BANNER_SETTING = "my_stats_banner_file_id"
MY_PROFILE_BANNER_SETTING = "my_profile_banner_file_id"
DEPOSIT_BANNER_SETTING = "deposit_banner_file_id"

def get_buy_account_banner():
    return get_banner_reference(get_setting(BUY_ACCOUNT_BANNER_SETTING))

async def send_bannered_message(event, setting, message, buttons=None):
    banner = get_banner_reference(get_setting(setting))
    if not banner or len(message) > 1024:
        return False
    try:
        await bot.send_file(event.chat_id, banner, caption=message, buttons=buttons)
        if isinstance(event, events.CallbackQuery.Event):
            try:
                await event.delete()
            except Exception:
                pass
        return True
    except Exception:
        return False

STORE_DEFAULT_MESSAGES = {
    "single": (
        "🛒 <b>ACCOUNT STORE</b>\n━━━━━━━━━━━━━━━━━━\n"
        "⚡ Select an account below to view details.\n\n"
        "💱 Rate: 1 USDT = ₹{rate}\n📦 Available: {available} accounts\n\n"
        "━━━━━━━━━━━━━━━━━━\n{products}{page}"
    ),
    "bulk": (
        "🔐 <b>SESSIONS STORE</b>\n━━━━━━━━━━━━━━━━━━\n"
        "⚡ Select a session package below.\n\n"
        "💱 Rate: 1 USDT = ₹{rate}\n📦 Available: {available} sessions\n\n"
        "━━━━━━━━━━━━━━━━━━\n{products}{page}"
    )
}

STORE_DEFAULT_BUTTONS = {
    "single": {"product": "{icon} {country}", "buy": "🛒 Buy Now", "back": "⬅️ Back to Accounts", "previous": "⬅️ Previous", "next": "Next ➡️", "cancel": "❌ Cancel", "page": "Page {page}/{total_pages}"},
    "bulk": {"product": "{icon} {country}", "quantity": "Quantity", "confirm": "✅ Confirm", "change_quantity": "✏️ Change Quantity", "back": "⬅️ Back to Sessions", "previous": "⬅️ Previous", "next": "Next ➡️", "cancel": "❌ Cancel", "page": "Page {page}/{total_pages}"}
}

def get_store_message(flow):
    return get_setting(f"{'account' if flow == 'single' else 'sessions'}_store_message", STORE_DEFAULT_MESSAGES[flow])

def get_store_buttons(flow):
    key = "account" if flow == "single" else "sessions"
    raw = get_setting(f"{key}_button_labels")
    try:
        labels = json.loads(raw) if raw else {}
        return {**STORE_DEFAULT_BUTTONS[flow], **labels}
    except (TypeError, ValueError, json.JSONDecodeError):
        return STORE_DEFAULT_BUTTONS[flow].copy()

def store_banner_key(flow):
    return "account_store_banner" if flow == "single" else "sessions_store_banner"

def to_usd(inr):
    return round(inr / get_usdt_rate(), 2)


def normalize_optional_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "/skip", "skip"}:
        return ""
    return text


def parse_inr_price(value):
    text = normalize_optional_text(value)
    if not text:
        return None
    cleaned = text.replace("₹", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return int(round(parsed))


def is_user_banned(uid):
    user = mongo_store.get_user(uid, {"banned": 1})
    return bool(user and user.get("banned") == 1)

def get_user_discount(uid):
    user = mongo_store.get_user(uid, {"discount": 1})
    return int(user.get("discount", 0) or 0) if user else 0

def get_user_terms_accepted(uid):
    user = mongo_store.get_user(uid, {"terms_accepted": 1})
    return int(user.get("terms_accepted", 0) or 0) if user else 0

def get_user_balance(uid):
    return mongo_store.get_balance(uid)

def update_balance(uid, amount, event_key, event_type="balance_adjustment", extra_inc=None):
    return mongo_store.apply_balance_event(
        uid,
        amount,
        event_key,
        event_type,
        extra_inc=extra_inc,
    )

def rollback_single_purchase(uid, phone, amount, purchase_id, *, remove_inventory):
    """Refund a charged single purchase before making its stock reusable/removing it."""
    refund = update_balance(
        uid,
        amount,
        f"purchase:single:{purchase_id}:refund",
        "purchase_refund",
    )
    if not refund.get("applied") and not refund.get("already_applied"):
        raise RuntimeError(f"Could not refund single purchase for {phone}")
    if remove_inventory:
        mongo_store.delete_inventory(phone)
    else:
        mongo_store.release_inventory([phone])

def delete_session_files(session_path):
    base = session_path if not session_path.endswith('.session') else session_path[:-8]
    for ext in ['.session', '.session-wal', '.session-shm', '.session-journal']:
        try:
            if os.path.exists(base + ext): os.remove(base + ext)
        except: pass

async def check_channel_joined(uid):
    if is_admin(uid): return True
    for ch in REQUIRED_CHANNELS:
        try:
            ch_id = int(ch.strip()) if str(ch).strip().lstrip('-').isdigit() else ch.strip()
            try:
                await bot(GetParticipantRequest(channel=ch_id, participant=uid))
            except ValueError:
                entity = await bot.get_entity(ch_id)
                await bot(GetParticipantRequest(channel=entity, participant=uid))
        except UserNotParticipantError:
            return False
        except ChatAdminRequiredError:
            logger.error(f"Bot is not admin in channel: {ch}")
            return False
        except Exception as e:
            logger.error(f"Channel Check Error for {ch}: {e}")
            return False
    return True

COUNTRY_CODES = {
    '1': ('USA/Canada', '🇺🇸'), '7': ('Russia', '🇷🇺'), '20': ('Egypt', '🇪🇬'),
    '27': ('South Africa', '🇿🇦'), '31': ('Netherlands', '🇳🇱'), '32': ('Belgium', '🇧🇪'),
    '33': ('France', '🇫🇷'), '34': ('Spain', '🇪🇸'), '39': ('Italy', '🇮🇹'), 
    '44': ('UK', '🇬🇧'), '46': ('Sweden', '🇸🇪'), '48': ('Poland', '🇵🇱'),
    '49': ('Germany', '🇩🇪'), '51': ('Peru', '🇵🇪'), '52': ('Mexico', '🇲🇽'),
    '54': ('Argentina', '🇦🇷'), '55': ('Brazil', '🇧🇷'), '56': ('Chile', '🇨🇱'),
    '57': ('Colombia', '🇨🇴'), '58': ('Venezuela', '🇻🇪'), '60': ('Malaysia', '🇲🇾'),
    '61': ('Australia', '🇦🇺'), '62': ('Indonesia', '🇮🇩'), '63': ('Philippines', '🇵🇭'), 
    '66': ('Thailand', '🇹🇭'), '84': ('Vietnam', '🇻🇳'), '86': ('China', '🇨🇳'), 
    '90': ('Turkey', '🇹🇷'), '91': ('India', '🇮🇳'), '92': ('Pakistan', '🇵🇰'), 
    '93': ('Afghanistan', '🇦🇫'), '94': ('Sri Lanka', '🇱🇰'), '95': ('Myanmar', '🇲🇲'),
    '98': ('Iran', '🇮🇷'), '212': ('Morocco', '🇲🇦'), '213': ('Algeria', '🇩🇿'),
    '234': ('Nigeria', '🇳🇬'), '254': ('Kenya', '🇰🇪'), '255': ('Tanzania', '🇹🇿'),
    '380': ('Ukraine', '🇺🇦'), '880': ('Bangladesh', '🇧🇩'), '964': ('Iraq', '🇮🇶'),
    '966': ('Saudi Arabia', '🇸🇦'), '971': ('UAE', '🇦🇪'), '998': ('Uzbekistan', '🇺🇿')
}

def get_flag_by_country_name(name):
    for code, (c_name, c_flag) in COUNTRY_CODES.items():
        if c_name == name: return c_flag
    try:
        document = mongo_store.custom_country_by_name(name)
        if document: return document.get("flag")
    except: pass
    return "🌍"

def get_country_info(phone):
    phone = str(phone).replace(' ', '').replace('+', '')
    if not phone: return "Unknown", "🌍"
    
    try:
        customs = mongo_store.list_custom_countries()
        customs.sort(key=lambda document: len(str(document.get("code", ""))), reverse=True)
        for document in customs:
            code = str(document.get("code", ""))
            if phone.startswith(code):
                return document.get("name"), document.get("flag")
    except: pass

    for length in (3, 2, 1):
        prefix = phone[:length]
        if prefix in COUNTRY_CODES: return COUNTRY_CODES[prefix]
    return "Unknown", "🌍"

async def detect_account_year(client):
    year = 2024
    try:
        try: await client.delete_dialog('TGDNAbot')
        except: pass
        await client.send_message('TGDNAbot', '/start')
        me = await client.get_me()
        await asyncio.sleep(1)
        await client.send_message('TGDNAbot', str(me.id)) 
        for _ in range(8):
            await asyncio.sleep(1.5)
            msgs = await client.get_messages('TGDNAbot', limit=3)
            for m in msgs:
                if m.text and ('Created:' in m.text or 'Age:' in m.text or 'Registration' in m.text):
                    match = re.search(r'(?:Created|Age|Registration)[^\d]*(\d{4})', m.text, re.IGNORECASE)
                    if match: return int(match.group(1))
    except Exception: pass
    return year

# ================= LOGGING LOGIC =================
async def process_referral_bonus(uid, amount, event_key):
    user = mongo_store.get_user(uid, {"referred_by": 1})
    ref = user.get("referred_by") if user else None
    if ref:
        pct_value = get_setting("ref_percent")
        pct = float(pct_value) if pct_value is not None else 3.0
        if pct > 0:
            bonus = int(amount * (pct / 100))
            if bonus > 0:
                result = mongo_store.credit_balance(
                    ref,
                    bonus,
                    f"{event_key}:referral",
                    "referral_reward",
                )
                if not result.get("applied"):
                    return False
                try:
                    await bot.send_message(
                        ref,
                        f"{PE_GIFT} <b>Referral Bonus Unlocked!</b>\n"
                        f"{PE_HEART} Your referral <code>{uid}</code> deposited {P_INR}{amount}.\n"
                        f"{PE_CHECK} You earned <b>{P_INR}{bonus}</b>!"
                    )
                except:
                    pass
                return True
    return False

async def log_primary_deposit(uid, amt, method):
    try:
        try:
            user = await bot.get_entity(int(uid))
            username = html.escape(user.username) if user.username else "NoUsername"
        except:
            username = "NoUsername"
        t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = (f"{PE_GIFT} <b>NEW DEPOSIT SUCCESSFUL</b>\n\n"
               f"{P_ACC} Uꜱᴇʀ ID: <code>{uid}</code>\n"
               f"👤 Uꜱᴇʀɴᴀᴍᴇ: @{username}\n"
               f"{P_MONEY} Aᴍᴏᴜɴᴛ: {P_INR}{amt}\n"
               f"{P_CARD} Mᴇᴛʜᴏᴅ: {method}\n"
               f"{P_TIME} Tɪᴍᴇ: {t}\n\n"
               f"<i>{PE_HEART} Thanks for depositing in Fresh Tg!</i>")
        try: await bot.send_message(LOG_CHANNEL_ID, msg)
        except Exception as e: logger.error(f"Failed Log: {e}")
    except Exception as e: logger.error(f"Global Dep Log Err: {e}")

async def log_primary_purchase(uid, country, price, amount, year, qty):
    try:
        t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = (f"{PE_LIGHTNING} <b>NEW PURCHASE SUCCESSFUL</b>\n\n"
               f"{P_ID} Uꜱᴇʀ Iᴅ: <code>{uid}</code>\n"
               f"{P_GLOBE} Cᴏᴜɴᴛʀʏ: {country}\n"
               f"{P_MONEY} Pʀɪᴄᴇ: {P_INR}{price}\n"
               f"{P_CARD} Tᴏᴛᴀʟ Pᴀɪᴅ: {P_INR}{amount}\n"
               f"{P_CAL} Yᴇᴀʀ: {year}\n"
               f"{P_PKG} Qᴜᴀɴᴛɪᴛʏ: {qty}\n"
               f"{P_TIME} Tɪᴍᴇ: {t}")
        try: await bot.send_message(LOG_CHANNEL_ID, msg)
        except: pass
    except Exception as e: logger.error(f"Pur Log Err: {e}")

# ================= MENU HELPERS =================
def get_persistent_menu(uid):
    rows = [
        [KeyboardButton("🛒 Buy Account"), KeyboardButton("👤 My Profile")],
        [KeyboardButton("📁 Buy Sessions")],
        [KeyboardButton("💰 Deposit"), KeyboardButton("📊 My Stats")],
        [KeyboardButton("📞 Support")]
    ]
    if is_admin(uid): rows.append([KeyboardButton("🔐 Admin Panel")])
    return ReplyKeyboardMarkup([KeyboardButtonRow(r) for r in rows], resize=True)

def get_terms_buttons():
    return [
        [Button.url("📜 Read Terms & Conditions", get_terms_url())],
        [Button.inline("✅ Accept", "tc_accept"), Button.inline("❌ Reject", "tc_reject")]
    ]

def get_support_buttons():
    channel_url = fix_url(JOIN_URLS[0]) if JOIN_URLS and JOIN_URLS[0] else DEFAULT_SUPPORT_URL
    buttons = [
        [Button.url("🆘 Support", get_support_url()), Button.url("👑 Owner", get_owner_url())],
        [Button.url("📢 Channel", channel_url)],
        [Button.url("📜 Terms & Conditions", get_terms_url())],
    ]
    return buttons

def get_join_buttons():
    buttons = []
    for i, link in enumerate(JOIN_URLS):
        if link:
            try:
                buttons.append([Button.url(f"📢 Join Channel {i+1}", fix_url(link))])
            except Exception:
                pass
    buttons.append([Button.inline("✅ I've Joined – Verify", "verify_join")])
    return buttons

async def send_main_menu(event, uid):
    me = await bot.get_me()
    pct = get_setting("ref_percent", "3")
    bot_username = me.username or ""
    msg = get_welcome_message(uid, pct, bot_username)
    banner = get_banner_media() if get_setting("images_enabled", "off") == "on" else None
    menu = get_persistent_menu(uid)
    
    if isinstance(event, events.CallbackQuery.Event):
        try: await event.delete()
        except: pass
        if banner:
            try:
                await bot.send_file(uid, banner, caption=msg, buttons=menu)
                return
            except Exception:
                pass
        await bot.send_message(uid, msg, buttons=menu)
    else:
        if banner:
            try:
                await bot.send_file(uid, banner, caption=msg, buttons=menu)
                return
            except Exception:
                pass
        await event.respond(msg, buttons=menu)

# ================= DEPOSIT HANDLERS =================
def format_payment_buttons(buttons):
    n = len(buttons)
    res = []
    for i in range(0, n, 2): res.append(buttons[i:i+2])
    return res

async def deposit_menu(event):
    msg = (f"{P_CARD} <b>Select Payment Method:</b>\n\n"
           f"{PE_LIGHTNING} Choose Automatic for instant credit.\n"
           f"{P_WAIT} Choose Manual for other methods.")
    
    flat_buttons = []
    if is_payment_method_enabled("manual"):
        flat_buttons.append(Button.inline("🏦 Manual Payment", "dep_upi"))
    if is_payment_method_enabled("automatic"):
        flat_buttons.append(Button.inline("⚡ Automatic Payment", "automatic_payment"))
    
    customs = mongo_store.list_custom_payments()
    for payment in customs:
        if str(payment.get("name", "")).strip().casefold() == "cwallet":
            continue
        flat_buttons.append(Button.inline(f"💳 {payment['name']}", f"depm_{payment['name']}"))
    if not flat_buttons:
        unavailable = "⚠️ No payment methods are currently available.\n\nPlease contact admin."
        if isinstance(event, events.CallbackQuery.Event):
            return await event.edit(unavailable)
        return await event.respond(unavailable)
    btns = format_payment_buttons(flat_buttons)
    btns.append([Button.inline("❌ Cancel", "cancel_action")])
    if await send_bannered_message(event, DEPOSIT_BANNER_SETTING, msg, btns):
        return
    await bot.send_message(event.chat_id, msg, buttons=btns)

def get_automatic_payment_purpose():
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"{FAMAPP_PURPOSE_PREFIX}{datetime.now(timezone.utc):%Y%m%d}{suffix}"

def build_automatic_upi_uri(amount, purpose):
    if not FAMAPP_UPI_ID:
        raise ValueError("FAMAPP_UPI_ID is not configured")
    return "upi://pay?" + urlencode({
        "pa": FAMAPP_UPI_ID,
        "pn": FAMAPP_PAYEE_NAME,
        "am": f"{int(amount)}",
        "cu": "INR",
        "tn": purpose,
    })

def generate_automatic_qr(amount, purpose):
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(build_automatic_upi_uri(amount, purpose))
    qr.make(fit=True)
    output = BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(output, format="PNG", optimize=True)
    output.name = "automatic-payment.png"
    output.seek(0)
    return output

def automatic_payment_message(amount, order_id):
    return (
        "🏦 <b>AUTOMATIC PAYMENT (UPI)</b>\n\n"
        f"{P_MONEY} Amount: <code>₹{amount}</code>\n"
        f"{P_ID} Order ID:\n<code>{order_id}</code>\n\n"
        "👇 Scan the QR below.\n"
        "Click ✅ Check Payment after paying."
    )

def parse_famapp_email(raw_message):
    try:
        message = email.message_from_bytes(raw_message, policy=policy.default)
        parts = []
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                if part.get_content_type() in {"text/plain", "text/html"}:
                    parts.append(str(part.get_content()))
        else:
            parts.append(str(message.get_content()))
        text = "\n".join(parts)
        prefix = re.escape(FAMAPP_PURPOSE_PREFIX)
        purpose_match = re.search(rf"(?i)Purpose\s*:\s*({prefix}[0-9]{{8}}[A-Z0-9]{{6}})", text)
        amount_match = re.search(r"₹\s*([0-9][0-9,]*(?:\.\d+)?)", text)
        return {
            "sender": str(message.get("From", "")),
            "subject": str(message.get("Subject", "")),
            "body": text,
            "purpose": purpose_match.group(1) if purpose_match else None,
            "amount": float(amount_match.group(1).replace(",", "")) if amount_match else None,
            "date": message.get("Date"),
        }
    except Exception:
        return None

def verify_automatic_payment(order):
    if not IMAP_USERNAME or not IMAP_APP_PASSWORD:
        logger.error("Automatic payment verification is not configured")
        return "error"
    if not order.get("amount_inr") or not order.get("payment_purpose"):
        logger.error("Automatic payment order is missing amount or payment purpose")
        return "error"
    expiry = datetime.fromisoformat(str(order["expires_at"]))
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expiry:
        return "expired"
    client = None
    try:
        client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        status, _ = client.login(IMAP_USERNAME, IMAP_APP_PASSWORD)
        if status != "OK":
            return "error"
        status, _ = client.select(IMAP_MAILBOX)
        if status != "OK":
            return "error"
        since = (datetime.now(timezone.utc) - timedelta(hours=IMAP_LOOKBACK_HOURS)).strftime("%d-%b-%Y")
        search_terms = ["FROM", IMAP_SENDER_FILTER, "SINCE", since] if IMAP_SENDER_FILTER else ["SINCE", since]
        status, data = client.search(None, *search_terms)
        if status != "OK" or not data or not data[0]:
            return "pending"
        for message_id in data[0].split():
            fetch_status, fetched = client.fetch(message_id, "(RFC822)")
            if fetch_status != "OK":
                continue
            for part in fetched:
                if not isinstance(part, tuple) or len(part) < 2:
                    continue
                parsed = parse_famapp_email(part[1])
                if not parsed:
                    continue
                received = (parsed["subject"] + "\n" + parsed["body"]).lower()
                sender_match = not IMAP_SENDER_FILTER or IMAP_SENDER_FILTER.lower() in parsed["sender"].lower()
                outgoing_match = "your payment" in received or "successfully paid" in received
                received_match = (
                    re.search(r"you\s+received\s+₹\s*[0-9]", received) is not None
                    or "you have successfully received" in received
                )
                if (sender_match and not outgoing_match and received_match
                        and parsed["amount"] == float(order["amount_inr"])
                        and parsed["purpose"] == order["payment_purpose"]):
                    return "success"
        return "pending"
    except Exception:
        logger.exception("Automatic payment IMAP verification failed")
        return "error"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

async def automatic_payment_init(event):
    deposit_input[event.sender_id] = {"step": "automatic_keypad", "val": "0"}
    await event.edit(
        f"{P_KEY} <b>ENTER AMOUNT IN INR</b> (Min ₹1)\n\n{P_MONEY} <code>₹0</code>",
        buttons=get_keypad(),
    )

def get_keypad():
    return [
        [Button.inline("1", "kp_1"), Button.inline("2", "kp_2"), Button.inline("3", "kp_3")],
        [Button.inline("4", "kp_4"), Button.inline("5", "kp_5"), Button.inline("6", "kp_6")],
        [Button.inline("7", "kp_7"), Button.inline("8", "kp_8"), Button.inline("9", "kp_9")],
        [Button.inline("🔙 Del", "kp_del"), Button.inline("0", "kp_0"), Button.inline("✅ Confirm", "kp_done")],
        [Button.inline("❌ Cancel", "cancel_action")]
    ]

def get_admin_custom_keypad(dep_id):
    return [
        [Button.inline("1", f"dkp|{dep_id}|1"), Button.inline("2", f"dkp|{dep_id}|2"), Button.inline("3", f"dkp|{dep_id}|3")],
        [Button.inline("4", f"dkp|{dep_id}|4"), Button.inline("5", f"dkp|{dep_id}|5"), Button.inline("6", f"dkp|{dep_id}|6")],
        [Button.inline("7", f"dkp|{dep_id}|7"), Button.inline("8", f"dkp|{dep_id}|8"), Button.inline("9", f"dkp|{dep_id}|9")],
        [Button.inline("🔙 Del", f"dkp|{dep_id}|del"), Button.inline("0", f"dkp|{dep_id}|0"), Button.inline("✅ Confirm", f"dkp|{dep_id}|conf")],
        [Button.inline("❌ Cancel", f"dkp|{dep_id}|cancel")]
    ]

async def manual_deposit_init(event, method):
    uid = event.sender_id
    deposit_input[uid] = {'step': 'wait_amt', 'method': method}
    await event.edit(
        f"{P_CARD} <b>{method} Deposit</b>\n\n"
        f"👇 Reply to this message with the <b>AMOUNT</b> in ₹ (INR) you want to deposit.",
        buttons=[[Button.inline("❌ Cancel", "cancel_action")]]
    )

# ========== UPI FUNCTIONS WITH SCREENSHOT ==========

async def init_upi_keypad(event):
    """Start UPI payment with QR"""
    uid = event.sender_id
    deposit_input[uid] = {'step': 'upi_keypad', 'val': '0'}
    
    caption = (f"{PE_LIGHTNING} <b>UPI PAYMENT</b>\n\n"
               f"👇 <b>Scan QR to pay</b>\n"
               f"💳 <b>UPI ID:</b> <code>{UPI_ID}</code>\n\n"
               f"💰 <b>Enter the AMOUNT</b> in INR using the keypad below.\n"
               f"<i>(Min: ₹1)</i>")
    
    await event.delete()
    
    try:
        # Generate QR
        upi_url = f"upi://pay?pa={UPI_ID}&pn=FreshTgStore&am=1&cu=INR"
        encoded_upi = quote(upi_url)
        qr_url = f"https://quickchart.io/qr?text={encoded_upi}&size=400"
        
        try:
            await bot.send_file(uid, qr_url, caption=caption, buttons=get_keypad())
        except Exception as e:
            logger.error(f"QR Init Error: {e}")
            await bot.send_message(uid, caption, buttons=get_keypad())
            
    except Exception as e:
        logger.error(f"init_upi_keypad Error: {e}")
        await bot.send_message(uid, caption, buttons=get_keypad())

async def keypad_logic(event):
    """Handle UPI keypad input"""
    uid = event.sender_id
    action = event.data.decode().replace("kp_", "")
    keypad_state = deposit_input.get(uid, {})
    keypad_step = keypad_state.get("step", "upi_keypad")
    curr = keypad_state.get('val', "0")

    if action.isdigit():
        if curr == "0": 
            curr = action
        else: 
            curr += action
        if len(curr) > 5: 
            curr = curr[:5]
    elif action == "del": 
        curr = curr[:-1] or "0"
    elif action == "done":
        try:
            amt = int(curr)
            if amt < 1: 
                return await event.answer("⚠️ Minimum Deposit is ₹1", alert=True)
            if keypad_step == "automatic_keypad":
                if not is_payment_method_enabled("automatic"):
                    return await event.answer("⚠️ Automatic Payment is currently unavailable.", alert=True)
                return await show_automatic_payment_qr(event, amt)
            if not is_payment_method_enabled("manual"):
                return await event.answer("⚠️ Manual Payment is currently unavailable.", alert=True)
            return await show_upi_qr(event, amt)
        except ValueError:
            return await event.answer("⚠️ Invalid amount", alert=True)
    
    deposit_input[uid] = {'step': keypad_step, 'val': curr}
    
    try:
        upi_line = "" if keypad_step == "automatic_keypad" else f"💳 UPI ID: <code>{UPI_ID}</code>\n\n"
        await event.edit(f"{P_KEY} <b>ENTER AMOUNT IN INR</b>\n\n"
                        f"{upi_line}"
                        f"{P_MONEY} <code>₹{curr}</code>",
                        buttons=get_keypad())
    except MessageNotModifiedError:
        pass

async def show_upi_qr(event, amount):
    """Show UPI QR with amount and UTR + Screenshot option"""
    uid = event.sender_id
    order_id = f"ORDER_{uid}_{int(time.time())}"
    
    try:
        # Create UPI URL
        upi_url = f"upi://pay?pa={UPI_ID}&pn=FreshTgStore&am={amount}&cu=INR"
        encoded_upi = quote(upi_url)
        
        # Generate QR
        generated_qr = f"https://quickchart.io/qr?text={encoded_upi}&size=400"
        
        # Save order
        mongo_store.create_upi_order(order_id, uid, amount, "pending")
        
        msg = (f"{PE_LIGHTNING} <b>UPI PAYMENT</b>\n\n"
               f"{P_MONEY} Amount: <code>₹{amount}</code>\n"
               f"{P_ID} Order ID: <code>{order_id}</code>\n\n"
               f"👇 <b>Scan QR below or pay to:</b>\n<code>{UPI_ID}</code>\n\n"
               f"📸 <b>Please send a payment screenshot after making the transfer.</b>")
        
        await event.delete()
        
        # Store pending UPI order
        pending_utr[uid] = {
            'order_id': order_id,
            'amount': amount,
            'step': 'wait_utr'
        }
        
        # Send generated QR
        try:
            await bot.send_file(uid, generated_qr, caption=msg, force_document=False, mime_type="image/png", buttons=[
                [Button.inline("📸 Upload Payment Screenshot", "upload_payment_screenshot")],
                [Button.inline("❌ Cancel", "cancel_action")]
            ])
        except Exception as e:
            logger.error(f"QR Send Error: {e}")
            # Fallback: Send UPI ID only
            fallback_msg = (f"{PE_LIGHTNING} <b>UPI PAYMENT</b>\n\n"
                           f"{P_MONEY} Amount: <code>₹{amount}</code>\n"
                           f"{P_ID} Order ID: <code>{order_id}</code>\n\n"
                           f"👇 <b>Pay to this UPI ID:</b>\n<code>{UPI_ID}</code>\n\n"
                           f"📸 <b>Please send a payment screenshot after making the transfer.</b>")
            
            await bot.send_message(uid, fallback_msg, buttons=[
                [Button.inline("📸 Upload Payment Screenshot", "upload_payment_screenshot")],
                [Button.inline("❌ Cancel", "cancel_action")]
            ])
            
    except Exception as e:
        logger.error(f"show_upi_qr Error: {e}")
        # Ultimate fallback
        fallback_msg = (f"{PE_LIGHTNING} <b>UPI PAYMENT</b>\n\n"
                       f"{P_MONEY} Amount: <code>₹{amount}</code>\n"
                       f"{P_ID} Order ID: <code>{order_id}</code>\n\n"
                       f"👇 <b>Pay to this UPI ID:</b>\n<code>{UPI_ID}</code>\n\n"
                       f"📸 <b>Please send a payment screenshot after making the transfer.</b>")
        
        pending_utr[uid] = {
            'order_id': order_id,
            'amount': amount,
            'step': 'wait_utr'
        }
        
        await bot.send_message(uid, fallback_msg, buttons=[
            [Button.inline("📸 Upload Payment Screenshot", "upload_payment_screenshot")],
            [Button.inline("❌ Cancel", "cancel_action")]
        ])

async def show_automatic_payment_qr(event, amount):
    uid = event.sender_id
    await event.answer("⏳ Generating Payment QR....", alert=False)
    try:
        order_id = f"ORD-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(4).upper()}"
        while mongo_store.get_automatic_payment(order_id):
            order_id = f"ORD-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(4).upper()}"
        purpose = get_automatic_payment_purpose()
        upi_uri = build_automatic_upi_uri(amount, purpose)
        qr_file = generate_automatic_qr(amount, purpose)
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(minutes=AUTOMATIC_PAYMENT_EXPIRY_MINUTES)
        mongo_store.create_automatic_payment(
            order_id,
            uid,
            amount,
            purpose,
            upi_uri,
            created_at.isoformat(),
            expires_at.isoformat(),
        )
        payment_message = automatic_payment_message(amount, order_id)
        await event.delete()
        await bot.send_file(
            uid,
            qr_file,
            caption=payment_message,
            force_document=False,
            mime_type="image/png",
            buttons=[
                [Button.inline("✅ Check Payment", f"automatic_check|{order_id}")],
                [Button.inline("❌ Cancel", f"automatic_cancel|{order_id}")],
            ],
        )
    except Exception:
        logger.exception("Automatic payment QR generation failed uid=%s", uid)
        try:
            await event.edit("❌ Could not generate the payment QR. Please try again later.")
        except Exception:
            await bot.send_message(uid, "❌ Could not generate the payment QR. Please try again later.")

async def send_automatic_check_result(event, message, *, order_id=None):
    buttons = None
    if order_id:
        buttons = [
            [Button.inline("✅ Check Payment", f"automatic_check|{order_id}")],
            [Button.inline("❌ Cancel", f"automatic_cancel|{order_id}")],
        ]
    try:
        await event.edit(message, buttons=buttons)
        return True
    except Exception:
        logger.exception("Unable to edit automatic payment status order_id=%s", order_id)
    try:
        await event.answer(message, alert=True)
        return True
    except Exception:
        logger.exception("Unable to answer automatic payment status order_id=%s", order_id)
    try:
        await bot.send_message(event.chat_id, message, buttons=buttons)
        return True
    except Exception:
        logger.exception("Unable to send automatic payment status order_id=%s", order_id)
        return False

async def check_automatic_payment(event, order_id):
    uid = event.sender_id
    try:
        await event.answer("🔎 Checking payment...", alert=False)
    except Exception:
        logger.exception("Unable to acknowledge automatic payment check order_id=%s", order_id)
    await send_automatic_check_result(event, "⏳ Verifying Payment...", order_id=order_id)
    try:
        order = mongo_store.get_automatic_payment(order_id)
        if not order or int(order.get("user_id")) != int(uid):
            return await send_automatic_check_result(event, "❌ Payment order not found.")
        if order.get("status") == "verified":
            return await send_automatic_check_result(event, "✅ Payment already verified.")
        if order.get("status") == "cancelled":
            return await send_automatic_check_result(event, "❌ Payment session was cancelled.")
        try:
            expiry = datetime.fromisoformat(str(order["expires_at"]))
        except (KeyError, TypeError, ValueError):
            logger.exception("Invalid automatic payment expiry order_id=%s", order_id)
            return await send_automatic_check_result(
                event,
                "⚠️ Payment verification temporarily failed.\n\nPlease try Check Payment again in a moment.",
                order_id=order_id,
            )
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expiry:
            mongo_store.update_automatic_payment_status(order_id, "pending", "expired", "expired")
            return await send_automatic_check_result(event, "⌛ Payment session expired.\n\nPlease start a new Automatic Payment.")

        result = await asyncio.to_thread(verify_automatic_payment, order)
        if result == "pending":
            mongo_store.update_automatic_payment_status(order_id, "pending", "pending", "not_found")
            return await send_automatic_check_result(
                event,
                "❌ Payment not received yet.\n\nIf you have already paid, please contact admin.",
                order_id=order_id,
            )
        if result == "expired":
            mongo_store.update_automatic_payment_status(order_id, "pending", "expired", "expired")
            return await send_automatic_check_result(event, "⌛ Payment session expired.\n\nPlease start a new Automatic Payment.")
        if result != "success":
            mongo_store.update_automatic_payment_status(order_id, "pending", "pending", "verification_error")
            return await send_automatic_check_result(
                event,
                "⚠️ Payment verification temporarily failed.\n\nPlease try Check Payment again in a moment.",
                order_id=order_id,
            )

        amount = int(order["amount_inr"])
        previous_balance = mongo_store.get_balance(uid)
        credit = update_balance(
            uid,
            amount,
            f"automatic_payment:{order_id}",
            "automatic_payment_credit",
            extra_inc={"total_deposited": amount},
        )
        if not credit.get("applied") and not credit.get("already_applied"):
            raise RuntimeError("Automatic payment wallet credit was not applied")
        mongo_store.update_automatic_payment_status(
            order_id,
            "pending",
            "verified",
            "verified",
            datetime.now(timezone.utc).isoformat(),
        )
        if credit.get("already_applied"):
            mongo_store.update_automatic_payment_status(
                order_id,
                "pending",
                "verified",
                "verified",
                datetime.now(timezone.utc).isoformat(),
            )
            return await send_automatic_check_result(event, "✅ Payment already verified.")
        new_balance = credit.get("balance", previous_balance + amount)
        try:
            await event.delete()
        except Exception:
            logger.exception("Unable to delete automatic payment screen order_id=%s", order_id)
        await bot.send_message(uid, f"✅ Payment Successful! ₹{amount} added to your wallet.")
        await bot.send_message(
            uid,
            "✅ <b>Payment Auto-Verified!</b>\n\n"
            f"{P_MONEY} Amount Added: ${to_usd(amount):.2f} (₹{amount})\n"
            f"📉 Previous Balance: ${to_usd(previous_balance):.2f} (₹{previous_balance})\n"
            f"📈 New Balance: ${to_usd(new_balance):.2f} (₹{new_balance})",
        )
    except Exception:
        logger.exception("Automatic payment check failed uid=%s order_id=%s", uid, order_id)
        await send_automatic_check_result(
            event,
            "⚠️ Payment verification temporarily failed.\n\nPlease try Check Payment again in a moment.",
            order_id=order_id,
        )

async def cancel_automatic_payment(event, order_id):
    uid = event.sender_id
    await event.answer("Payment cancelled.", alert=False)
    try:
        order = mongo_store.get_automatic_payment(order_id)
        if not order or int(order.get("user_id")) != int(uid):
            return await event.answer("❌ Payment order not found.", alert=True)
        if order.get("status") == "verified":
            return await event.answer("✅ Payment already verified.", alert=True)
        mongo_store.update_automatic_payment_status(order_id, "pending", "cancelled", "cancelled")
        await event.delete()
    except Exception:
        logger.exception("Automatic payment cancellation failed uid=%s order_id=%s", uid, order_id)
        await event.answer("❌ Could not cancel this payment session.", alert=True)

async def submit_utr_handler(event, order_id):
    """Handle UTR submission with screenshot"""
    uid = event.sender_id
    
    # Check if order exists
    upi_order = mongo_store.get_upi_order(order_id)
    if not upi_order:
        return await event.answer("❌ Order not found.", alert=True)
    
    if upi_order.get("status") == 'success':
        return await event.answer("✅ Already credited!", alert=True)
    
    await event.delete()
    
    chat = event.chat_id
    
    async with bot.conversation(chat, timeout=180) as conv:
        try:
            # Step 1: Ask for UTR
            await conv.send_message(f"{P_UTR} <b>Step 1/2: Enter UTR Number</b>\n\n"
                                   f"Please enter the <b>12-Digit UTR</b> / Reference Number of your payment:\n\n"
                                   f"<i>Type /cancel to abort</i>")
            
            resp = await conv.get_response()
            utr_number = resp.text.strip()
            
            if utr_number.lower() == "/cancel":
                return await conv.send_message("❌ Cancelled.")
            
            if len(utr_number) < 8:
                return await conv.send_message("❌ Invalid UTR. Please try again with a valid 12-digit UTR.")
            
            # Step 2: Ask for Screenshot
            await conv.send_message(f"{P_SCREEN} <b>Step 2/2: Send Payment Screenshot</b>\n\n"
                                   f"Please send the <b>payment confirmation screenshot</b>.\n\n"
                                   f"<i>Make sure the UTR is visible in the screenshot.</i>\n\n"
                                   f"<i>Type /skip if you don't have screenshot</i>")
            
            # Wait for photo
            photo_msg = await conv.get_response()
            
            screenshot_path = None
            if photo_msg.photo:
                screenshot_path = f"screenshots/utr_{uid}_{int(time.time())}.jpg"
                os.makedirs("screenshots", exist_ok=True)
                await bot.download_media(photo_msg, screenshot_path)
                await conv.send_message("✅ Screenshot received! Thank you.")
            elif photo_msg.text and photo_msg.text.lower() == "/skip":
                await conv.send_message("⚠️ Screenshot skipped (not recommended)")
            else:
                await conv.send_message("⚠️ No screenshot received. Continuing without it.")
            
            # Save deposit
            amount = int(upi_order["amount"])
            method = f"UPI (UTR: {utr_number})"
            
            deposit = mongo_store.create_deposit(
                uid,
                amount,
                method,
                status="pending",
                screenshot=screenshot_path,
                utr=utr_number,
            )
            dep_id = deposit["_id"]
            
            # Notify admin with UTR and screenshot
            cap = (f"{PE_LIGHTNING} <b>NEW UPI DEPOSIT (Needs Approval)</b>\n"
                   f"{P_ACC} User: <code>{uid}</code>\n"
                   f"{P_MONEY} Amount: <b>₹{amount}</b>\n"
                   f"{P_UTR} UTR Submitted: <code>{utr_number}</code>\n")
            
            if screenshot_path:
                cap += f"{P_SCREEN} Screenshot: ✅ Received\n"
            else:
                cap += f"{P_SCREEN} Screenshot: ❌ Not Provided\n"
            
            cap += f"\nPlease verify this UTR in your app."
            
            btns = [
                [Button.inline(f"✅ Accept (₹{amount})", f"dep_acc|{dep_id}|{uid}|UPI|exact|{amount}"), 
                 Button.inline("❌ Reject", f"dep_rej|{dep_id}|{uid}")]
            ]
            
            # Send to admin with screenshot
            try:
                if screenshot_path and os.path.exists(screenshot_path):
                    await bot.send_file(LOG_CHANNEL_ID, screenshot_path, caption=cap, buttons=btns)
                else:
                    await bot.send_message(LOG_CHANNEL_ID, cap, buttons=btns)
            except Exception as e:
                logger.error(f"Admin log error: {e}")
                await bot.send_message(LOG_CHANNEL_ID, cap, buttons=btns)
            
            await conv.send_message("✅ <b>UTR Submitted successfully!</b>\n\n"
                                   f"{P_UTR} UTR: <code>{utr_number}</code>\n"
                                   f"{P_SCREEN} Screenshot: {'✅ Received' if screenshot_path else '❌ Not Provided'}\n\n"
                                   f"Amount will be added to your balance as soon as our admin verifies the payment.\n"
                                   f"Thank you for your patience! 🙏")
            
            # Cleanup
            if uid in pending_utr:
                del pending_utr[uid]
            
        except asyncio.TimeoutError:
            await conv.send_message("❌ Time out. Please try again.")
        except Exception as e:
            logger.error(f"UTR Error: {e}")
            await conv.send_message("❌ Error processing your request. Please try again.")

def approve_deposit(deposit_id, amount):
    return mongo_store.approve_deposit(deposit_id, amount)

# ================= BUYING FLOW =================
def get_available_account_products():
    return mongo_store.inventory_products()


def get_product_stock(product):
    category = normalize_optional_text(product.get("category"))
    dc = product.get("dc")
    if dc is not None and str(dc).strip().lower() in {"none", "null", ""}:
        dc = None
    filters = mongo_store.inventory_filter(
        country=product["country"],
        year=product["year"],
        price=product["price"],
        category=category,
        dc=dc,
        available=1,
        country_prefix=False,
    )
    return mongo_store.count_inventory(filters)


def get_product_token(product):
    identity = (
        product.get("country") or "",
        normalize_optional_text(product.get("category")),
        product.get("dc") or "",
        product.get("year"),
        int(product.get("price") or 0),
    )
    return hashlib.sha256(json.dumps(identity, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()[:16]


def resolve_product(token):
    for product in get_available_account_products():
        if get_product_token(product) == token:
            return product
    return None


def format_store_product_line(product):
    display = f"{product['icon']} {product['country']}"
    category = normalize_optional_text(product.get("category"))
    if category:
        display += f" ({category})"
    dc = normalize_optional_text(product.get("dc"))
    if dc:
        display += f" (dc {dc})"
    if product.get("year"):
        display += f" {product['year']}"
    display += f": ${to_usd(product['price']):.2f} (₹{product['price']}) - Stock: {product['stock']}"
    return f"> • {display}"


def get_product_button_label(product):
    icon = product.get("icon") or "🌍"
    country = str(product.get("country") or "Unknown").strip()
    category = normalize_optional_text(product.get("category"))
    label = f"{icon} {country}"
    if category:
        short = f" • {category}"
        if len(label + short) <= 22:
            label += short
    if len(label) > 22:
        label = label[:21] + "…"
    return label


def account_store_caption(products, page, total_pages, page_products, flow):
    labels = get_store_buttons(flow)
    product_lines = [format_store_product_line(product) for product in page_products]
    page_text = ""
    if total_pages > 1:
        page_text = f"\n\n📄 {html.escape(labels['page'].format(page=page, total_pages=total_pages))}"
    values = {
        "rate": f"{get_usdt_rate():g}",
        "available": sum(product["stock"] for product in products),
        "products": "\n".join(product_lines) if product_lines else f"\n\n{P_PKG} No accounts/sessions are currently available.\n\nPlease check again later.",
        "page": page_text,
        "total_pages": total_pages
    }
    try:
        return get_store_message(flow).format(**values)
    except (KeyError, ValueError):
        return STORE_DEFAULT_MESSAGES[flow].format(**values)

async def render_account_store(event, flow, page=1, send_banner=False):
    limit = 10
    products = get_available_account_products()
    total_pages = max(1, (len(products) + limit - 1) // limit)
    page = min(max(page, 1), total_pages)
    page_products = products[(page - 1) * limit:page * limit]

    uid = event.sender_id
    account_product_state[uid] = {"page": page}
    product_buttons = []
    for product in page_products:
        label = get_product_button_label(product)
        product_buttons.append(Button.inline(label, f"prod|{flow}|{get_product_token(product)}"))
    buttons = [product_buttons[index:index + 2] for index in range(0, len(product_buttons), 2)]

    if total_pages > 1:
        navigation = []
        if page > 1:
            navigation.append(Button.inline(get_store_buttons(flow)["previous"], f"shop|{flow}|{page - 1}"))
        navigation.append(Button.inline(get_store_buttons(flow)["page"].format(page=page, total_pages=total_pages), "shop_noop"))
        if page < total_pages:
            navigation.append(Button.inline(get_store_buttons(flow)["next"], f"shop|{flow}|{page + 1}"))
        buttons.append(navigation)
    buttons.append([Button.inline(get_store_buttons(flow)["back"], "shop_back")])

    caption = account_store_caption(products, page, total_pages, page_products, flow)
    buy_account_banner = get_buy_account_banner() if flow == "single" else None
    banner = get_banner_reference(get_setting(store_banner_key(flow)))
    if send_banner and buy_account_banner:
        if len(caption) <= 1024:
            try:
                await bot.send_file(event.chat_id, buy_account_banner, caption=caption, buttons=buttons)
                return
            except Exception:
                pass
    if send_banner and banner and not buy_account_banner:
        try:
            await bot.send_file(event.chat_id, banner, caption=caption, buttons=buttons)
            return
        except Exception:
            pass
    if isinstance(event, events.CallbackQuery.Event):
        await event.edit(caption, buttons=buttons)
    else:
        await event.respond(caption, buttons=buttons)

async def show_product_details(event, flow, token):
    state = account_product_state.get(event.sender_id, {})
    page = state.get("page", 1) if isinstance(state, dict) else 1
    product = resolve_product(token)
    if not product:
        await event.edit(f"{P_NO} <b>Out of Stock</b>\n\nThis product is no longer available.", buttons=[[Button.inline(get_store_buttons(flow)["back"], f"shop|{flow}|{page}")]])
        return
    stock = get_product_stock(product)
    if stock == 0:
        await event.edit(f"{P_NO} <b>Out of Stock</b>\n\nThis product is no longer available.", buttons=[[Button.inline(get_store_buttons(flow)["back"], f"shop|{flow}|{page}")]])
        return

    lines = [
        f"{P_CART} <b>PRODUCT DETAILS</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"🌍 <b>Country:</b> {product['icon']} {html.escape(product['country'])}"
    ]
    category = normalize_optional_text(product.get("category"))
    if category:
        lines.append(f"📌 <b>Type:</b> {html.escape(category)}")
    dc = normalize_optional_text(product.get("dc"))
    if dc:
        lines.append(f"🖥 <b>DC:</b> {html.escape(str(dc))}")
    if product.get("year"):
        lines.append(f"📅 <b>Year:</b> {html.escape(str(product['year']))}")
    lines.extend([
        "",
        f"💵 <b>Price:</b> ${to_usd(product['price']):.2f}",
        f"🇮🇳 <b>Price:</b> {P_INR}{product['price']}",
        f"{P_PKG} <b>Available:</b> {stock}"
    ])
    buttons = [
        [Button.inline(get_store_buttons(flow)["buy"], f"pbuy|{flow}|{token}")],
        [Button.inline(get_store_buttons(flow)["back"], f"shop|{flow}|{page}")]
    ]
    await event.edit("\n".join(lines), buttons=buttons)

async def show_countries(event, flow, page=1):
    return await render_account_store(event, flow, page, send_banner=not isinstance(event, events.CallbackQuery.Event))

async def show_years(event, flow, country):
    grouped = {}
    for document in mongo_store.list_inventory(
        {
            "country_name": {
                "$regex": f"^{re.escape(str(country))}",
                "$options": "i",
            },
            "available": 1,
        }
    ):
        key = (document.get("account_year"), int(document.get("price") or 0))
        grouped[key] = grouped.get(key, 0) + 1
    rows = sorted(
        [(year, price, count) for (year, price), count in grouped.items()],
        key=lambda row: str(row[0]),
        reverse=True,
    )
    if not rows: return await event.answer("❌ Out of stock for this country.", alert=True)

    uid = event.sender_id
    discount = get_user_discount(uid)

    msg = f"{PE_FLOWER} <b>Select Account Year</b>\n{P_GLOBE} Country: <b>{country}</b>\n\n"
    btns = []
    
    for (y, p, c) in rows:
        disp_p = p if discount == 0 else int(p * (100 - discount) / 100)
        disc_text = f" (-{discount}%)" if discount > 0 else ""
        btns.append([Button.inline(f"{y} | ₹{disp_p}{disc_text} | {c}", f"by|{flow}|{country}|{y}|{p}")])

    btns.append([Button.inline("Back to Countries", f"pg_c|{flow}|1")])
    await event.edit(msg, buttons=btns)

async def confirm_purchase(event, country, year, price_str, category=None, dc=None):
    uid = event.sender_id
    base_price = int(price_str)
    
    bal = get_user_balance(uid)
    discount = get_user_discount(uid)
    final_price = base_price if discount == 0 else int(base_price * (100 - discount) / 100)

    msg = (f"{PE_CHECK} <b>Confirm Your Purchase</b>\n\n"
           f"{P_FLAG} <b>Country:</b> {country}\n"
           f"{P_CAL} <b>Year:</b> {year}\n"
           f"{P_MONEY} <b>Final Price:</b> {P_INR}{final_price}\n\n"
           f"{P_CARD} <b>Your Balance:</b> {P_INR}{bal}\n\n"
           f"❓ Do you want to proceed with this purchase?")
    
    category_key = (category or "Standard").strip() or "Standard"
    dc_key = "" if dc is None or str(dc).strip().lower() in {"none", "null", ""} else str(dc)
    btns = [
        [Button.inline(get_store_buttons("single")["buy"], f"buy_cf|{country}|{year}|{base_price}|{category_key}|{dc_key}")],
        [Button.inline(get_store_buttons("single")["cancel"], "cancel_action")]
    ]
    await event.edit(msg, buttons=btns)

async def process_purchase(event, country, year_str, price_str, category=None, dc=None):
    message = getattr(event, "message", None)
    message_id = getattr(message, "id", None) or getattr(event, "id", None)
    chat_id = getattr(event, "chat_id", None)
    attempt_key = (
        (event.sender_id, chat_id, message_id)
        if message_id is not None
        else None
    )
    purchase_id = (
        f"{event.sender_id}:{chat_id}:{message_id}"
        if message_id is not None
        else None
    )
    if attempt_key is not None:
        if attempt_key in purchase_attempts:
            logger.info("Ignoring repeated purchase callback key=%r", attempt_key)
            return
        if not mongo_store.claim_purchase_callback(
            event.sender_id,
            chat_id,
            message_id,
        ):
            logger.info("Ignoring Mongo-claimed purchase callback key=%r", attempt_key)
            purchase_attempts[attempt_key] = "completed"
            return
        purchase_attempts[attempt_key] = "processing"
    try:
        result = await _process_purchase(
            event, country, year_str, price_str, category, dc, purchase_id
        )
        if attempt_key is not None:
            purchase_attempts[attempt_key] = "completed"
        return result
    except Exception:
        if attempt_key is not None:
            purchase_attempts[attempt_key] = "failed"
        logger.exception(
            "process_purchase failed uid=%s country=%r year=%r price=%r category=%r dc=%r",
            getattr(event, "sender_id", None),
            country,
            year_str,
            price_str,
            category,
            dc,
        )
        raise


async def _process_purchase(
    event, country, year_str, price_str, category=None, dc=None, purchase_id=None
):
    uid, base_price = event.sender_id, int(price_str)

    discount = get_user_discount(uid)
    final_price = base_price if discount == 0 else int(base_price * (100 - discount) / 100)

    category_was_omitted = (
        category is None or str(category).strip().lower() in {"", "none", "null"}
    )
    dc_was_omitted = dc is None or str(dc).strip().lower() in {"", "none", "null"}
    category = (category or "Standard").strip() or "Standard"
    dc = None if dc_was_omitted else str(dc)

    async with get_user_lock(uid):
        reserved = mongo_store.reserve_inventory_item(
            country,
            year_str,
            base_price,
            category,
            dc,
            match_any_category=category_was_omitted,
            match_any_dc=dc_was_omitted,
        )
        if not reserved:
            return await event.answer("❌ Sold out! Another user just bought this account.", alert=True)

        phone = reserved["phone"]
        sess = reserved["session_file"]
        c_icon = reserved.get("country_icon") or "🌍"
        actual_year = reserved.get("account_year")
        twofa_pass = reserved.get("twofa") or "None"

        try:
            debited = mongo_store.deduct_balance(
                uid,
                final_price,
                event_key=f"purchase:single:{purchase_id}:debit",
                event_type="purchase_debit",
            )
            if not debited:
                mongo_store.release_inventory([phone])
                return await event.answer(f"❌ Insufficient Balance! Need ₹{final_price}", alert=True)
        except Exception:
            mongo_store.release_inventory([phone])
            raise

    try:
        await event.edit(f"{PE_LIGHTNING} <b>Fetching Number (+{phone})...</b>")
        clean_sess = sess if not sess.endswith(".session") else sess[:-8]
        client = get_inventory_client(reserved)
    except Exception:
        async with get_user_lock(uid):
            rollback_single_purchase(
                uid, phone, final_price, purchase_id, remove_inventory=False
            )
        raise
    
    try:
        await client.connect()
        if not await client.is_user_authorized(): raise Exception("Session dead")
    except Exception:
        logger.exception(
            "process_purchase session validation failed uid=%s phone=%s",
            uid,
            phone,
        )
        async with get_user_lock(uid):
            rollback_single_purchase(
                uid, phone, final_price, purchase_id, remove_inventory=True
            )
        try:
            await client.disconnect()
        except Exception:
            logger.exception("process_purchase disconnect failed uid=%s phone=%s", uid, phone)
        delete_session_files(sess)
        return await event.edit(f"{P_NO} <b>Account Invalid.</b> Money refunded. Try buying another.")

    msg = (f"{PE_LIGHTNING} <b>Order Active!</b>\n\n"
           f"{P_PHONE} <b>Phone:</b> <code>{phone}</code>\n"
           f"{P_FLAG} <b>Country:</b> {c_icon} {country}\n\n"
           f"🔻 <b>INSTRUCTIONS:</b>\n"
           f"1. Open Telegram & Add Account\n"
           f"2. Enter the number above.\n"
           f"3. {P_WAIT} <b>Please wait!</b> The bot is actively listening for your OTP and will send it automatically once Telegram delivers it.\n\n"
           f"<i>Note: If no OTP is received within 10 minutes, the bot will auto-cancel and refund your balance automatically.</i>")
    
    sent_msg = await event.edit(msg)
    
    active_orders[phone] = {
        'uid': uid,
        'client': client, 'sess': sess, 'start_time': time.time(), 
        'paid': False, 'price': final_price, 'country': country, 'year': actual_year, 
        'c_icon': c_icon, 'twofa': twofa_pass, 'msg_id': sent_msg.id,
        'purchase_id': purchase_id,
    }
    asyncio.create_task(auto_otp_task(phone))

async def auto_otp_task(phone):
    if phone not in active_orders: return
    
    order = active_orders[phone]
    client = order['client']
    start_time = order['start_time']
    uid = order['uid']
    msg_id = order['msg_id']
    
    while time.time() - start_time < get_auto_cancel_seconds():
        if phone not in active_orders: return 
        try:
            msgs = await client.get_messages(777000, limit=5)
            code = None
            for m in msgs:
                if m.date.timestamp() > start_time - 10: 
                    if m.message and re.search(OTP_REGEX, m.message) and "Login detected" not in m.message:
                        code = re.search(OTP_REGEX, m.message).group()
                        break
            
            if code:
                if not order['paid']:
                    async with get_user_lock(uid):
                        mongo_store.create_order(
                            uid,
                            order['country'],
                            order['year'],
                            order['price'],
                            phone,
                            code,
                            purchase_key=f"purchase:single:{order['purchase_id']}",
                        )
                        order['paid'] = True
                        mongo_store.delete_inventory(phone)
                    
                    await log_primary_purchase(uid, order['country'], order['price'], order['price'], order['year'], 1)
                
                twofa_text = f"{P_2FA} <b>2FA:</b> <code>{order['twofa']}</code>" if order['twofa'] != "None" else f"🔓 <b>2FA:</b> <code>Disabled (No Password)</code>"
                msg_text = (f"{PE_CHECK} <b>Latest OTP Fetched!</b>\n\n"
                            f"{P_PHONE} <b>Phone:</b> <code>{phone}</code>\n"
                            f"{P_FLAG} <b>Country:</b> {order['c_icon']} {order['country']}\n"
                            f"{P_OTP} <b>OTP:</b> <code>{code}</code>\n"
                            f"{twofa_text}")
                
                try: 
                    await bot.edit_message(uid, msg_id, msg_text, buttons=[[Button.inline("🔄 Get OTP Again", f"get_otp_again|{phone}")], [Button.inline("🚪 Finish & Logout", f"logout_bot|{phone}")]])
                except MessageNotModifiedError: pass
                except Exception: 
                    await bot.send_message(uid, msg_text, buttons=[[Button.inline("🔄 Get OTP Again", f"get_otp_again|{phone}")], [Button.inline("🚪 Finish & Logout", f"logout_bot|{phone}")]])
                return 
        except Exception: pass
        await asyncio.sleep(6) 
        
    if phone in active_orders and not active_orders[phone]['paid']:
        order = active_orders.pop(phone)
        try: await order['client'].disconnect()
        except: pass
        
        async with get_user_lock(uid):
            update_balance(
                uid,
                order["price"],
                f"purchase:single:{order['purchase_id']}:timeout_refund",
                "timeout_refund",
            )
        mongo_store.set_inventory_available(phone, 1)
            
        try: await bot.edit_message(uid, msg_id, f"{P_TIME} <b>Order Expired!</b>\nThe 10-minute limit for <code>{phone}</code> ran out. Your money ({P_INR}{order['price']}) has been automatically refunded.")
        except: pass

async def init_session_purchase(event, country, year, price_str, category=None, dc=None):
    uid, price = event.sender_id, int(price_str)
    category = (category or "Standard").strip() or "Standard"
    dc = None if dc is None or str(dc).strip().lower() in {"none", "null", ""} else str(dc)

    stock = mongo_store.count_inventory(
        mongo_store.inventory_filter(
            country=country,
            year=year,
            price=price,
            category=category,
            dc=dc,
            available=1,
        )
    )
    if stock == 0: return await event.answer("❌ Out of stock!", alert=True)
    
    session_buy_state[uid] = {'country': country, 'year': year, 'price': price, 'stock': stock, 'category': category, 'dc': dc}
    discount = get_user_discount(uid)
    p_disp = price if discount == 0 else int(price * (100 - discount) / 100)
    
    msg = (f"{PE_GIFT} <b>Buy {country} ({year}) Sessions</b>\n\n"
           f"{P_MONEY} <b>Price per session:</b> {P_INR}{p_disp}\n"
           f"{P_PKG} <b>Available Stock:</b> {stock}\n\n"
           f"👇 <b>Reply to this message</b> with the <b>Number of Sessions</b> you want to buy.")
    await event.edit(msg, buttons=[[Button.inline(get_store_buttons("bulk")["cancel"], "cancel_action")]])

async def process_bulk_sessions(event, uid, qty, state, final_cost):
    country, year, price = state['country'], int(state['year']), int(state['price'])
    category = (state.get('category') or 'Standard').strip() or 'Standard'
    dc = state.get('dc')
    if dc is not None and str(dc).strip().lower() in {"none", "null", ""}:
        dc = None
    await event.respond(f"{PE_LIGHTNING} <b>Processing your sessions...</b>")

    async with get_user_lock(uid):
        reserved = mongo_store.reserve_inventory(
            qty, country, year, price, category, dc
        )
        if len(reserved) < qty:
            return await event.respond(f"{P_NO} Stock changed during processing. Purchase Cancelled.")
        
        try:
            phones = [item["phone"] for item in reserved]
            purchase_key = "purchase:bulk:" + ",".join(sorted(str(phone) for phone in phones))
            debited = mongo_store.deduct_balance(
                uid,
                final_cost,
                event_key=f"{purchase_key}:debit",
                event_type="purchase_debit",
            )
            if not debited:
                mongo_store.release_inventory(item["phone"] for item in reserved)
                return await event.respond(f"{P_NO} Insufficient Balance! Purchase Cancelled.")

            price_per_acc = final_cost // qty
            created_order_ids = []
            for p in phones:
                purchase_key = f"purchase:bulk:{p}"
                existing_order = mongo_store.orders.find_one({"purchase_key": purchase_key})
                order_document = mongo_store.create_order(
                    uid,
                    country,
                    year,
                    price_per_acc,
                    p,
                    "SESSION_FILES",
                    purchase_key=purchase_key,
                )
                if existing_order is None:
                    created_order_ids.append(order_document["_id"])
        except Exception:
            for order_id in locals().get("created_order_ids", []):
                mongo_store.delete_order(order_id)
            mongo_store.release_inventory(item["phone"] for item in reserved)
            if "purchase_key" in locals():
                update_balance(
                    uid,
                    final_cost,
                    f"{purchase_key}:rollback",
                    "purchase_rollback",
                )
            raise

    zip_name = f"sessions_{uid}_{int(time.time())}.zip"
    numbers_txt = ""

    try:
        with zipfile.ZipFile(zip_name, 'w') as zf:
            for item in reserved:
                phone = item["phone"]
                sess_file = item["session_file"]
                twofa_pass = item.get("twofa") or "None"
                base_s = sess_file if not sess_file.endswith(".session") else sess_file[:-8]
                for ext in ['.session', '.session-wal', '.session-shm', '.session-journal']:
                    src = base_s + ext
                    if os.path.exists(src): zf.write(src, os.path.basename(src))
                
                pass_text = twofa_pass if twofa_pass != "None" else "No_Password"
                numbers_txt += f"+{phone} | pass:{pass_text}\n"
            
            numbers_txt += "\n\nPurchased from @Freshtgsales\n"
            zf.writestr("numbers.txt", numbers_txt)
            
        caption = f"{PE_GIFT} <b>Bulk Purchase Successful!</b>\n\n{P_FLAG} Country: {country}\n{P_PKG} Quantity: {qty}\n{P_CARD} Total Paid: {P_INR}{final_cost}\n\n<i>(Note: Sessions are safely provided, the bot does not keep them active)</i>"
        await bot.send_file(uid, zip_name, caption=caption)
        await log_primary_purchase(uid, country, price, final_cost, year, qty)
    except Exception as e: await event.respond(f"{P_WARN} Error creating zip: {e}")
    finally:
        if os.path.exists(zip_name): os.remove(zip_name)

# ================= STATS & PROFILE FUNCTIONS =================
async def profile_handler(event):
    uid = event.sender_id
    user = mongo_store.get_user(uid)
    if not user:
        return await bot.send_message(event.chat_id, "⚠️ Error: Please type /start to initialize your account.")
    bal = mongo_store.get_balance(uid)
    dep = user.get("total_deposited", 0) or 0
    date = user.get("joined_date") or ""
    discount = user.get("discount", 0) or 0
    ref_count = mongo_store.count_referrals(uid)
    me = await bot.get_me()
    bot_username = me.username or ""
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}" if bot_username else None
    disc_msg = f"\n{P_GIFT} Active Discount: <b>{discount}% OFF</b>" if discount > 0 else ""
    ref_block = (f"{P_USERS} <b>Your Referral Link:</b>\n<code>{ref_link}</code>\n\n"
                 if ref_link else
                 f"{P_USERS} <b>Referral Link:</b>\n<i>Set a public bot username to enable referrals.</i>\n\n")
    
    msg = (f"{PE_KISS} <b>USER PROFILE</b>\n\n"
           f"{P_ID} User ID: <code>{uid}</code>\n"
           f"{P_MONEY} Balance: <code>${to_usd(bal):.2f} (₹{bal})</code>\n"
           f"{P_CARD} Deposited: <code>${to_usd(dep):.2f} (₹{dep})</code>{disc_msg}\n"
           f"{P_USERS} Referred Users: <b>{ref_count}</b>\n"
           f"{P_CAL} Joined: {date[:10]}\n\n"
           f"{ref_block}"
           f"<i>(Share this link with your friends to earn bonuses!)</i>")
    if await send_bannered_message(event, MY_PROFILE_BANNER_SETTING, msg):
        return
    await bot.send_message(event.chat_id, msg)

async def stats_handler(event, is_callback=False):
    uid = event.sender_id
    user = mongo_store.get_user(uid, {"total_deposited": 1})
    if not user: return
    dep = user.get("total_deposited", 0) or 0
    user_orders = list(mongo_store.orders.find({"user_id": int(uid)}))
    total_orders = len(user_orders)
    spent = sum(int(order.get("price", 0) or 0) for order in user_orders)
    ref_count = mongo_store.count_referrals(uid)
    
    msg = (f"{PE_CROWN} <b>My Statistics</b>\n\n"
           f"{P_CART} <b>Accounts Bought:</b> {total_orders}\n"
           f"{P_USERS} <b>Referrals:</b> {ref_count}\n"
           f"{P_MONEY} <b>Total Spent:</b>\n${to_usd(spent):.2f}\n"
           f"{P_CARD} <b>Total Deposited:</b>\n${to_usd(dep):.2f}")
    
    btns = [[Button.inline("View Purchase Logs", "page_purchases_1")], [Button.inline("Referral Logs", "view_referrals")]]
    if is_callback:
        if await send_bannered_message(event, MY_STATS_BANNER_SETTING, msg, btns):
            return
        try: await event.edit(msg, buttons=btns)
        except MessageNotModifiedError: pass
    else:
        if await send_bannered_message(event, MY_STATS_BANNER_SETTING, msg, btns):
            return
        await bot.send_message(event.chat_id, msg, buttons=btns)

async def send_purchase_page(event, uid, page):
    limit = 5
    offset = (page - 1) * limit
    user_orders = sorted(
        mongo_store.orders.find({"user_id": int(uid)}),
        key=lambda order: int(order.get("_id", 0)),
        reverse=True,
    )
    total = len(user_orders)
    rows = [
        (order.get("phone"), order.get("date"))
        for order in user_orders[offset:offset + limit]
    ]
    
    msg = f"{PE_FLOWER} <b>Purchase History</b>\nPage {page}\n\n"
    if not rows: msg += "No purchases found."
    else:
        for ph, d in rows:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
                d_str = dt.strftime("%a %b %d %H:%M:%S %Y")
            except:
                d_str = d
            msg += f"{P_PHONE} {ph}\n{P_CAL} {d_str}\n────────────────\n"
            
    nav = []
    if page > 1: nav.append(Button.inline("Prev", f"page_purchases_{page-1}"))
    nav.append(Button.inline("Back", "back_to_stats"))
    if offset + limit < total: nav.append(Button.inline("Next", f"page_purchases_{page+1}"))
    await event.edit(msg, buttons=[nav])

async def view_referrals(event):
    ref_count = mongo_store.count_referrals(event.sender_id)
    await event.answer(f"👥 You have referred {ref_count} user(s).", alert=True)

# ================= ADMIN ACTIONS =================
async def admin_panel_handler(event):
    uid = event.sender_id
    if not is_admin(uid): return
    
    status_text = "🟢 Bot is ON" if is_bot_online() else "🔴 Bot is OFF"
    total_users = mongo_store.count_users()
    total_stock = mongo_store.count_inventory({"available": 1})
    pending_deposits = mongo_store.deposits.count_documents({"status": "pending"})
    btns = []
    
    if uid in ADMIN_IDS or has_perm(uid, 'p_settings'):
        btns.append([Button.inline(f"Status: {status_text}", "adm_togglebot")])
        
    r1 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_add_stock'):
        r1.extend([Button.inline("Add Single Acc", "adm_addstock"), Button.inline("Add ZIP", "adm_addzip")])
        btns.append([Button.inline("🛠 Maintenance Mode", "adm_maintenance"), Button.inline("⚙️ General Settings", "adm_general")])
    if r1: btns.append(r1)

    r2 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock'):
        r2.extend([Button.inline("Manage Stock", "adm_managestock"), Button.inline("Auto Price", "adm_autoprice")])
    if r2: btns.append(r2)

    r3 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_stats'):
        r3.extend([Button.inline("Statistics", "adm_stats"), Button.inline("Broadcast", "adm_bcast")])
        r3.append(Button.inline("User Info", "adm_userinfo"))
    if r3: btns.append(r3)

    r4 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_bal'):
        r4.extend([Button.inline("Change Balance", "adm_bal"), Button.inline("Ban User", "adm_ban")])
    if r4: btns.append(r4)

    r5 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_settings'):
        r5.extend([Button.inline("Discount", "adm_discount"), Button.inline("Ref %", "adm_refpct")])
        btns.append(r5)
        btns.append([Button.inline("📝 Set Welcome Msg", "adm_welcome"), Button.inline("🖼️ Banner Images", "adm_banner")])
        btns.append([Button.inline("📝 Store Messages", "adm_store_messages"), Button.inline("⚙️ Store Buttons", "adm_store_buttons")])
        btns.append([Button.inline("Support URL", "adm_supporturl"), Button.inline("Payments", "adm_payments")])
        btns.append([Button.inline("💳 Payment Methods", "adm_payment_methods")])
        btns.append([Button.inline("👑 Owner", "adm_setting_edit|owner_username")])
        btns.append([Button.inline("Set USDT Rate", "adm_usdtrate")])
        btns.append([Button.inline("Backup Users", "adm_backupusr"), Button.inline("Restore Users", "adm_restoreusr")])

    if uid in ADMIN_IDS:
        btns.append([Button.inline("Manage Admins", "adm_manageadmins")])

    header = (f"{PE_CROWN} <b>ADVANCED ADMIN DASHBOARD</b>\n\n"
              f"{P_USERS} Users: <b>{total_users}</b>\n"
              f"{P_PKG} Available Stock: <b>{total_stock}</b>\n"
              f"{P_WAIT} Pending Deposits: <b>{pending_deposits}</b>")
    await bot.send_message(event.chat_id, header, buttons=btns)

async def maintenance_menu(event):
    enabled = is_maintenance_mode()
    status = "🟢 Enabled" if enabled else "🔴 Disabled"
    buttons = [
        [Button.inline("🟢 Enable", "adm_maintenance_set|on"), Button.inline("🔴 Disable", "adm_maintenance_set|off")],
        [Button.inline("✏️ Change Message", "adm_maintenance_message")],
        [Button.inline("📊 Status", "adm_maintenance_status")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(
        f"🛠 <b>Maintenance Mode</b>\n\nStatus: <b>{status}</b>\n\n"
        f"Current message:\n{html.escape(get_maintenance_message())}",
        buttons=buttons
    )

async def general_settings_menu(event):
    msg = (f"⚙️ <b>General Settings</b>\n\n"
           f"🔗 Support URL: <code>{html.escape(get_support_url())}</code>\n"
                   f"👑 Owner: <code>{html.escape(get_owner_url())}</code>\n"
           f"📜 Terms URL: <code>{html.escape(get_terms_url())}</code>\n"
           f"💱 USDT rate: <b>{get_usdt_rate()}</b> INR\n"
           f"⏱ Auto-cancel: <b>{get_auto_cancel_seconds()}</b> seconds")
    buttons = [
        [Button.inline("🔗 Support URL", "adm_setting_edit|support_url")],
        [Button.inline("👑 Owner", "adm_setting_edit|owner_username")],
        [Button.inline("📜 Terms URL", "adm_setting_edit|terms_url")],
        [Button.inline("💱 USDT Rate", "adm_setting_edit|usdt_rate")],
        [Button.inline("⏱ Auto-cancel Seconds", "adm_setting_edit|auto_cancel_seconds")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(msg, buttons=buttons)

async def payment_methods_menu(event):
    automatic_status = "🟢 ON" if is_payment_method_enabled("automatic") else "🔴 OFF"
    manual_status = "🟢 ON" if is_payment_method_enabled("manual") else "🔴 OFF"
    buttons = [
        [Button.inline(f"⚡ Automatic Payment: {automatic_status}", "adm_payment_toggle|automatic")],
        [Button.inline(f"💳 Manual Payment: {manual_status}", "adm_payment_toggle|manual")],
        [Button.inline("🔙 Back", "adm_adminmain")],
    ]
    await event.edit(
        f"💳 <b>Payment Methods</b>\n\n"
        f"⚡ Automatic Payment: {automatic_status}\n"
        f"💳 Manual Payment: {manual_status}",
        buttons=buttons,
    )

def get_stats_period(period):
    now = datetime.utcnow()
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_week = start_today - timedelta(days=start_today.weekday())
    start_month = start_today.replace(day=1)
    periods = {
        "today": ("Today", start_today.strftime("%Y-%m-%d %H:%M:%S")),
        "week": ("This Week", start_week.strftime("%Y-%m-%d %H:%M:%S")),
        "month": ("This Month", start_month.strftime("%Y-%m-%d %H:%M:%S")),
        "all": ("All Time", None),
    }
    return periods.get(period, periods["all"])

async def render_admin_stats(event, period="all"):
    if not has_perm(event.sender_id, 'p_stats'):
        return await event.answer("Not authorized.", alert=True)
    label, since = get_stats_period(period)
    date_filter = "" if period == "all" else " AND date >= '" + since + "'"

    total_users = mongo_store.count_users()
    period_users = (
        mongo_store.count_users({"joined_date": {"$gte": since}})
        if period != "all"
        else total_users
    )
    banned_users = mongo_store.count_users({"banned": 1})
    total_balance = mongo_store.sum_user_field("balance")
    total_upi_revenue = get_setting("upi_revenue", "0")

    deposit_filters = {"status": "approved"}
    if period != "all":
        deposit_filters["date"] = {"$gte": since}
    approved_deposits = list(mongo_store.deposits.find(deposit_filters))
    deposit_row = (
        len(approved_deposits),
        sum(int(document.get("amount", 0) or 0) for document in approved_deposits),
    )
    total_deposits = mongo_store.sum_user_field("total_deposited")
    period_deposit_count, period_deposits = deposit_row

    all_orders = list(mongo_store.orders.find({}))
    period_order_documents = (
        all_orders
        if period == "all"
        else [
            order for order in all_orders
            if str(order.get("date") or "") >= since
        ]
    )
    period_orders = len(period_order_documents)
    period_sales = sum(
        int(order.get("price", 0) or 0) for order in period_order_documents
    )
    total_orders = len(all_orders)
    total_sales = sum(int(order.get("price", 0) or 0) for order in all_orders)

    total_referrals = mongo_store.count_referrals()
    period_referrals = (
        mongo_store.count_users(
            {"referred_by": {"$ne": None}, "joined_date": {"$gte": since}}
        )
        if period != "all"
        else total_referrals
    )
    top_referrers = mongo_store.top_referrers()

    available_stock = mongo_store.count_inventory({"available": 1})
    used_stock = mongo_store.count_inventory({"available": 0})
    pending_deposits = mongo_store.deposits.count_documents({"status": "pending"})
    top_text = "Unavailable"
    if top_referrers:
        top_text = "\n".join(f"<code>{referrer}</code>: {count}" for referrer, count in top_referrers)

    msg = (f"{P_STATS} <b>ADVANCED STATISTICS</b>\n"
           f"📅 Period: <b>{label}</b>\n\n"
           f"{P_USERS} <b>USERS</b>\n"
           f"Total: <b>{total_users}</b> | In period: <b>{period_users}</b>\n"
           f"Banned: <b>{banned_users}</b>\n\n"
           f"{P_MONEY} <b>FINANCIAL</b>\n"
           f"Total deposits: <b>{P_INR}{total_deposits}</b>\n"
           f"In period: <b>{P_INR}{period_deposits}</b> ({period_deposit_count} approved)\n"
           f"Total UPI revenue: <b>{P_INR}{html.escape(str(total_upi_revenue))}</b>\n"
           f"Total sales/revenue: <b>{P_INR}{total_sales}</b>\n"
           f"Period sales/revenue: <b>{P_INR}{period_sales}</b>\n\n"
           f"{P_CART} <b>ORDERS / STOCK</b>\n"
           f"Total orders: <b>{total_orders}</b> | In period: <b>{period_orders}</b>\n"
           f"Pending deposits: <b>{pending_deposits}</b>\n"
           f"Pending/completed/cancelled orders: <i>Unavailable (not stored)</i>\n"
           f"Available stock: <b>{available_stock}</b>\n"
           f"Used stock records: <b>{used_stock}</b>\n"
           f"Overall user balance: <b>{P_INR}{total_balance}</b>\n\n"
           f"{P_GIFT} <b>REFERRALS</b>\n"
           f"Total referrals: <b>{total_referrals}</b> | In period: <b>{period_referrals}</b>\n"
           f"Referral rewards issued: <i>Unavailable (not stored)</i>\n"
           f"Top referrers:\n{top_text}")
    buttons = [
        [Button.inline("Today", "adm_statsp|today"), Button.inline("This Week", "adm_statsp|week")],
        [Button.inline("This Month", "adm_statsp|month"), Button.inline("All Time", "adm_statsp|all")],
        [Button.inline("🔄 Refresh", f"adm_statsp|{period}")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(msg, buttons=buttons)

async def manage_admins_menu(event):
    rows = [(user_id,) for user_id in mongo_store.list_admin_ids()]
    msg = f"{PE_CROWN} <b>Manage Sub-Admins</b>\n\n"
    for r in rows: msg += f"{P_ACC} <code>{r[0]}</code>\n"
    btns = [[Button.inline("Add Admin", "adm_addadmin"), Button.inline("Edit Admin", "adm_editadminreq")],
            [Button.inline("Back", "adm_adminmain")]]
    await event.edit(msg, buttons=btns)

async def render_user_management(event, target_id):
    user = mongo_store.get_user(target_id)
    if not user:
        return await event.edit(f"{P_NO} <b>User not found.</b>", buttons=[[Button.inline("Back", "adm_adminmain")]])

    user_id = int(user["_id"])
    balance = mongo_store.get_balance(user_id)
    referred_by = user.get("referred_by")
    deposited = user.get("total_deposited", 0) or 0
    joined = user.get("joined_date") or ""
    banned = user.get("banned", 0) or 0
    discount = user.get("discount", 0) or 0
    username = "Not available"
    name = "Not available"
    try:
        telegram_user = await bot.get_entity(int(user_id))
        username = f"@{html.escape(telegram_user.username)}" if telegram_user.username else "No username"
        name = html.escape(" ".join(filter(None, [telegram_user.first_name, telegram_user.last_name]))) or "No name"
    except Exception:
        pass

    user_orders = list(mongo_store.orders.find({"user_id": user_id}))
    approved_deposits = list(
        mongo_store.deposits.find({"user_id": user_id, "status": "approved"})
    )
    deposit_row = (
        len(approved_deposits),
        sum(int(document.get("amount", 0) or 0) for document in approved_deposits),
    )
    referral_count = mongo_store.count_referrals(user_id)
    order_count = len(user_orders)
    spent = sum(int(order.get("price", 0) or 0) for order in user_orders)
    approved_count, approved_total = deposit_row

    msg = (f"{P_ACC} <b>USER MANAGEMENT</b>\n\n"
           f"{P_ID} Telegram ID: <code>{user_id}</code>\n"
           f"👤 Username: <b>{username}</b>\n"
           f"🪪 Name: <b>{name}</b>\n"
           f"{P_MONEY} Balance: <b>{P_INR}{balance}</b>\n"
           f"{P_CARD} Deposited: <b>{P_INR}{deposited}</b> ({approved_count} approved, {P_INR}{approved_total})\n"
           f"{P_CART} Orders: <b>{order_count}</b> ({P_INR}{spent} spent)\n"
           f"{P_USERS} Referred by: <code>{referred_by if referred_by else 'None'}</code>\n"
           f"{P_USERS} Referrals: <b>{referral_count}</b>\n"
           f"{P_CAL} Joined: <b>{joined}</b>\n"
           f"{P_GIFT} Discount: <b>{discount}%</b>\n"
           f"{P_OFF} Status: <b>{'Banned' if banned else 'Active'}</b>")

    buttons = []
    if has_perm(event.sender_id, 'p_bal'):
        buttons.append([Button.inline("💰 Change Balance", f"adm_um_bal|{user_id}")])
        if banned:
            buttons.append([Button.inline("✅ Unban User", f"adm_um_ban|{user_id}|0")])
        else:
            buttons.append([Button.inline("🚫 Ban User", f"adm_um_ban|{user_id}|1")])
    buttons.append([Button.inline("Back to Admin", "adm_adminmain")])
    await event.edit(msg, buttons=buttons)

async def start_user_search(event, action):
    if not (has_perm(event.sender_id, 'p_stats') or has_perm(event.sender_id, 'p_bal')):
        return await event.answer("Not authorized.", alert=True)
    async with bot.conversation(event.chat_id, timeout=180) as conv:
        try:
            await conv.send_message(
                f"{P_ACC} <b>Enter Telegram numeric user ID:</b>\n\n"
                f"Search by username is unavailable because usernames are not stored in the database.\n"
                f"<i>Type /cancel to abort.</i>"
            )
            response = await conv.get_response()
            value = response.text.strip()
            if value.lower() == "/cancel":
                return await conv.send_message("✅ Cancelled.")
            if not value.isdigit() or int(value) <= 0:
                return await conv.send_message(f"{P_NO} Enter a valid numeric Telegram user ID.")
            target_id = int(value)
            if not mongo_store.get_user(target_id, {"_id": 1}):
                return await conv.send_message(f"{P_NO} <b>User not found.</b>")
            if action == "balance":
                await conv.send_message(
                    f"{P_MONEY} <b>Enter balance change for <code>{target_id}</code>:</b>\n"
                    f"Use a positive number to increase or a negative number to decrease.\n"
                    f"<i>Type /cancel to abort.</i>"
                )
                amount_text = (await conv.get_response()).text.strip()
                if amount_text.lower() == "/cancel":
                    return await conv.send_message("✅ Cancelled.")
                if not re.fullmatch(r"[+-]?\d+", amount_text) or int(amount_text) == 0:
                    return await conv.send_message(f"{P_NO} Enter a non-zero whole number, such as <code>500</code> or <code>-100</code>.")
                amount = int(amount_text)
            else:
                amount = None
            class ConversationEvent:
                sender_id = event.sender_id
                async def edit(self, text, buttons):
                    await conv.send_message(text, buttons=buttons)
            if amount is None:
                await render_user_management(ConversationEvent(), target_id)
            else:
                await confirm_balance_change(ConversationEvent(), target_id, amount)
        except asyncio.TimeoutError:
            await conv.send_message(f"{P_NO} Timed out. Please try again.")

async def confirm_balance_change(event, target_id, amount):
    user = mongo_store.get_user(target_id, {"balance": 1})
    if not user:
        return await event.answer("User not found.", alert=True)
    balance = int(user.get("balance", 0) or 0)
    new_balance = balance + amount
    if new_balance < 0:
        return await event.answer("This change would make the balance negative.", alert=True)
    action = "increase" if amount > 0 else "decrease"
    admin_user_state[event.sender_id] = {
        "target_id": target_id,
        "amount": amount,
        "event_key": f"admin:{event.sender_id}:{target_id}:{time.time_ns()}",
    }
    await event.edit(
        f"{P_WARN} <b>Confirm balance change</b>\n\nUser: <code>{target_id}</code>\n"
        f"Current: <b>{P_INR}{balance}</b>\nChange: <b>{action} {P_INR}{abs(amount)}</b>\n"
        f"New balance: <b>{P_INR}{new_balance}</b>",
        buttons=[
            [Button.inline("✅ Confirm", f"adm_um_balcf|{target_id}"), Button.inline("❌ Cancel", f"adm_um|{target_id}")]
        ]
    )

async def start_balance_for_user(event, target_id):
    if not mongo_store.get_user(target_id, {"_id": 1}):
        return await event.answer("User not found.", alert=True)
    async with bot.conversation(event.chat_id, timeout=180) as conv:
        try:
            await conv.send_message(
                f"{P_MONEY} <b>Enter balance change for <code>{target_id}</code>:</b>\n"
                f"Use a positive number to increase or a negative number to decrease.\n"
                f"<i>Type /cancel to abort.</i>"
            )
            amount_text = (await conv.get_response()).text.strip()
            if amount_text.lower() == "/cancel":
                return await conv.send_message("✅ Cancelled.")
            if not re.fullmatch(r"[+-]?\d+", amount_text) or int(amount_text) == 0:
                return await conv.send_message(f"{P_NO} Enter a non-zero whole number, such as <code>500</code> or <code>-100</code>.")
            class ConversationEvent:
                sender_id = event.sender_id
                async def edit(self, text, buttons):
                    await conv.send_message(text, buttons=buttons)
            await confirm_balance_change(ConversationEvent(), target_id, int(amount_text))
        except asyncio.TimeoutError:
            await conv.send_message(f"{P_NO} Timed out. Please try again.")

async def edit_admin_menu(event, target_id):
    admin = mongo_store.get_admin(target_id)
    if not admin: return await event.answer("Admin not found", alert=True)
    p = [
        "✅" if admin.get(permission, 0) == 1 else "❌"
        for permission in ("p_add_stock", "p_manage_stock", "p_stats", "p_bal", "p_settings")
    ]
    
    btns = [
        [Button.inline(f"Add Stock: {p[0]}", f"adm_tglperm|{target_id}|p_add_stock")],
        [Button.inline(f"Manage Stock: {p[1]}", f"adm_tglperm|{target_id}|p_manage_stock")],
        [Button.inline(f"Stats & Bcast: {p[2]}", f"adm_tglperm|{target_id}|p_stats")],
        [Button.inline(f"Bal & Users: {p[3]}", f"adm_tglperm|{target_id}|p_bal")],
        [Button.inline(f"Settings: {p[4]}", f"adm_tglperm|{target_id}|p_settings")],
        [Button.inline("Remove Admin", f"adm_deladmin|{target_id}")],
        [Button.inline("Back", "adm_manageadmins")]
    ]
    await event.edit(f"✏️ <b>Editing Admin:</b> <code>{target_id}</code>", buttons=btns)

async def send_manage_stock_page(event, page):
    limit = 10
    offset = (page - 1) * limit
    rows = [(country,) for country in mongo_store.list_inventory_countries()]
    total = len(rows)
    countries = rows[offset:offset+limit]
    
    btns = []
    for (c,) in countries: 
        flag = get_flag_by_country_name(c)
        btns.append([Button.inline(f"{flag} {c}", f"adm_msc|{c}")])
    
    nav = []
    if page > 1: nav.append(Button.inline("Prev", f"adm_mspg|{page-1}"))
    if offset + limit < total: nav.append(Button.inline("Next", f"adm_mspg|{page+1}"))
    if nav: btns.append(nav)
    btns.append([Button.inline("Back", "adm_adminmain")])
    await event.edit(f"{PE_LOCATION} <b>Manage Stock</b> (Page {page})\nSelect a country to edit its properties:", buttons=btns)

async def send_manage_stock_country(event, c_name):
    years = [(year,) for year in mongo_store.list_inventory_years(c_name)]
    flag = get_flag_by_country_name(c_name)
    btns = [
        [Button.inline("Edit Country Name", f"adm_msedit|name|{c_name}"), Button.inline("Edit Flag", f"adm_msedit|flag|{c_name}")],
        [Button.inline("Edit Common Price (All Years)", f"adm_msedit|cprice|{c_name}")]
    ]
    y_btns = []
    for (y,) in years: y_btns.append(Button.inline(f"{y}", f"adm_msedit|yprice|{c_name}|{y}"))
    
    for i in range(0, len(y_btns), 3): btns.append(y_btns[i:i+3])
    btns.append([Button.inline("Back", "adm_mspg|1")])
    await event.edit(f"{flag} <b>Managing: {c_name}</b>\nSelect an option to edit:", buttons=btns)

async def send_autoprice_page(event, page):
    limit = 10
    offset = (page - 1) * limit
    c_list = set([c[0] for c in COUNTRY_CODES.values()])
    for c in mongo_store.list_inventory_countries():
        c_list.add(c)
    
    custom_countries = mongo_store.list_custom_countries()
    for country in custom_countries:
        if country.get("name"):
            c_list.add(country["name"])

    c_list = sorted(list(c_list))
    total = len(c_list)
    countries = c_list[offset:offset+limit]
    
    btns = []
    for c in countries: 
        flag = get_flag_by_country_name(c)
        btns.append([Button.inline(f"{flag} {c}", f"adm_apc|{c}")])
        
    nav = []
    if page > 1: nav.append(Button.inline("Prev", f"adm_appg|{page-1}"))
    if offset + limit < total: nav.append(Button.inline("Next", f"adm_appg|{page+1}"))
    if nav: btns.append(nav)
    btns.append([Button.inline("Add Custom Country", "adm_ap_add_country")])
    btns.append([Button.inline("Back", "adm_adminmain")])
    await event.edit(f"{PE_LIGHTNING} <b>Auto Price Setup</b> (Page {page})\nSelect a country to set fixed prices:", buttons=btns)

async def send_autoprice_country(event, c_name):
    flag = get_flag_by_country_name(c_name)
    btns = [[Button.inline("Set Common Price", f"adm_apset|{c_name}|Common")]]
    y_btns = []
    for y in range(2024, 1999, -1): y_btns.append(Button.inline(f"{y}", f"adm_apset|{c_name}|{y}"))
    for i in range(0, len(y_btns), 4): btns.append(y_btns[i:i+4])
    btns.append([Button.inline("Back", "adm_appg|1")])
    await event.edit(f"{flag} <b>Auto Price: {c_name}</b>\nSelect 'Common' for default price, or specific years:", buttons=btns)

async def welcome_manager_menu(event):
    uid = event.sender_id
    me = await bot.get_me()
    current = get_welcome_message(uid, get_setting("ref_percent", "3"), me.username or "")
    btns = [
        [Button.inline("✏️ Edit", "adm_welcome_edit"), Button.inline("👁 Preview", "adm_welcome_preview")],
        [Button.inline("🔄 Reset", "adm_welcome_reset")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(f"📝 <b>Welcome Message</b>\n\n{current}", buttons=btns)

async def banner_manager_menu(event):
    enabled = get_setting("images_enabled", "off") == "on"
    status = "🟢 ON" if enabled else "🔴 OFF"
    banner_status = "configured" if get_setting("banner_photo") else "not configured"
    buy_account_banner_status = (
        "configured"
        if get_setting(BUY_ACCOUNT_BANNER_SETTING)
        else "not configured"
    )
    my_stats_banner_status = "configured" if get_setting(MY_STATS_BANNER_SETTING) else "not configured"
    my_profile_banner_status = "configured" if get_setting(MY_PROFILE_BANNER_SETTING) else "not configured"
    deposit_banner_status = "configured" if get_setting(DEPOSIT_BANNER_SETTING) else "not configured"
    btns = [
        [Button.inline("➕ Add/Replace Banner", "adm_banner_add")],
        [Button.inline("🗑️ Delete Banner", "adm_banner_delete"), Button.inline("👁️ Preview Banner", "adm_banner_preview")],
        [Button.inline("🛒 Buy Account Banner", "adm_buy_account_banner")],
        [
            Button.inline("🗑️ Delete Buy Account Banner", "adm_buy_account_banner_delete"),
            Button.inline("👁️ Preview Buy Account Banner", "adm_buy_account_banner_preview"),
        ],
        [Button.inline("📊 My Stats Banner", "adm_my_stats_banner")],
        [Button.inline("🗑️ Delete My Stats Banner", "adm_my_stats_banner_delete"), Button.inline("👁️ Preview My Stats Banner", "adm_my_stats_banner_preview")],
        [Button.inline("👤 My Profile Banner", "adm_my_profile_banner")],
        [Button.inline("🗑️ Delete My Profile Banner", "adm_my_profile_banner_delete"), Button.inline("👁️ Preview My Profile Banner", "adm_my_profile_banner_preview")],
        [Button.inline("💳 Deposit Banner", "adm_deposit_banner")],
        [Button.inline("🗑️ Delete Deposit Banner", "adm_deposit_banner_delete"), Button.inline("👁️ Preview Deposit Banner", "adm_deposit_banner_preview")],
        [Button.inline(f"Images {'ON' if enabled else 'OFF'}", "adm_banner_toggle")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(
        f"🖼️ <b>Banner Images</b>\n\n"
        f"Status: {status}\n"
        f"Banner: {banner_status}\n"
        f"Buy Account Banner: {buy_account_banner_status}\n"
        f"My Stats Banner: {my_stats_banner_status}\n"
        f"My Profile Banner: {my_profile_banner_status}\n"
        f"Deposit Banner: {deposit_banner_status}",
        buttons=btns,
    )

async def store_settings_menu(event, flow):
    name = "Account" if flow == "single" else "Sessions"
    key = "account" if flow == "single" else "sessions"
    preview_label = "👁 Preview Account Store" if flow == "single" else "👁 Preview Sessions Store"
    buttons = [
        [Button.inline("✏️ Edit Message", f"adm_store_msg|{flow}"), Button.inline(preview_label, f"adm_store_preview|{flow}")],
        [Button.inline("🖼 Set Banner", f"adm_store_banner|{flow}"), Button.inline("👁 Preview Banner", f"adm_store_banner_preview|{flow}")],
        [Button.inline("🗑 Remove Banner", f"adm_store_banner_delete|{flow}")],
        [Button.inline("↩️ Back", "adm_store_messages")]
    ]
    return await event.edit(
        f"🛒 <b>{name} Store</b>\n\n"
        f"Message: {'customized' if get_setting(f'{key}_store_message') else 'default'}\n"
        f"Banner: {'configured' if get_setting(store_banner_key(flow)) else 'not configured'}",
        buttons=buttons
    )

async def store_messages_menu(event):
    return await event.edit(
        "📝 <b>Store Messages</b>\n\nChoose the store to configure.",
        buttons=[[Button.inline("🛒 Buy Account Message", "adm_store_config|single")],
                 [Button.inline("🔐 Buy Sessions Message", "adm_store_config|bulk")],
                 [Button.inline("↩️ Back", "adm_adminmain")]]
    )

async def store_buttons_menu(event):
    return await event.edit(
        "⚙️ <b>Store Buttons</b>\n\nChoose the store whose labels you want to edit.",
        buttons=[[Button.inline("🛒 Account Buttons", "adm_store_btns|single")],
                 [Button.inline("🔐 Sessions Buttons", "adm_store_btns|bulk")],
                 [Button.inline("↩️ Back", "adm_adminmain")]]
    )

async def store_button_editor(event, flow):
    name = "Account" if flow == "single" else "Sessions"
    labels = get_store_buttons(flow)
    rows = [[Button.inline(f"✏️ {key}: {label[:18]}", f"adm_store_btn|{flow}|{key}")] for key, label in labels.items()]
    rows.append([Button.inline("↩️ Back", "adm_store_buttons")])
    return await event.edit(f"⚙️ <b>{name} Store Buttons</b>\n\nSelect a label to edit.", buttons=rows)

async def preview_store(event, flow):
    class PreviewEvent:
        sender_id = event.sender_id
        chat_id = event.chat_id
    await render_account_store(PreviewEvent(), flow, 1, send_banner=True)
    return await event.answer("Preview sent.", alert=True)

async def admin_actions(event):
    data_full = event.data.decode()
    if not data_full.startswith("adm_"): return
    uid = event.sender_id
    action_data = data_full[4:]
    chat = event.chat_id
    
    if action_data == "adminmain":
        await event.delete()
        class FakeEvent: chat_id = chat; sender_id = uid
        return await admin_panel_handler(FakeEvent())

    if action_data.startswith("bcast_confirm|"):
        if not has_perm(uid, 'p_stats'):
            return await event.answer("Not authorized.", alert=True)
        owner_id = int(action_data.split("|", 1)[1])
        if owner_id != uid or owner_id not in broadcast_drafts:
            return await event.answer("Broadcast draft not found or expired.", alert=True)
        if owner_id in broadcast_jobs:
            return await event.answer("Broadcast is already running.", alert=True)
        draft = broadcast_drafts[owner_id]
        await event.answer("Broadcast started.", alert=True)
        asyncio.create_task(run_broadcast(owner_id, chat, draft))
        return

    if action_data.startswith("bcast_cancel|"):
        if not has_perm(uid, 'p_stats'):
            return await event.answer("Not authorized.", alert=True)
        owner_id = int(action_data.split("|", 1)[1])
        if owner_id != uid:
            return await event.answer("Not authorized.", alert=True)
        job = broadcast_jobs.get(owner_id)
        if job:
            job["cancelled"] = True
            return await event.answer("Cancellation requested.", alert=True)
        broadcast_drafts.pop(owner_id, None)
        await event.answer("Broadcast cancelled.", alert=True)
        return await event.edit("❌ <b>Broadcast cancelled.</b>", buttons=[[Button.inline("◀️ Back", "adm_adminmain")]])

    if action_data in {"maintenance", "general"}:
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        return await maintenance_menu(event) if action_data == "maintenance" else await general_settings_menu(event)

    if action_data == "payment_methods":
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        return await payment_methods_menu(event)

    if action_data.startswith("payment_toggle|"):
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        method = action_data.split("|", 1)[1]
        if method not in PAYMENT_METHOD_SETTINGS:
            return await event.answer("Invalid payment method.", alert=True)
        setting = PAYMENT_METHOD_SETTINGS[method]
        set_setting(setting, "off" if is_payment_method_enabled(method) else "on")
        await event.answer("Payment method visibility updated.", alert=True)
        return await payment_methods_menu(event)

    if action_data == "maintenance_status":
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        status = "enabled" if is_maintenance_mode() else "disabled"
        return await event.answer(f"Maintenance mode is {status}.", alert=True)

    if action_data.startswith("maintenance_set|"):
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        desired = action_data.split("|", 1)[1]
        if desired not in {"on", "off"}:
            return await event.answer("Invalid maintenance state.", alert=True)
        admin_content_state[uid] = {"type": "maintenance_confirm", "value": desired}
        verb = "enable" if desired == "on" else "disable"
        return await event.edit(
            f"⚠️ <b>Confirm {verb} maintenance mode?</b>",
            buttons=[
                [Button.inline("✅ Confirm", f"adm_maintenance_confirm|{desired}"), Button.inline("❌ Cancel", "adm_maintenance")]
            ]
        )

    if action_data.startswith("maintenance_confirm|"):
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        desired = action_data.split("|", 1)[1]
        pending = admin_content_state.get(uid)
        if pending != {"type": "maintenance_confirm", "value": desired}:
            return await event.answer("This confirmation has expired.", alert=True)
        set_setting("maintenance_enabled", desired)
        admin_content_state.pop(uid, None)
        await event.answer("Maintenance mode updated.", alert=True)
        return await maintenance_menu(event)

    if action_data == "maintenance_message":
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        admin_content_state[uid] = "maintenance_message"
        return await event.edit(
            "✏️ <b>Send the new maintenance message.</b>\nHTML formatting is supported.",
            buttons=[[Button.inline("◀️ Cancel", "adm_maintenance")]]
        )

    if action_data.startswith("setting_edit|"):
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        setting_name = action_data.split("|", 1)[1]
        if setting_name not in {"support_url", "owner_username", "terms_url", "usdt_rate", "auto_cancel_seconds"}:
            return await event.answer("Invalid setting.", alert=True)
        admin_content_state[uid] = {"type": "general_setting", "name": setting_name}
        labels = {
            "support_url": "Support URL (http:// or https://)",
            "owner_username": "Owner username or Telegram URL",
            "terms_url": "Terms URL (http:// or https://)",
            "usdt_rate": "USDT rate in INR (positive number)",
            "auto_cancel_seconds": "Auto-cancel seconds (at least 1)"
        }
        return await event.edit(
            f"⚙️ <b>Enter {labels[setting_name]}:</b>\n\n"
            f"Current: <code>{html.escape(str(get_setting(setting_name, get_support_url() if setting_name == 'support_url' else get_owner_url() if setting_name == 'owner_username' else get_terms_url() if setting_name == 'terms_url' else get_usdt_rate() if setting_name == 'usdt_rate' else get_auto_cancel_seconds())))}</code>",
            buttons=[[Button.inline("◀️ Cancel", "adm_general")]]
        )

    if action_data in {"userinfo", "bal", "ban"}:
        required_perm = 'p_stats' if action_data == "userinfo" else 'p_bal'
        if not has_perm(uid, required_perm):
            return await event.answer("Not authorized.", alert=True)
        return await start_user_search(event, "balance" if action_data == "bal" else "info")

    if action_data.startswith("um|"):
        if not (has_perm(uid, 'p_stats') or has_perm(uid, 'p_bal')):
            return await event.answer("Not authorized.", alert=True)
        return await render_user_management(event, int(action_data.split("|", 1)[1]))

    if action_data.startswith("um_bal|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        target_id = int(action_data.split("|", 1)[1])
        return await start_balance_for_user(event, target_id)

    if action_data.startswith("um_balcf|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        target_id = int(action_data.split("|", 1)[1])
        pending = admin_user_state.pop(uid, None)
        if not pending or pending.get("target_id") != target_id:
            return await event.answer("This confirmation has expired.", alert=True)
        amount = pending["amount"]
        result = mongo_store.apply_balance_event(
            target_id,
            amount,
            pending.get(
                "event_key",
                f"admin:{uid}:{target_id}:{amount}",
            ),
            "admin_balance_adjustment",
        )
        if not result.get("applied") and not result.get("already_applied"):
            return await event.answer("Balance change was not applied.", alert=True)
        await event.answer("Balance updated.", alert=True)
        return await render_user_management(event, target_id)

    if action_data.startswith("um_ban|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        _, target_text, desired_text = action_data.split("|")
        target_id, desired = int(target_text), int(desired_text)
        user = mongo_store.get_user(target_id, {"banned": 1})
        if not user:
            return await event.answer("User not found.", alert=True)
        if user.get("banned", 0) != desired:
            return await render_user_management(event, target_id)
        admin_user_state[uid] = {"ban_target": target_id, "ban_value": desired}
        verb = "ban" if desired else "unban"
        return await event.edit(
            f"{P_WARN} <b>Confirm {verb} for user <code>{target_id}</code>?</b>",
            buttons=[[Button.inline("✅ Confirm", f"adm_um_bancf|{target_id}|{desired}"), Button.inline("❌ Cancel", f"adm_um|{target_id}")]]
        )

    if action_data.startswith("um_bancf|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        _, target_text, desired_text = action_data.split("|")
        target_id, desired = int(target_text), int(desired_text)
        pending = admin_user_state.pop(uid, None)
        if not pending or pending.get("ban_target") != target_id or pending.get("ban_value") != desired:
            return await event.answer("This confirmation has expired.", alert=True)
        mongo_store.set_user_fields(target_id, {"banned": desired})
        await event.answer("User status updated.", alert=True)
        return await render_user_management(event, target_id)

    if action_data in {"welcome", "welcome_edit", "welcome_cancel", "welcome_preview", "welcome_reset", "banner", "banner_add", "banner_cancel", "banner_delete", "banner_preview", "banner_toggle", "buy_account_banner", "buy_account_banner_cancel", "buy_account_banner_delete", "buy_account_banner_preview", "my_stats_banner", "my_stats_banner_cancel", "my_stats_banner_delete", "my_stats_banner_preview", "my_profile_banner", "my_profile_banner_cancel", "my_profile_banner_delete", "my_profile_banner_preview", "deposit_banner", "deposit_banner_cancel", "deposit_banner_delete", "deposit_banner_preview", "store_messages", "store_buttons"} or action_data.startswith(("store_config|", "store_msg|", "store_preview|", "store_banner", "store_btns|", "store_btn|")):
        if not (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
            return await event.answer("Not authorized.", alert=True)

    if action_data == "store_messages":
        return await store_messages_menu(event)
    if action_data == "store_buttons":
        return await store_buttons_menu(event)
    if action_data.startswith("store_config|"):
        flow = action_data.split("|", 1)[1]
        if flow not in {"single", "bulk"}: return await event.answer("Invalid store.", alert=True)
        return await store_settings_menu(event, flow)
    if action_data.startswith("store_msg|"):
        flow = action_data.split("|", 1)[1]
        admin_content_state[uid] = {"type": "store_message", "flow": flow}
        return await event.edit(
            "📝 <b>Send the complete store message.</b>\n"
            "HTML formatting is supported. Use {rate}, {available}, {products}, and {page} for dynamic values.",
            buttons=[[Button.inline("↩️ Cancel", f"adm_store_config|{flow}")]]
        )
    if action_data.startswith("store_preview|"):
        flow = action_data.split("|", 1)[1]
        return await preview_store(event, flow)
    if action_data.startswith("store_banner|"):
        flow = action_data.split("|", 1)[1]
        admin_content_state[uid] = {"type": "store_banner", "flow": flow}
        return await event.edit("🖼 <b>Send the store banner image/photo.</b>", buttons=[[Button.inline("↩️ Cancel", f"adm_store_config|{flow}")]])
    if action_data.startswith("store_banner_preview|"):
        flow = action_data.split("|", 1)[1]
        banner = get_banner_reference(get_setting(store_banner_key(flow)))
        if not banner: return await event.answer("No banner configured.", alert=True)
        try:
            await bot.send_file(uid, banner)
            return await event.answer("Preview sent.", alert=True)
        except Exception:
            return await event.answer("Banner reference expired. Upload it again.", alert=True)
    if action_data.startswith("store_banner_delete|"):
        flow = action_data.split("|", 1)[1]
        delete_setting(store_banner_key(flow))
        await event.answer("Banner removed.", alert=True)
        return await store_settings_menu(event, flow)
    if action_data.startswith("store_btns|"):
        flow = action_data.split("|", 1)[1]
        return await store_button_editor(event, flow)
    if action_data.startswith("store_btn|"):
        parts = action_data.split("|")
        if len(parts) != 3 or parts[1] not in {"single", "bulk"} or parts[2] not in get_store_buttons(parts[1]):
            return await event.answer("Invalid store button.", alert=True)
        admin_content_state[uid] = {"type": "store_button", "flow": parts[1], "key": parts[2]}
        return await event.edit(f"✏️ <b>Send the new label for {parts[2]}.</b>", buttons=[[Button.inline("↩️ Cancel", f"adm_store_btns|{parts[1]}")]])

    if action_data in {"welcome", "welcome_edit", "welcome_cancel", "welcome_preview", "welcome_reset", "banner", "banner_add", "banner_cancel", "banner_delete", "banner_preview", "banner_toggle", "buy_account_banner", "buy_account_banner_cancel", "buy_account_banner_delete", "buy_account_banner_preview"} and not (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        return await event.answer("Not authorized.", alert=True)

    if action_data == "welcome":
        return await welcome_manager_menu(event)
    if action_data == "welcome_edit":
        admin_content_state[uid] = "welcome"
        return await event.edit("📝 <b>Send the new welcome message.</b>\nHTML formatting is supported.", buttons=[[Button.inline("◀️ Cancel", "adm_welcome_cancel")]])
    if action_data == "welcome_cancel":
        admin_content_state.pop(uid, None)
        return await welcome_manager_menu(event)
    if action_data == "welcome_preview":
        me = await bot.get_me()
        await bot.send_message(uid, get_welcome_message(uid, get_setting("ref_percent", "3"), me.username or ""))
        return await event.answer("Preview sent.", alert=True)
    if action_data == "welcome_reset":
        delete_setting("welcome_message")
        await event.answer("Welcome message reset.", alert=True)
        return await welcome_manager_menu(event)
    if action_data == "banner":
        return await banner_manager_menu(event)
    if action_data == "banner_add":
        admin_content_state[uid] = "banner"
        return await event.edit("🖼️ <b>Send the banner image/photo.</b>", buttons=[[Button.inline("◀️ Cancel", "adm_banner_cancel")]])
    if action_data == "banner_cancel":
        admin_content_state.pop(uid, None)
        return await banner_manager_menu(event)
    if action_data == "banner_toggle":
        set_setting("images_enabled", "off" if get_setting("images_enabled", "off") == "on" else "on")
        return await banner_manager_menu(event)
    if action_data == "banner_delete":
        delete_setting("banner_photo")
        await event.answer("Banner deleted.", alert=True)
        return await banner_manager_menu(event)
    if action_data == "banner_preview":
        banner = get_banner_media()
        if not banner:
            return await event.answer("No banner configured.", alert=True)
        try:
            await bot.send_file(uid, banner)
            return await event.answer("Preview sent.", alert=True)
        except Exception:
            return await event.answer("Banner reference expired. Upload it again.", alert=True)

    if action_data == "buy_account_banner":
        admin_content_state[uid] = "buy_account_banner"
        return await event.edit(
            "🛒 <b>Send the Buy Account banner image/photo.</b>",
            buttons=[[Button.inline("◀️ Cancel", "adm_buy_account_banner_cancel")]],
        )
    if action_data == "buy_account_banner_cancel":
        admin_content_state.pop(uid, None)
        return await banner_manager_menu(event)
    if action_data == "buy_account_banner_delete":
        delete_setting(BUY_ACCOUNT_BANNER_SETTING)
        await event.answer("Buy Account banner deleted.", alert=True)
        return await banner_manager_menu(event)
    if action_data == "buy_account_banner_preview":
        banner = get_buy_account_banner()
        if not banner:
            return await event.answer("No Buy Account banner configured.", alert=True)
        try:
            await bot.send_file(uid, banner)
            return await event.answer("Buy Account banner preview sent.", alert=True)
        except Exception:
            return await event.answer("Banner reference expired. Upload it again.", alert=True)

    banner_actions = {
        "my_stats_banner": (MY_STATS_BANNER_SETTING, "📊 My Stats"),
        "my_profile_banner": (MY_PROFILE_BANNER_SETTING, "👤 My Profile"),
        "deposit_banner": (DEPOSIT_BANNER_SETTING, "💳 Deposit"),
    }
    if action_data in banner_actions:
        setting, label = banner_actions[action_data]
        admin_content_state[uid] = action_data
        return await event.edit(
            f"{label} banner image/photo.",
            buttons=[[Button.inline("◀️ Cancel", f"adm_{action_data}_cancel")]],
        )
    if action_data.endswith("_cancel") and action_data[:-7] in banner_actions:
        admin_content_state.pop(uid, None)
        return await banner_manager_menu(event)
    if action_data.endswith("_delete") and action_data[:-7] in banner_actions:
        setting, label = banner_actions[action_data[:-7]]
        delete_setting(setting)
        await event.answer(f"{label} banner deleted.", alert=True)
        return await banner_manager_menu(event)
    if action_data.endswith("_preview") and action_data[:-8] in banner_actions:
        setting, label = banner_actions[action_data[:-8]]
        banner = get_banner_reference(get_setting(setting))
        if not banner:
            return await event.answer(f"No {label} banner configured.", alert=True)
        try:
            await bot.send_file(uid, banner)
            return await event.answer("Preview sent.", alert=True)
        except Exception:
            return await event.answer("Banner reference expired. Upload it again.", alert=True)

    if action_data == "togglebot" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        new_status = 'off' if is_bot_online() else 'on'
        set_setting("bot_status", new_status)
        await event.answer(f"Bot turned {new_status.upper()}", alert=True)
        class FakeEvent: chat_id = chat; sender_id = uid
        await admin_panel_handler(FakeEvent())
        await event.delete()
        return

    elif action_data == "stats" and (uid in ADMIN_IDS or has_perm(uid, 'p_stats')):
        return await render_admin_stats(event, "all")

    elif action_data.startswith("statsp|") and (uid in ADMIN_IDS or has_perm(uid, 'p_stats')):
        return await render_admin_stats(event, action_data.split("|", 1)[1])

    elif action_data == "payments" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        btns = [
            [Button.inline("Add Payment Method", "adm_addpay")],
            [Button.inline("Remove Payment Method", "adm_delpay")],
            [Button.inline("Back to Admin", "adm_adminmain")]
        ]
        return await event.edit(f"{P_CARD} <b>Manage Payment Methods</b>", buttons=btns)

    elif action_data == "manageadmins" and uid in ADMIN_IDS:
        return await manage_admins_menu(event)

    elif action_data.startswith("tglperm|") and uid in ADMIN_IDS:
        _, t_id, p_name = action_data.split("|")
        if p_name not in {"p_add_stock", "p_manage_stock", "p_stats", "p_bal", "p_settings"}:
            return await event.answer("Invalid permission.", alert=True)
        mongo_store.toggle_permission(int(t_id), p_name)
        return await edit_admin_menu(event, t_id)
        
    elif action_data.startswith("deladmin|") and uid in ADMIN_IDS:
        t_id = action_data.split("|")[1]
        mongo_store.delete_admin(int(t_id))
        await event.answer("✅ Admin Removed", alert=True)
        return await manage_admins_menu(event)

    elif action_data == "managestock" and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_manage_stock_page(event, 1)
    elif action_data.startswith("mspg|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_manage_stock_page(event, int(action_data.split("|")[1]))
    elif action_data.startswith("msc|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_manage_stock_country(event, action_data.split("|")[1])
    elif action_data == "autoprice" and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_autoprice_page(event, 1)
    elif action_data.startswith("appg|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_autoprice_page(event, int(action_data.split("|")[1]))
    elif action_data.startswith("apc|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_autoprice_country(event, action_data.split("|")[1])
        
    elif action_data == "backupusr" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        with open("users_backup.csv", "w", newline="", encoding="utf-8") as f:
            fields = [
                "user_id", "balance", "referred_by", "total_deposited",
                "joined_date", "banned", "discount", "terms_accepted",
            ]
            w = csv.writer(f)
            w.writerow(fields)
            for user in mongo_store.list_users():
                w.writerow([
                    user["_id"],
                    user.get("balance", 0) or 0,
                    user.get("referred_by"),
                    user.get("total_deposited", 0),
                    user.get("joined_date"),
                    user.get("banned", 0),
                    user.get("discount", 0),
                    user.get("terms_accepted", 0),
                ])
        await bot.send_file(chat, "users_backup.csv", caption=f"{P_USERS} <b>Users Backup CSV</b>")
        os.remove("users_backup.csv")
        return await event.answer("✅ Backup Generated!", alert=True)

    async with bot.conversation(chat, timeout=600) as conv:
        async def get_reply(txt):
            await conv.send_message(txt + "\n\n<i>(Type /cancel to abort)</i>")
            resp = await conv.get_response()
            if resp.text == "/cancel": raise ValueError("Cancelled")
            return resp

        try:
            if action_data == "ap_add_country" and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
                code = (await get_reply(f"{P_PHONE} <b>Enter Country Calling Code (without +):</b>\n<i>Example: 91</i>")).text.replace("+", "").strip()
                flag = html.escape((await get_reply(f"{P_FLAG} <b>Enter Country Flag Emoji:</b>\n<i>Example: 🇮🇳</i>")).text.strip())
                name = html.escape((await get_reply(f"{P_GLOBE} <b>Enter Country Name:</b>\n<i>Example: India</i>")).text.strip())
                
                mongo_store.save_custom_country(code, name, flag)
                await conv.send_message(f"{P_YES} <b>Custom Country Added Successfully!</b>\n{flag} {name} (+{code})\n\n<i>It will now automatically be recognized when adding stock!</i>")

            elif action_data == "addadmin" and uid in ADMIN_IDS:
                new_ad = int((await get_reply(f"{P_ACC} <b>Enter User ID for new Admin:</b>")).text)
                mongo_store.add_admin(new_ad)
                await conv.send_message(f"{P_YES} Admin added!")
                class FakeEvent: 
                    async def edit(self, text, buttons): await bot.send_message(chat, text, buttons=buttons)
                    async def answer(self, txt, alert): pass
                await edit_admin_menu(FakeEvent(), new_ad)
                
            elif action_data == "editadminreq" and uid in ADMIN_IDS:
                t_id = int((await get_reply(f"{P_ACC} <b>Enter User ID to edit:</b>")).text)
                class FakeEvent: 
                    async def edit(self, text, buttons): await bot.send_message(chat, text, buttons=buttons)
                    async def answer(self, txt, alert): pass
                await edit_admin_menu(FakeEvent(), t_id)

            elif action_data.startswith("msedit|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
                parts = action_data.split("|")
                action, c_name = parts[1], parts[2]
                
                if action == "name":
                    new_name = html.escape((await get_reply(f"{P_DOC} <b>Enter NEW Name for {c_name}:</b>")).text)
                    mongo_store.rename_inventory_country(c_name, new_name)
                    mongo_store.rename_auto_price_country(c_name, new_name)
                    await conv.send_message(f"{P_YES} Country '{c_name}' successfully renamed to '{new_name}'!")
                    
                elif action == "flag":
                    new_flag = html.escape((await get_reply(f"{P_FLAG} <b>Enter NEW Flag Emoji for {c_name}:</b>")).text)
                    mongo_store.update_inventory_by_country(c_name, {"country_icon": new_flag})
                    await conv.send_message(f"{P_YES} Flag updated to {new_flag} for '{c_name}'!")
                    
                elif action == "cprice":
                    new_p = int((await get_reply(f"{P_MONEY} <b>Enter NEW Common Price for all {c_name} accounts:</b>")).text)
                    mongo_store.update_inventory_by_country(c_name, {"price": new_p})
                    await conv.send_message(f"{P_YES} All existing '{c_name}' accounts updated to {P_INR}{new_p}!")
                    
                elif action == "yprice":
                    year = parts[3]
                    new_p = int((await get_reply(f"{P_MONEY} <b>Enter NEW Price for {c_name} ({year}):</b>")).text)
                    mongo_store.update_inventory_by_country(
                        c_name, {"price": new_p}, year=year
                    )
                    await conv.send_message(f"{P_YES} All existing '{c_name}' ({year}) accounts updated to {P_INR}{new_p}!")
                    
            elif action_data.startswith("apset|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
                parts = action_data.split("|")
                c_name, year = parts[1], parts[2]
                new_p = int((await get_reply(f"{P_ASST} <b>Enter Auto-Price for {c_name} ({year}):</b>\n<i>(Enter 0 to remove this auto-price)</i>")).text)
                if new_p == 0:
                    mongo_store.delete_auto_price(c_name, year)
                    await conv.send_message(f"{P_YES} Auto-Price for {c_name} ({year}) removed!")
                else:
                    mongo_store.set_auto_price(c_name, year, new_p)
                    await conv.send_message(f"{P_YES} Auto-Price for {c_name} ({year}) set to {P_INR}{new_p}! Incoming accounts will use this price automatically.")

            elif action_data == "addpay" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                name = html.escape((await get_reply(f"{P_CARD} <b>Enter Payment Method Name:</b>\n<i>(e.g., Binance Pay, TRX)</i>")).text)
                qr_msg = await get_reply(f"📸 <b>Send QR Code Image:</b>\n<i>(Or type <code>skip</code> if no QR needed)</i>")
                qr_file_id = ""
                if qr_msg.photo:
                    photo = qr_msg.photo
                    qr_file_id = json.dumps({
                        "id": photo.id,
                        "access_hash": photo.access_hash,
                        "file_reference": photo.file_reference.hex(),
                    })
                
                cap_msg = (await get_reply(f"{P_DOC} <b>Enter Payment Caption:</b>\n<i>(Use <code>text</code> to make wallet IDs or UPI copyable)</i>")).text
                cap_msg = html.escape(cap_msg).replace("&lt;code&gt;", "<code>").replace("&lt;/code&gt;", "</code>")
                mongo_store.add_custom_payment(name, cap_msg, qr_file_id)
                await conv.send_message(f"{P_YES} Payment Method '{name}' added successfully!")

            elif action_data == "delpay" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                rows = [
                    (payment["_id"], payment["name"])
                    for payment in mongo_store.list_custom_payments()
                ]
                if not rows: return await conv.send_message(f"{P_NO} No custom payment methods.")
                msg = f"{P_DOC} <b>Reply with the ID of the method to delete:</b>\n\n"
                for r in rows: msg += f"ID: {r[0]} - {r[1]}\n"
                del_id = (await get_reply(msg)).text
                try:
                    del_id = del_id.strip()
                    payment = mongo_store.get_custom_payment_by_id(del_id)
                    file_path = payment.get("qr_file_id") if payment else None
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                    if not payment:
                        raise ValueError
                    mongo_store.delete_custom_payment(payment["_id"])
                    await conv.send_message(f"{P_YES} Deleted!")
                except: await conv.send_message(f"{P_NO} Invalid ID.")

            elif action_data == "addzip" and (uid in ADMIN_IDS or has_perm(uid, 'p_add_stock')):
                resp = await get_reply(f"{P_PKG} <b>Send the ZIP file containing <code>.session</code> files:</b>")
                if not resp.file or not resp.file.name.endswith('.zip'): return await conv.send_message(f"{P_NO} Invalid file.")
                
                await conv.send_message(f"{P_WAIT} <b>Extracting & Scanning Accounts...</b>")
                zip_path = await bot.download_media(resp, "temp_sessions.zip")
                extracted_dir = f"temp_extracted_{int(time.time())}"
                os.makedirs(extracted_dir, exist_ok=True)
                with zipfile.ZipFile(zip_path, 'r') as zip_ref: zip_ref.extractall(extracted_dir)

                groups = {}
                for file in os.listdir(extracted_dir):
                    if not file.endswith(".session"): continue
                    sess_path = os.path.join(extracted_dir, file)
                    clean_path = sess_path[:-8]
                    try:
                        client = get_inventory_client({"session_file": clean_path + ".session"})
                        await client.connect()
                        if not await client.is_user_authorized(): await client.disconnect(); continue
                        me = await client.get_me()
                        phone = getattr(me, 'phone', None)
                        if not phone: await client.disconnect(); continue
                        
                        c_name, c_icon = get_country_info(phone)
                        pwd = await client(GetPasswordRequest())
                        has_2fa = pwd.has_password
                        year = await detect_account_year(client)
                        session_string = export_session_string(client)
                        await client.disconnect()

                        key = (c_name, year, has_2fa)
                        if key not in groups: groups[key] = []
                        groups[key].append({
                            "phone": phone,
                            "path": clean_path,
                            "c_icon": c_icon,
                            "session_string": session_string,
                        })
                    except Exception as e: logger.error(f"Scan error: {e}")

                for key in list(groups.keys()):
                    if key[0] == "Unknown":
                        sample_phone = groups[key][0]["phone"]
                        await conv.send_message(f"{P_WARN} <b>Country not recognized for +{sample_phone}!</b>")
                        new_icon = html.escape((await get_reply(f"{P_FLAG} <b>Enter Country Flag Emoji:</b>\n<i>Example: 🇮🇳</i>")).text)
                        new_name = html.escape((await get_reply(f"{P_GLOBE} <b>Enter Country Name:</b>\n<i>Example: India</i>")).text)
                        new_key = (new_name, key[1], key[2])
                        groups[new_key] = groups.pop(key)
                        for acc in groups[new_key]: acc["c_icon"] = new_icon

                success = 0
                added_phones = []
                for (c_name, year, has_2fa), accs in groups.items():
                    c_icon = accs[0]["c_icon"]
                    twofa_pass = "None"
                    if has_2fa: twofa_pass = html.escape((await get_reply(f"{P_2FA} <b>Enter 2FA Password for {len(accs)}x {c_name} accounts:</b>")).text)

                    auto_price = mongo_store.get_auto_price(c_name, str(year))
                    if auto_price is None:
                        auto_price = mongo_store.get_auto_price(c_name, "Common")

                    if auto_price is not None:
                        price = auto_price
                        await conv.send_message(f"⚡ <b>Auto-Price Applied:</b> {len(accs)}x {c_name} ({year}) at {P_INR}{price}.")
                    else:
                        existing_price = mongo_store.inventory_price_for_country(c_name)
                        if existing_price is not None:
                            price = existing_price
                            await conv.send_message(f"⚡ <b>Auto-Added:</b> {len(accs)}x {c_name} at {P_INR}{price} (Copied from DB).")
                        else:
                            price = int((await get_reply(f"📌 Found {len(accs)}x {c_name} ({year}).\n{P_MONEY} Enter Price (₹):")).text)

                    for acc in accs:
                        perm_base = f"sessions/{acc['phone']}"
                        for ext in ['.session', '.session-wal', '.session-shm', '.session-journal']:
                            if os.path.exists(acc['path'] + ext): shutil.move(acc['path'] + ext, perm_base + ext)
                        mongo_store.save_inventory({
                            "phone": acc["phone"],
                            "session_file": perm_base + ".session",
                            "country_name": c_name,
                            "country_icon": c_icon,
                            "account_year": year,
                            "category": "Good",
                            "price": price,
                            "available": 1,
                            "twofa": twofa_pass,
                            "added_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                            "data_center": None,
                            "session_string": encrypt_session_string(acc["session_string"]),
                            "session_storage": "encrypted_string_session",
                            "session_migrated_at": datetime.now(timezone.utc).isoformat(),
                        })
                        added_phones.append(acc['phone'])
                        success += 1
                os.remove(zip_path); shutil.rmtree(extracted_dir)
                await conv.send_message(f"{P_YES} <b>Bulk Interactive Upload Complete!</b>\n{P_ON} Added: {success}")

            elif action_data == "addstock" and (uid in ADMIN_IDS or has_perm(uid, 'p_add_stock')):
                phone = (await get_reply(f"{P_PHONE} Enter Phone (+919999...):")).text.replace(" ", "").replace("+", "")
                sp = f"sessions/{phone}"
                client = TelegramClient(sp, API_ID, API_HASH)
                await client.connect()
                sreq = await client.send_code_request(phone)

                twofa_pass = "None"
                try:
                    await client.sign_in(phone, (await get_reply(f"{P_OTP} OTP:")).text, phone_code_hash=sreq.phone_code_hash)
                except SessionPasswordNeededError:
                    twofa_pass = html.escape((await get_reply(f"{P_2FA} 2FA Pass required. Enter it now:")).text)
                    await client.sign_in(password=twofa_pass)

                c_name = ""
                while True:
                    c_name = normalize_optional_text((await get_reply(f"{P_GLOBE} <b>Country:</b>\nEnter country name.")).text)
                    if c_name:
                        break
                    await conv.send_message(f"{P_WARN} Country is required. Please enter a country name or /cancel to abort.")

                category = ""
                category_value = normalize_optional_text((await get_reply(f"{P_DOC} <b>Condition / Type:</b>\nExample: Spam Free, Spammed Account, etc.\nOr type <code>/skip</code>")).text)
                if category_value:
                    category = category_value

                dc = None
                dc_value = normalize_optional_text((await get_reply(f"{P_PC} <b>Data Center:</b>\nEnter DC or type <code>/skip</code>.")).text)
                if dc_value:
                    dc = dc_value

                year = None
                while True:
                    year_raw = normalize_optional_text((await get_reply(f"{P_CAL} <b>Year:</b>\nEnter year or type <code>/skip</code>.")).text)
                    if not year_raw:
                        break
                    try:
                        year = int(year_raw)
                        if year <= 0:
                            raise ValueError
                        break
                    except ValueError:
                        await conv.send_message(f"{P_WARN} Invalid year. Please enter a valid year or type <code>/skip</code>.")

                price = None
                while True:
                    price_raw = (await get_reply(f"{P_MONEY} <b>Price (₹):</b>\nEnter selling price in INR.")).text
                    price = parse_inr_price(price_raw)
                    if price is not None:
                        break
                    await conv.send_message(f"❌ Price is required.\n\nPlease enter the price in INR.\nOr provide /cancel to abort.")

                c_icon = get_flag_by_country_name(c_name) or "🌍"
                if c_icon == "🌍" and (country_name := c_name.lower()):
                    try:
                        c_icon = next((v[1] for k, v in COUNTRY_CODES.items() if k and str(v[0]).lower() == country_name), "🌍")
                    except Exception:
                        pass

                auto_year = await detect_account_year(client)
                if year is None:
                    year = auto_year
                session_string = export_session_string(client)
                await client.disconnect()

                mongo_store.save_inventory({
                    "phone": phone,
                    "session_file": sp + ".session",
                    "country_name": c_name,
                    "country_icon": c_icon,
                    "account_year": year,
                    "category": category,
                    "price": price,
                    "available": 1,
                    "twofa": twofa_pass,
                    "added_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    "data_center": dc,
                    "session_string": encrypt_session_string(session_string),
                    "session_storage": "encrypted_string_session",
                    "session_migrated_at": datetime.now(timezone.utc).isoformat(),
                })
                usd_price = to_usd(price)
                display = f"{c_icon} {c_name}"
                if category:
                    display += f" ({category.lower()})"
                if dc:
                    display += f" (dc {str(dc).lower()})"
                if year:
                    display += f" {year}"
                display += f": ${usd_price:.2f} (₹{price})"
                await conv.send_message(f"{P_YES} <b>Added successfully!</b>\n\n{display}")

            elif action_data == "supporturl" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                url = (await get_reply("🔗 Enter new Support URL (must start with http:// or https://):")).text
                if not url.startswith("http"): url = "https://" + url.replace("@", "t.me/")
                set_setting("support_url", url)
                await conv.send_message(f"{P_YES} Support URL updated.")

            elif action_data == "bcast" and (uid in ADMIN_IDS or has_perm(uid, 'p_stats')):
                message = await get_reply(f"{P_DOC} <b>Send the message or media to broadcast.</b>\nText, photo, video, and document captions are preserved.\nSupports HTML & tg-emoji tags.")
                if not message.text and not message.media:
                    return await conv.send_message(f"{P_NO} Empty messages cannot be broadcast.")
                btn_name = (await get_reply(f"🔘 <b>Button Name (or 'skip'):</b>")).text
                url = (await get_reply("🔗 <b>URL:</b>")).text if btn_name.lower() != 'skip' else None
                btns = [[Button.url(btn_name, url)]] if url else None
                broadcast_drafts[uid] = {
                    "message": message,
                    "text": message.text or "",
                    "caption": message.message or message.text or "",
                    "buttons": btns
                }
                await send_broadcast_preview(uid, broadcast_drafts[uid])
                await conv.send_message(f"{P_EYE} Preview sent. Confirm or cancel it using the buttons below.")

            elif action_data == "discount" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                t_uid = int((await get_reply(f"{P_ACC} <b>User ID:</b>")).text)
                pct = int((await get_reply(f"{P_GIFT} <b>Discount % (0 to remove):</b>")).text)
                if not mongo_store.set_user_fields(t_uid, {"discount": pct}):
                    await conv.send_message(f"{P_NO} User not found.")
                    return
                await conv.send_message(f"{P_YES} User {t_uid} has {pct}% discount.")
                
            elif action_data == "refpct" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                pct = int((await get_reply(f"{P_USERS} <b>New Referral %:</b>")).text)
                set_setting("ref_percent", str(pct))
                await conv.send_message(f"{P_YES} Ref revenue set to {pct}%.")

            elif action_data == "usdtrate" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                r = float((await get_reply(f"{P_USDT} <b>New USDT Rate (INR):</b>")).text)
                set_setting("usdt_rate", str(r))
                await conv.send_message(f"{P_YES} Rate set to {r}.")

            elif action_data == "restoreusr" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                resp = await get_reply(f"📤 <b>Send the <code>users_backup.csv</code> file:</b>")
                if not resp.file or not resp.file.name.endswith('.csv'): return await conv.send_message(f"{P_NO} Invalid file.")
                await bot.download_media(resp, "temp_restore.csv")
                with open("temp_restore.csv", "r", encoding="utf-8") as f:
                    reader = csv.reader(f); next(reader); count = 0
                    for row in reader:
                        try:
                            user_id = int(row[0])
                            user_document = {
                                "_id": user_id,
                                "balance": int(row[1]),
                                "referred_by": int(row[2]) if row[2] else None,
                                "total_deposited": int(row[3]),
                                "joined_date": row[4],
                                "banned": int(row[5]),
                                "discount": int(row[6]),
                                "terms_accepted": int(row[7]),
                            }
                            if mongo_store.insert_user_if_missing(user_document):
                                count += 1
                        except: pass
                os.remove("temp_restore.csv")
                await conv.send_message(f"{P_YES} Restored {count} users.")

        except ValueError: await conv.send_message(f"{P_NO} Cancelled.")
        except Exception as e: await conv.send_message(f"{P_NO} Error: {e}")

# ================= CORE EVENT ROUTERS =================
@bot.on(events.NewMessage(pattern=r"(?i)^/start"))
async def handle_start(e):
    try:
        uid = e.sender_id
        if not uid: return
        
        ensure_user(uid)
        if is_user_banned(uid): return

        if (not is_bot_online() or is_maintenance_mode()) and not is_admin(uid):
            return await e.respond(get_maintenance_message() if is_maintenance_mode() else f"{P_OFF} <b>Bot is currently under maintenance.</b> Please try again later.")
        
        session_buy_state.pop(uid, None)
        deposit_input.pop(uid, None)

        text = e.text or ''
        if len(text.split()) > 1:
            start_param = text.split()[1]
            if start_param.startswith("ref_"):
                ref = start_param.replace("ref_", "")
                if ref.isdigit() and int(ref) != uid:
                    mongo_store.set_referred_by_if_empty(uid, int(ref))

        is_joined = await check_channel_joined(uid)
        if not is_joined:
            msg = ("🔒 Access Required\n\n"
                   "To use this bot, please join our official channel(s) below.\n\n"
                   "📢 Join the channel(s), then tap:\n"
                   "✅ I've Joined – Verify\n\n"
                   "Thank you for supporting us ❤️")
            return await e.respond(msg, buttons=get_join_buttons())

        terms_acc = get_user_terms_accepted(uid)
        if not terms_acc:
            msg = f"{PE_FLOWER} <b>TERMS & CONDITIONS</b>\nPlease read and accept our Terms & Conditions before using the bot."
            return await e.respond(msg, buttons=get_terms_buttons())

        await send_main_menu(e, uid)
    except Exception as ex: 
        print(f"Start Error: {ex}")

@bot.on(events.NewMessage())
async def handle_all_messages(e):
    try:
        uid = e.sender_id
        if not uid: return
        if getattr(e, 'text', None) and e.text.startswith('/') and not (e.text.strip().lower() == '/cancel' and uid in admin_content_state): return
        if (not is_bot_online() or is_maintenance_mode()) and not is_admin(uid):
            return await e.respond(get_maintenance_message() if is_maintenance_mode() else f"{P_OFF} <b>Bot is currently under maintenance.</b> Please try again later.")
        
        ensure_user(uid)
        if is_user_banned(uid): return

        if uid in waiting_proof and (e.photo or (e.text and "http" in e.text)):
            info = waiting_proof.pop(uid)
            final_amt = info['amount']
            
            screenshot_path = None
            if e.photo:
                screenshot_path = f"screenshots/dep_{uid}_{int(time.time())}.jpg"
                os.makedirs("screenshots", exist_ok=True)
                await bot.download_media(e.photo, screenshot_path)
            
            deposit = mongo_store.create_deposit(
                uid,
                final_amt,
                info['method'],
                status="pending",
                screenshot=screenshot_path,
            )
            dep_id = deposit["_id"]
            await e.reply(f"{PE_GIFT} Deposit request submitted! Please wait for admin approval.")
            
            cap = (f"{PE_LIGHTNING} <b>💰 Deposit Request</b>\n\n"
                   f"{P_ACC} User: <code>{uid}</code>\n"
                   f"{P_ID} Order ID: <code>{dep_id}</code>\n"
                   f"{P_MONEY} Amount: <b>{P_INR}{info['amount']}</b>\n"
                   f"{P_CARD} Method: {info['method']}\n\n"
                   f"{P_SCREEN} Payment Screenshot\n")
            
            btns = [[Button.inline(f"✅ Accept (₹{final_amt})", f"dep_acc|{dep_id}|{uid}|{info['method']}|exact|{final_amt}"), Button.inline("❌ Reject", f"dep_rej|{dep_id}|{uid}")],
                    [Button.inline("📝 Custom Amount", f"dep_acc|{dep_id}|{uid}|{info['method']}|custom|0")]]
            
            try:
                if e.photo:
                    await bot.send_message(LOG_CHANNEL_ID, cap, file=e.media, buttons=btns)
                else:
                    await bot.send_message(LOG_CHANNEL_ID, cap + f"\n🔗 Hash: {html.escape(e.text)}", buttons=btns)
            except Exception as log_err:
                logger.error(f"Failed to log deposit: {log_err}")
            return

        text = e.text or ""
        if is_admin(uid) and uid in admin_content_state:
            content_type = admin_content_state[uid]
            if text.strip().lower() == "/cancel":
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Cancelled.")
            if isinstance(content_type, dict) and content_type.get("type") == "store_message":
                if not text.strip():
                    return await e.reply("❌ Store message cannot be empty.")
                set_setting(f"{'account' if content_type['flow'] == 'single' else 'sessions'}_store_message", text)
                flow = content_type["flow"]
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Store message saved.", buttons=[[Button.inline("↩️ Back", f"adm_store_config|{flow}")]])
            if isinstance(content_type, dict) and content_type.get("type") == "store_button":
                label = text.strip()
                if not label or len(label.encode("utf-8")) > 60:
                    return await e.reply("❌ Enter a non-empty label up to 60 bytes.")
                flow, button_key = content_type["flow"], content_type["key"]
                labels = get_store_buttons(flow)
                labels[button_key] = label
                set_setting(f"{'account' if flow == 'single' else 'sessions'}_button_labels", json.dumps(labels, ensure_ascii=False))
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Button label saved.", buttons=[[Button.inline("↩️ Back", f"adm_store_btns|{flow}")]])
            if isinstance(content_type, dict) and content_type.get("type") == "store_banner":
                if not e.photo:
                    return await e.reply("❌ Please send a Telegram photo.")
                photo = e.photo
                reference = {"id": photo.id, "access_hash": photo.access_hash, "file_reference": photo.file_reference.hex()}
                set_setting(store_banner_key(content_type["flow"]), json.dumps(reference))
                flow = content_type["flow"]
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Store banner saved.", buttons=[[Button.inline("↩️ Back", f"adm_store_config|{flow}")]])
            if isinstance(content_type, dict) and content_type.get("type") == "general_setting":
                name = content_type["name"]
                value = text.strip()
                try:
                    if name in {"support_url", "terms_url"}:
                        if not re.match(r"^https?://[^\s]+$", value, re.IGNORECASE):
                            raise ValueError
                    elif name == "owner_username":
                        if not re.match(r"^(?:@[A-Za-z0-9_]{5,32}|https?://t\.me/[A-Za-z0-9_]{5,32})$", value, re.IGNORECASE):
                            raise ValueError
                    elif name == "usdt_rate":
                        if float(value) <= 0:
                            raise ValueError
                        value = str(float(value))
                    elif name == "auto_cancel_seconds":
                        if not value.isdigit() or int(value) < 1:
                            raise ValueError
                    set_setting(name, value)
                    admin_content_state.pop(uid, None)
                    return await e.reply(f"✅ <b>{name}</b> saved.", buttons=[[Button.inline("Back", "adm_general")]])
                except ValueError:
                    return await e.reply("❌ Invalid value. Please try again or type /cancel.")
            if content_type == "maintenance_message":
                if not text.strip():
                    return await e.reply("❌ Maintenance message cannot be empty.")
                set_setting("maintenance_message", text.strip())
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Maintenance message saved.", buttons=[[Button.inline("Back", "adm_maintenance")]])
            if content_type == "welcome":
                if not text:
                    return await e.reply("❌ Welcome message cannot be empty.")
                set_setting("welcome_message", text)
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Welcome message saved.")
            if content_type == "banner":
                if not e.photo:
                    return await e.reply("❌ Please send a Telegram photo.")
                photo = e.photo
                reference = {
                    "id": photo.id,
                    "access_hash": photo.access_hash,
                    "file_reference": photo.file_reference.hex()
                }
                set_setting("banner_photo", json.dumps(reference))
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Banner added/replaced successfully.")
            if content_type == "buy_account_banner":
                if not e.photo:
                    return await e.reply("❌ Please send a Telegram photo.")
                photo = e.photo
                reference = {
                    "id": photo.id,
                    "access_hash": photo.access_hash,
                    "file_reference": photo.file_reference.hex()
                }
                set_setting(BUY_ACCOUNT_BANNER_SETTING, json.dumps(reference))
                admin_content_state.pop(uid, None)
                return await e.reply(
                    "✅ Buy Account banner added/replaced successfully.",
                    buttons=[[Button.inline("◀️ Back", "adm_banner")]],
                )
            banner_uploads = {
                "my_stats_banner": (MY_STATS_BANNER_SETTING, "My Stats"),
                "my_profile_banner": (MY_PROFILE_BANNER_SETTING, "My Profile"),
                "deposit_banner": (DEPOSIT_BANNER_SETTING, "Deposit"),
            }
            if content_type in banner_uploads:
                if not e.photo:
                    return await e.reply("❌ Please send a Telegram photo.")
                setting, label = banner_uploads[content_type]
                photo = e.photo
                reference = {
                    "id": photo.id,
                    "access_hash": photo.access_hash,
                    "file_reference": photo.file_reference.hex()
                }
                set_setting(setting, json.dumps(reference))
                admin_content_state.pop(uid, None)
                return await e.reply(
                    f"✅ {label} banner added/replaced successfully.",
                    buttons=[[Button.inline("◀️ Back", "adm_banner")]],
                )
        if not text: return

        if "Buy Account" in text or "Buy Sessions" in text or "Deposit" in text or "My Profile" in text or "My Stats" in text or "Support" in text or "Admin Panel" in text:
            session_buy_state.pop(uid, None)
            deposit_input.pop(uid, None)
            admin_dep_state.pop(uid, None)

        if is_admin(uid) and uid in admin_dep_state:
            st = admin_dep_state[uid]
            if st['step'] == 'wait_reason':
                t_uid, dep_id, msg_id = st['target_uid'], st['dep_id'], st['msg_id']
                transitioned = mongo_store.transition_deposit(
                    dep_id,
                    "pending",
                    "rejected",
                )
                if not transitioned:
                    admin_dep_state.pop(uid, None)
                    return await e.reply(f"{P_WARN} Deposit already processed.")
                
                try: await bot.edit_message(LOG_CHANNEL_ID, msg_id, f"{P_NO} <b>REJECTED USER {t_uid}</b>\nReason: {html.escape(text)}")
                except: pass
                
                await bot.send_message(int(t_uid), f"{P_NO} <b>Deposit Rejected!</b>\n📋 Reason: {html.escape(text)}")
                await e.reply(f"{P_YES} Rejection reason sent.")
                admin_dep_state.pop(uid)
                return

        if uid in session_buy_state:
            state = session_buy_state[uid]
            try:
                qty = int(re.sub(r'[^\d]', '', text))
                if qty < 1: raise ValueError
                if qty > state['stock']: return await e.respond(f"{P_WARN} <b>Not enough stock!</b> Max is {state['stock']}.")
                
                discount = get_user_discount(uid)
                total_cost = qty * state['price']
                if discount > 0: total_cost = int(total_cost * (100 - discount) / 100)
                    
                user_bal = mongo_store.get_balance(uid)
                if user_bal < total_cost: return await e.respond(f"{P_NO} <b>Insufficient Balance!</b>\nYou need {P_INR}{total_cost} to buy {qty} sessions.")

                session_buy_state.pop(uid)
                await process_bulk_sessions(e, uid, qty, state, total_cost)
                return
            except ValueError: return await e.respond(f"{P_NO} Please enter a valid number.")

        if uid in deposit_input and deposit_input[uid]['step'] == 'wait_amt':
            try:
                amt = int(re.sub(r'[^\d]', '', text))
                if amt < 10: return await e.reply(f"{P_WARN} Minimum Deposit is ₹10.")
                method = deposit_input[uid]['method']

                waiting_proof[uid] = {'amount': amt, 'method': method}
                deposit_input.pop(uid)

                rate = get_usdt_rate()
                usdt_amt = round(amt / rate, 2)
                rate_text = f"\n\n{P_MONEY} <b>Amount to Pay:</b> {P_INR}{amt} (~{P_USDT}{usdt_amt} USDT)\n💱 <i>Exchange Rate: {P_INR}{rate} = $1</i>"
                
                if method == "UPI":
                    return await show_upi_qr(event, amt)
                payment = mongo_store.get_custom_payment(method)
                if payment:
                    cap = payment.get("caption", "") + f"{rate_text}\n\n👇 <b>After paying, send a clear Screenshot here:</b>"
                    btns = [[Button.inline("❌ Cancel", "cancel_action")]]
                    qr_file_id = payment.get("qr_file_id")
                    qr_media = get_banner_reference(qr_file_id)
                    if qr_media:
                        try:
                            await bot.send_file(e.chat_id, qr_media, caption=cap, buttons=btns)
                        except Exception:
                            await e.reply(cap, buttons=btns)
                    elif qr_file_id and os.path.exists(qr_file_id):
                        # Legacy custom methods may still contain a local path.
                        try: await bot.send_file(e.chat_id, qr_file_id, caption=cap, buttons=btns)
                        except: await e.reply(cap, buttons=btns)
                    else: await e.reply(cap, buttons=btns)
                else:
                    await e.reply(f"{P_CARD} <b>{method} Deposit</b>{rate_text}\n\n👇 Send Screenshot here:", buttons=[[Button.inline("❌ Cancel", "cancel_action")]])
            except ValueError: 
                await e.respond(f"{P_NO} Please enter a valid number in {P_INR} (INR).")
            return

        if "Buy Account" in text: await show_countries(e, 'single', 1)
        elif "Buy Sessions" in text: await show_countries(e, 'bulk', 1)
        elif "Deposit" in text: await deposit_menu(e)
        elif "My Profile" in text: await profile_handler(e)
        elif "My Stats" in text: await stats_handler(e)
        elif "Support" in text: 
            await e.reply("🟢 <b>Support</b>\n\n⚠️ For support, contact admin.", buttons=get_support_buttons())
        elif "Admin Panel" in text: 
            if is_admin(uid): await admin_panel_handler(e)

    except Exception as ex: print(f"Message Error: {ex}")

@bot.on(events.CallbackQuery)
async def handle_callback_query(e):
    try:
        uid = e.sender_id
        if (not is_bot_online() or is_maintenance_mode()) and not is_admin(uid):
            return await e.answer(
                get_maintenance_message() if is_maintenance_mode() else "⚙️ Bot is under maintenance.",
                alert=True
            )
            
        ensure_user(uid)
        now = time.time()
        if uid in user_spam_cooldown and now - user_spam_cooldown[uid] < 0.5:
            return await e.answer("⚠️ Please slow down! Don't spam buttons.", alert=True)
        user_spam_cooldown[uid] = now

        if is_user_banned(uid): return await e.answer("🚫 BANNED", alert=True)
        data = e.data.decode()

        if data == "verify_join":
            if not await check_channel_joined(uid): return await e.answer("⚠️ You must join the channels first!", alert=True)
            terms = get_user_terms_accepted(uid)
            if not terms:
                msg = f"{PE_FLOWER} <b>TERMS & CONDITIONS</b>\nPlease read and accept our Terms & Conditions before using the bot."
                try: await e.edit(msg, buttons=get_terms_buttons())
                except MessageNotModifiedError: pass
                return
            await send_main_menu(e, uid)

        elif data == "tc_accept":
            mongo_store.set_user_fields(uid, {"terms_accepted": 1})
            await e.answer("✅ Terms Accepted!", alert=True)
            await send_main_menu(e, uid)
            
        elif data == "tc_reject":
            try: await e.edit(f"{P_NO} You cannot use the bot without accepting the terms.")
            except MessageNotModifiedError: pass
            
        elif data == "cancel_action":
            deposit_input.pop(uid, None); waiting_proof.pop(uid, None); session_buy_state.pop(uid, None)
            if uid in pending_utr:
                del pending_utr[uid]
            try: await e.edit(f"{P_NO} <b>Cancelled.</b>")
            except MessageNotModifiedError: pass

        elif data.startswith("shop|"):
            parts = data.split("|")
            if len(parts) != 3 or parts[1] not in {"single", "bulk"} or not parts[2].isdigit():
                return await e.answer("Invalid shop page.", alert=True)
            await show_countries(e, parts[1], int(parts[2]))

        elif data == "shop_noop":
            await e.answer("You are viewing this page.")

        elif data == "shop_back":
            account_product_state.pop(uid, None)
            await send_main_menu(e, uid)

        elif data.startswith("prod|"):
            parts = data.split("|")
            if len(parts) != 3 or parts[1] not in {"single", "bulk"} or not re.fullmatch(r"[0-9a-f]{16}", parts[2]):
                return await e.answer("Invalid product.", alert=True)
            await show_product_details(e, parts[1], parts[2])

        elif data.startswith("pbuy|"):
            parts = data.split("|")
            if len(parts) != 3 or parts[1] not in {"single", "bulk"} or not re.fullmatch(r"[0-9a-f]{16}", parts[2]):
                return await e.answer("Invalid product.", alert=True)
            flow, token = parts[1], parts[2]
            product = resolve_product(token)
            if not product:
                return await e.edit(
                    f"{P_NO} <b>Out of Stock</b>\n\nThis product is no longer available.",
                    buttons=[[Button.inline(get_store_buttons(flow)["back"], "shop|%s|1" % flow)]]
                )
            if get_product_stock(product) == 0:
                return await e.edit(
                    f"{P_NO} <b>Out of Stock</b>\n\nThis product is no longer available.",
                    buttons=[[Button.inline(get_store_buttons(flow)["back"], f"shop|{flow}|1")]]
                )
            if flow == "single":
                await confirm_purchase(e, product["country"], product["year"], str(product["price"]), product.get("category"), product.get("dc"))
            else:
                await init_session_purchase(e, product["country"], product["year"], str(product["price"]), product.get("category"), product.get("dc"))

        elif data.startswith("pg_c|"): 
            p = data.split("|")
            await show_countries(e, p[1], int(p[2]))

        elif data.startswith("bc|"):
            p = data.split("|")
            await show_years(e, p[1], p[2])

        elif data.startswith("by|"):
            p = data.split("|")
            if p[1] == 'single': await confirm_purchase(e, p[2], p[3], p[4])
            else: await init_session_purchase(e, p[2], p[3], p[4])
            
        elif data.startswith("buy_cf|"):
            try:
                # Acknowledge immediately so Telegram does not leave the
                # button showing a spinner during Mongo/session work.
                await e.answer()
                p = data.split("|")
                if len(p) < 4 or len(p) > 6 or not p[1] or not p[2] or not p[3]:
                    raise ValueError(f"Invalid buy callback payload: {data!r}")
                category = p[4] if len(p) > 4 else None
                dc = p[5] if len(p) > 5 else None
                await process_purchase(e, p[1], p[2], p[3], category, dc)
            except Exception:
                logger.exception("buy_cf callback failed uid=%s data=%r", uid, data)
                try:
                    await e.edit(f"{P_WARN} Purchase could not be completed. Please try again.")
                except Exception:
                    logger.exception("Unable to show buy_cf failure uid=%s data=%r", uid, data)

        elif data.startswith("get_otp_again|"):
            phone = data.split("|")[1]
            if phone not in active_orders:
                return await e.answer("⚠️ Session already logged out or expired.", alert=True)
            
            order = active_orders[phone]
            client = order['client']
            start_time = order['start_time']
            
            await e.answer("🔄 Fetching latest OTP...", alert=False)
            try:
                msgs = await client.get_messages(777000, limit=5)
                latest_code = None
                for m in msgs:
                    if m.date.timestamp() > start_time - 10:
                        if m.message and re.search(OTP_REGEX, m.message) and "Login detected" not in m.message:
                            latest_code = re.search(OTP_REGEX, m.message).group()
                            break
                
                if latest_code:
                    twofa_text = f"{P_2FA} <b>2FA:</b> <code>{order['twofa']}</code>" if order['twofa'] != "None" else f"🔓 <b>2FA:</b> <code>Disabled (No Password)</code>"
                    msg = (f"{P_YES} <b>Latest OTP Fetched!</b>\n\n"
                           f"{P_PHONE} <b>Phone:</b> <code>{phone}</code>\n"
                           f"{P_FLAG} <b>Country:</b> {order['c_icon']} {order['country']}\n"
                           f"{P_OTP} <b>OTP:</b> <code>{latest_code}</code>\n"
                           f"{twofa_text}")
                    try: await e.edit(msg, buttons=[[Button.inline("🔄 Get OTP Again", f"get_otp_again|{phone}")], [Button.inline("🚪 Finish & Logout", f"logout_bot|{phone}")]])
                    except MessageNotModifiedError: pass
                else:
                    await e.answer("⏳ No new OTP found yet. Try again in a few seconds.", alert=True)
            except Exception as ex:
                await e.answer(f"❌ Error fetching OTP.", alert=True)

        elif data.startswith("logout_bot|"):
            phone = data.split("|")[1]
            if phone in active_orders:
                order = active_orders.pop(phone)
                try: await order['client'].log_out()
                except: pass
                try: await order['client'].disconnect()
                except: pass
                delete_session_files(order['sess'])
                await e.edit(f"{P_YES} <b>Session Finished & Logged out successfully.</b>")
            else:
                await e.answer("⚠️ No active order found or already logged out.", alert=True)
        
        elif data.startswith("page_purchases_"): await send_purchase_page(e, uid, int(data.split("_")[2]))
        elif data == "back_to_stats": await stats_handler(e, is_callback=True)
        elif data == "view_referrals": await view_referrals(e)
            
        elif data == "automatic_payment":
            if not is_payment_method_enabled("automatic"):
                return await e.answer("⚠️ Automatic Payment is currently unavailable.", alert=True)
            await automatic_payment_init(e)
        elif data.startswith("automatic_check|"):
            parts = data.split("|")
            if len(parts) != 2 or not parts[1]:
                return await e.answer("❌ Invalid payment order.", alert=True)
            await check_automatic_payment(e, parts[1])
        elif data.startswith("automatic_cancel|"):
            parts = data.split("|")
            if len(parts) != 2 or not parts[1]:
                return await e.answer("❌ Invalid payment order.", alert=True)
            await cancel_automatic_payment(e, parts[1])
        elif data.startswith("depm_"):
            method = data.replace("depm_", "", 1)
            if method.strip().casefold() == "cwallet":
                return await e.answer("This payment method is no longer available.", alert=True)
            await manual_deposit_init(e, method)
        elif data == "dep_upi":
            if not is_payment_method_enabled("manual"):
                return await e.answer("⚠️ Manual Payment is currently unavailable.", alert=True)
            await init_upi_keypad(e)
        elif data == "upload_payment_screenshot":
            if uid not in waiting_proof and uid in pending_utr:
                pending = pending_utr[uid]
                waiting_proof[uid] = {
                    'amount': pending['amount'],
                    'method': 'UPI',
                    'order_id': pending['order_id']
                }
            if uid not in waiting_proof:
                return await e.answer("📸 Please upload your payment screenshot after making the transfer.", alert=True)
            try:
                await e.answer("📸 Please send your payment screenshot now.", alert=False)
                await e.edit(
                    f"{P_SCREEN} <b>Upload Payment Screenshot</b>\n\n"
                    f"Please send the <b>payment screenshot</b> for your deposit.",
                    buttons=[[Button.inline("❌ Cancel", "cancel_action")]]
                )
            except MessageNotModifiedError:
                pass
        elif data.startswith("kp_"): await keypad_logic(e)
        
        elif data.startswith("adm_") and is_admin(uid): await admin_actions(e)
        
        elif data.startswith("dkp|") and has_perm(uid, 'p_bal'):
            _, dep_id, action = data.split("|")
            dep_id = int(dep_id)
            deposit = mongo_store.get_deposit(dep_id)
            if not deposit or deposit.get("status") != 'pending':
                return await e.edit(f"{P_WARN} Already processed.")
            t_uid = int(deposit["user_id"])
            method, orig_amt = deposit["method_name"], deposit["amount"]
            
            curr = custom_dep_amt.get(dep_id, "0")
            
            if action.isdigit():
                if curr == "0": curr = action
                else: curr += action
                if len(curr) > 7: curr = curr[:7]
            elif action == "del": curr = curr[:-1] or "0"
            elif action == "cancel":
                btns = [[Button.inline(f"✅ Accept (₹{orig_amt})", f"dep_acc|{dep_id}|{t_uid}|{method}|exact|{orig_amt}"), Button.inline("❌ Reject", f"dep_rej|{dep_id}|{t_uid}")],
                        [Button.inline("📝 Custom Amount", f"dep_acc|{dep_id}|{t_uid}|{method}|custom|0")]]
                return await e.edit(f"{PE_LIGHTNING} <b>NEW DEPOSIT REQUEST</b>\n{P_ACC} User: <code>{t_uid}</code>\n{P_MONEY} Request: <b>{P_INR}{orig_amt}</b>\n{P_CARD} Method: {method}\n{P_ID} Ref: <code>{dep_id}</code>", buttons=btns)
            elif action == "conf":
                amt = int(curr)
                if amt <= 0: return await e.answer("Amount must be > 0", alert=True)
                
                async with get_user_lock(t_uid):
                    approval = approve_deposit(dep_id, amt)
                if approval["status"] != "approved":
                    return await e.edit(f"{P_WARN} Already processed.")
                prev_bal = approval["previous_balance"]
                amt = approval["amount"]
                await process_referral_bonus(
                    t_uid,
                    amt,
                    f"deposit:{dep_id}:approval",
                )
                if not approval.get("credited"):
                    return await e.edit(f"{P_WARN} Already processed.")
                await e.edit(f"{PE_CHECK} <b>APPROVED {P_INR}{amt} TO {t_uid} (Custom Amount)</b>")
                await bot.send_message(int(t_uid), f"{PE_CHECK} <b>Deposit Approved!</b>\n{P_MONEY} Amount Added: {P_INR}{amt}\n📉 Old: {P_INR}{prev_bal} | 📈 New: {P_INR}{prev_bal+amt}")
                return

            custom_dep_amt[dep_id] = curr
            await e.edit(f"{P_KEY} <b>Enter Custom Amount for User {t_uid}:</b>\n\n{P_MONEY} {curr}", buttons=get_admin_custom_keypad(dep_id))

        elif data.startswith("dep_acc|") and has_perm(uid, 'p_bal'):
            p = data.split("|")
            dep_id, t_uid, method, a_type = p[1], int(p[2]), p[3], p[4]
            deposit = mongo_store.get_deposit(dep_id)
            if not deposit:
                return await e.edit(f"{P_WARN} Already processed.")
            if a_type == "custom" and deposit.get("status") != "pending":
                return await e.edit(f"{P_WARN} Already processed.")
            
            if a_type == "exact":
                amt = int(p[5]) 
                async with get_user_lock(t_uid):
                    approval = approve_deposit(dep_id, amt)
                if approval["status"] != "approved":
                    return await e.edit(f"{P_WARN} Already processed.")
                amt = approval["amount"]
                prev_bal = approval["previous_balance"]
                
                await process_referral_bonus(
                    t_uid,
                    amt,
                    f"deposit:{dep_id}:approval",
                )
                if not approval.get("credited"):
                    return await e.edit(f"{P_WARN} Already processed.")
                
                user_msg = (f"{PE_CHECK} <b>Deposit Approved!</b>\n\n{P_MONEY} <b>Amount Added:</b> ${to_usd(amt):.2f} ({P_INR}{amt})\n"
                            f"📉 <b>Previous Balance:</b> ${to_usd(prev_bal):.2f} ({P_INR}{prev_bal})\n📈 <b>New Balance:</b> ${to_usd(prev_bal+amt):.2f} ({P_INR}{prev_bal+amt})")
                await bot.send_message(int(t_uid), user_msg)
                try: await e.edit(f"{PE_CHECK} <b>INSTANT CREDITED {P_INR}{amt} TO {t_uid}</b>")
                except MessageNotModifiedError: pass
                
            elif a_type == "custom":
                custom_dep_amt[int(dep_id)] = "0"
                await e.edit(f"{P_KEY} <b>Enter Custom Amount for User {t_uid}:</b>\n\n{P_MONEY} 0", buttons=get_admin_custom_keypad(int(dep_id)))
                
        elif data.startswith("dep_rej|") and has_perm(uid, 'p_bal'):
            p = data.split("|")
            dep_id, t_uid = p[1], int(p[2])
            deposit = mongo_store.get_deposit(dep_id)
            if not deposit or deposit.get("status") != 'pending':
                return await e.edit(f"{P_WARN} Already processed.")
            admin_dep_state[uid] = {'target_uid': t_uid, 'dep_id': dep_id, 'step': 'wait_reason', 'msg_id': e.message.id}
            await bot.send_message(uid, f"{P_WARN} Reply to this message with the REASON for rejecting user <code>{t_uid}</code>:")
            try: await e.answer("Check your bot PMs to enter the reason.", alert=True)
            except: pass

    except Exception as ex: print(f"Callback Error: {ex}")

async def main():
    await migrate_inventory_sessions()
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ Health server listening on 0.0.0.0:{port}")

    print("=" * 50)
    print("✅ ULTIMATE ADVANCED HTML BOT STARTED SUCCESSFULLY")
    print("=" * 50)
    print(f"✅ Admins: {ADMIN_IDS}")
    print(f"✅ Support: @{SUPPORT_USERNAME_1} & @{SUPPORT_USERNAME_2}")
    print("=" * 50)
    try:
        await bot.run_until_disconnected()
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    bot.start(bot_token=BOT_TOKEN)
    persist_bot_session()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
