import sqlite3
import re
import tempfile
import threading
import unittest
from pathlib import Path

from pymongo import ReturnDocument

from mongo_persistence import (
    COLLECTION_NAMES,
    MongoRepository,
    MongoRuntimeStore,
    SQLiteMongoMigrator,
    managed_projection,
    sqlite_connection_read_only,
    sqlite_row_to_document,
)


class FakeCollection:
    def __init__(self):
        self.documents = []
        self.indexes = [{"name": "_id_", "key": {"_id": 1}, "unique": True}]
        self.create_index_calls = []
        self._lock = threading.Lock()

    @staticmethod
    def matches(document, query):
        for key, value in (query or {}).items():
            if key == "$or":
                if not any(FakeCollection.matches(document, item) for item in value):
                    return False
                continue
            actual = document.get(key)
            if isinstance(value, dict):
                if "$ne" in value:
                    forbidden = value["$ne"]
                    if (
                        actual == forbidden
                        or isinstance(actual, list)
                        and forbidden in actual
                    ):
                        return False
                if "$exists" in value and (key in document) != value["$exists"]:
                    return False
                if "$in" in value and actual not in value["$in"]:
                    return False
                if "$nin" in value:
                    forbidden = value["$nin"]
                    if actual in forbidden or (
                        isinstance(actual, list)
                        and any(item in forbidden for item in actual)
                    ):
                        return False
                if "$gte" in value and (actual is None or actual < value["$gte"]):
                    return False
                if "$regex" in value:
                    options = re.IGNORECASE if "i" in value.get("$options", "") else 0
                    if not re.search(value["$regex"], str(actual or ""), options):
                        return False
                continue
            if isinstance(actual, list):
                if value not in actual:
                    return False
            elif actual != value:
                return False
        return True

    def find(self, query=None, projection=None):
        query = query or {}
        return [
            document.copy()
            for document in self.documents
            if self.matches(document, query)
        ]

    def find_one(self, query=None, projection=None):
        matches = self.find(query, projection)
        return matches[0] if matches else None

    def insert_one(self, document):
        stored = document.copy()
        if "_id" not in stored:
            stored["_id"] = len(self.documents) + 1
        self.documents.append(stored)
        return type(
            "InsertOneResult",
            (),
            {"inserted_id": stored.get("_id")},
        )()

    def update_one(self, query, update, upsert=False):
        with self._lock:
            document = next(
                (item for item in self.documents if self.matches(item, query)),
                None,
            )
            inserted = False
            if document is None and upsert:
                document = {
                    key: value for key, value in query.items()
                    if not key.startswith("$") and not isinstance(value, dict)
                }
                self.documents.append(document)
                inserted = True
            if document is None:
                return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0, "upserted_id": None})()

            before = document.copy()
            if inserted:
                document.update(update.get("$setOnInsert", {}))
                if "_id" not in document:
                    document["_id"] = len(self.documents)
            document.update(update.get("$set", {}))
            for key, amount in update.get("$inc", {}).items():
                document[key] = document.get(key, 0) + amount
            for key, value in update.get("$addToSet", {}).items():
                current = document.setdefault(key, [])
                if value not in current:
                    current.append(value)
            for key, operation in update.get("$bit", {}).items():
                if "xor" in operation:
                    document[key] = int(document.get(key, 0)) ^ int(operation["xor"])
            return type(
                "UpdateResult",
                (),
                {
                    "matched_count": 1,
                    "modified_count": int(document != before),
                    "upserted_id": document.get("_id") if inserted else None,
                },
            )()

    def update_many(self, query, update):
        with self._lock:
            matches = [item for item in self.documents if self.matches(item, query)]
            for document in matches:
                document.update(update.get("$set", {}))
            return type(
                "UpdateResult",
                (),
                {
                    "matched_count": len(matches),
                    "modified_count": len(matches),
                },
            )()

    def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if self.matches(document, query):
                self.documents.pop(index)
                return type("DeleteResult", (), {"deleted_count": 1})()
        return type("DeleteResult", (), {"deleted_count": 0})()

    def count_documents(self, query=None):
        return len(self.find(query))

    def create_index(self, keys, name, unique=False):
        self.create_index_calls.append(
            {"keys": list(keys), "name": name, "unique": unique}
        )
        self.indexes.append(
            {"name": name, "key": dict(keys), "unique": unique}
        )
        return name

    def list_indexes(self):
        return list(self.indexes)

    def aggregate(self, pipeline):
        group = pipeline[0]["$group"]
        fields = tuple(group["_id"].keys())
        grouped = {}
        for document in self.documents:
            key = tuple(document.get(field) for field in fields)
            grouped.setdefault(key, []).append(document.get("_id"))
        return [
            {
                "_id": dict(zip(fields, key)),
                "count": len(ids),
                "ids": ids,
            }
            for key, ids in grouped.items()
            if len(ids) > 1
        ]

    def find_one_and_update(
        self,
        query,
        update,
        upsert=False,
        return_document=ReturnDocument.BEFORE,
    ):
        if return_document not in (ReturnDocument.BEFORE, ReturnDocument.AFTER):
            raise ValueError(
                "return_document must be ReturnDocument.BEFORE or "
                "ReturnDocument.AFTER"
            )
        with self._lock:
            document = next(
                (item for item in self.documents if self.matches(item, query)),
                None,
            )
            inserted = False
            if document is None and upsert:
                document = {
                    key: value for key, value in query.items()
                    if not key.startswith("$") and not isinstance(value, dict)
                }
                self.documents.append(document)
                inserted = True
            if document is None:
                return None
            before = document.copy()
            if inserted:
                document.update(update.get("$setOnInsert", {}))
                if "_id" not in document:
                    document["_id"] = len(self.documents)
            document.update(update.get("$set", {}))
            for key, amount in update.get("$inc", {}).items():
                document[key] = document.get(key, 0) + amount
            return (
                document.copy()
                if return_document == ReturnDocument.AFTER
                else before
            )


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def list_collection_names(self):
        return list(self.collections)

    def create_collection(self, name):
        self.collections.setdefault(name, FakeCollection())

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class MongoPersistenceTests(unittest.TestCase):
    def make_sqlite(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                referred_by INTEGER,
                total_deposited INTEGER DEFAULT 0,
                joined_date TEXT,
                banned INTEGER DEFAULT 0,
                discount INTEGER DEFAULT 0,
                terms_accepted INTEGER DEFAULT 0
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE stock (
                phone TEXT PRIMARY KEY, session_file TEXT, country_name TEXT,
                country_icon TEXT, account_year INTEGER, category TEXT,
                price INTEGER, available INTEGER, twofa TEXT, added_date TEXT,
                data_center TEXT
            );
            CREATE TABLE auto_prices (
                country TEXT, year TEXT, price INTEGER,
                PRIMARY KEY (country, year)
            );
            CREATE TABLE deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                amount INTEGER, method_name TEXT, status TEXT, date TEXT,
                screenshot TEXT, utr TEXT
            );
            CREATE TABLE upi_orders (
                order_id TEXT PRIMARY KEY, user_id INTEGER, amount INTEGER,
                status TEXT, date TEXT
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                country TEXT, year INTEGER, price INTEGER, phone TEXT,
                otp TEXT, date TEXT
            );
            CREATE TABLE custom_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
                caption TEXT, qr_file_id TEXT
            );
            CREATE TABLE admins (
                user_id INTEGER PRIMARY KEY, p_add_stock INTEGER,
                p_manage_stock INTEGER, p_stats INTEGER, p_bal INTEGER,
                p_settings INTEGER
            );
            CREATE TABLE custom_countries (
                code TEXT PRIMARY KEY, name TEXT, flag TEXT
            );
            """
        )
        connection.commit()
        return path, connection

    def tearDown(self):
        for path in getattr(self, "_paths", []):
            path.unlink(missing_ok=True)

    def test_mapping_keeps_settings_as_strings_and_inventory_fields(self):
        path, connection = self.make_sqlite()
        self._paths = [path]
        connection.execute("INSERT INTO settings VALUES (?, ?)", ("x", "001"))
        connection.execute(
            "INSERT INTO stock VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("+1", "a.session", "US", "🇺🇸", 2024, "Good", 10, 1, "None", "date", "dc1"),
        )
        setting = connection.execute("SELECT * FROM settings").fetchone()
        stock = connection.execute("SELECT * FROM stock").fetchone()
        self.assertEqual(sqlite_row_to_document("settings", setting)["value"], "001")
        self.assertEqual(sqlite_row_to_document("inventory", stock)["data_center"], "dc1")
        connection.close()

    def test_prepare_creates_all_collections_and_indexes(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        report = repository.prepare()
        self.assertEqual(set(database.collections), set(COLLECTION_NAMES))
        self.assertIn("inventory_phone_unique", report["indexes"]["inventory"]["created"])
        self.assertIn(
            "auto_prices_country_year_unique",
            report["indexes"]["auto_prices"]["created"],
        )

    def test_prepare_does_not_create_builtin_id_index(self):
        database = FakeDatabase()
        users = database["users"]
        users.indexes = []

        repository = MongoRepository(database)
        report = repository.prepare()

        self.assertIn("_id_", report["indexes"]["users"]["verified"])
        self.assertNotIn(
            "_id_",
            [call["name"] for call in users.create_index_calls],
        )
        self.assertIn(
            "users_referred_by",
            [call["name"] for call in users.create_index_calls],
        )

    def test_duplicate_unique_keys_are_reported_and_index_is_not_created(self):
        database = FakeDatabase()
        collection = database["inventory"]
        collection.documents.extend(
            [{"_id": "a", "phone": "+1"}, {"_id": "b", "phone": "+1"}]
        )
        repository = MongoRepository(database)
        report = repository.prepare()
        self.assertIn(
            "inventory_phone_unique",
            report["indexes"]["inventory"]["skipped_duplicates"],
        )
        self.assertIn("inventory", report["duplicates"])

    def test_migration_is_insert_only_and_preserves_extra_mongo_fields(self):
        path, connection = self.make_sqlite()
        self._paths = [path]
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (7, 25, None, 25, "date", 0, 0, 1),
        )
        connection.execute("INSERT INTO settings VALUES (?, ?)", ("banner", "photo"))
        connection.execute(
            "INSERT INTO stock VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("+1", "a.session", "US", "🇺🇸", 2024, "Good", 10, 1, "None", "date", None),
        )
        connection.commit()

        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        database["users"].documents.append(
            {
                "_id": 7,
                "balance": 25,
                "referred_by": None,
                "total_deposited": 25,
                "joined_date": "date",
                "banned": 0,
                "discount": 0,
                "terms_accepted": 1,
                "future_field": "keep-me",
            }
        )
        report = SQLiteMongoMigrator(repository, connection).migrate()
        self.assertEqual(report["collections"]["users"]["identical"], 1)
        self.assertEqual(report["collections"]["settings"]["inserted"], 1)
        self.assertEqual(report["collections"]["inventory"]["inserted"], 1)
        self.assertEqual(database["users"].documents[0]["future_field"], "keep-me")
        self.assertEqual(report["sqlite_sequence"], "not migrated")
        connection.close()

    def test_conflict_is_reported_without_overwrite(self):
        path, connection = self.make_sqlite()
        self._paths = [path]
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (7, 25, None, 25, "date", 0, 0, 1),
        )
        connection.commit()
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        database["users"].documents.append({"_id": 7, "balance": 999})
        report = SQLiteMongoMigrator(repository, connection).migrate()
        self.assertEqual(report["collections"]["users"]["conflicts"], 1)
        self.assertEqual(database["users"].documents[0]["balance"], 999)
        connection.close()

    def test_read_only_sqlite_connection_rejects_writes(self):
        path, connection = self.make_sqlite()
        self._paths = [path]
        connection.close()
        read_only = sqlite_connection_read_only(path)
        with self.assertRaises(sqlite3.OperationalError):
            read_only.execute("CREATE TABLE should_not_exist (id INTEGER)")
        read_only.close()

    def test_counter_initialization_never_lowers_existing_counter(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        self.assertEqual(repository.initialize_counter("orders", 4)["status"], "inserted")
        self.assertEqual(repository.initialize_counter("orders", 4)["status"], "identical")
        result = repository.initialize_counter("orders", 9)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(database["counters"].find_one({"_id": "orders"})["value"], 4)
        self.assertEqual(repository.allocate_counter("orders"), 5)

    def test_runtime_store_preserves_existing_user_fields_and_uses_string_settings(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        store = MongoRuntimeStore(repository)

        store.users.documents.append({"_id": 7, "balance": 900, "discount": 5})
        store.ensure_user(7)
        self.assertEqual(store.get_user(7)["balance"], 900)
        self.assertEqual(store.get_user(7)["discount"], 5)

        store.ensure_user(8)
        self.assertEqual(store.get_user(8)["balance"], 0)
        store.set_setting("usdt_rate", 83.5)
        self.assertEqual(store.get_setting("usdt_rate"), "83.5")

    def test_start_and_buy_account_banners_are_independent_and_persisted(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        store = MongoRuntimeStore(repository)

        store.set_setting("banner_photo", "start-banner-reference")
        store.set_setting("buy_account_banner_file_id", "buy-account-banner-reference")

        restarted_store = MongoRuntimeStore(MongoRepository(database))
        self.assertEqual(
            restarted_store.get_setting("banner_photo"),
            "start-banner-reference",
        )
        self.assertEqual(
            restarted_store.get_setting("buy_account_banner_file_id"),
            "buy-account-banner-reference",
        )

        restarted_store.delete_setting("buy_account_banner_file_id")
        self.assertIsNone(restarted_store.get_setting("buy_account_banner_file_id"))
        self.assertEqual(
            restarted_store.get_setting("banner_photo"),
            "start-banner-reference",
        )

    def test_editable_terms_settings_persist_without_changing_terms_url(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        store = MongoRuntimeStore(repository)

        store.set_setting("terms_url", "https://example.test/terms")
        store.set_setting("editable_terms_text", "Exact T&C text <keep>")
        store.set_setting(
            "editable_terms_banner_file_id",
            '{"id": 123, "access_hash": 456, "file_reference": "00"}',
        )

        restarted_store = MongoRuntimeStore(MongoRepository(database))
        self.assertEqual(restarted_store.get_setting("terms_url"), "https://example.test/terms")
        self.assertEqual(
            restarted_store.get_setting("editable_terms_text"),
            "Exact T&C text <keep>",
        )
        self.assertEqual(
            restarted_store.get_setting("editable_terms_banner_file_id"),
            '{"id": 123, "access_hash": 456, "file_reference": "00"}',
        )

    def test_bot_runtime_does_not_initialize_sqlite(self):
        source = Path("james.py").read_text(encoding="utf-8")
        self.assertNotIn("import sqlite3", source)
        self.assertNotIn('sqlite3.connect("otp_bot_final.db"', source)
        self.assertNotIn("def setup_db", source)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS", source)

    def test_buy_account_banner_display_has_safe_fallback_without_banner(self):
        source = Path("james.py").read_text(encoding="utf-8")
        start_banner_start = source.index("def get_banner_media")
        start_banner_end = source.index("\n\ndef get_banner_reference", start_banner_start)
        start_banner_helpers = source[start_banner_start:start_banner_end]
        self.assertIn('get_setting("banner_photo")', start_banner_helpers)

        start_menu_start = source.index("async def send_main_menu")
        start_menu_end = source.index("\nasync def ", start_menu_start + 10)
        start_menu = source[start_menu_start:start_menu_end]
        self.assertIn("get_banner_media()", start_menu)
        self.assertIn('get_setting("images_enabled", "off") == "on"', start_menu)

        render_start = source.index("async def render_account_store")
        render_end = source.index("\nasync def show_product_details", render_start)
        render_account_store = source[render_start:render_end]

        self.assertIn(
            'BUY_ACCOUNT_BANNER_SETTING = "buy_account_banner_file_id"',
            source,
        )
        self.assertIn(
            'buy_account_banner = get_buy_account_banner() if flow == "single" else None',
            render_account_store,
        )
        self.assertIn(
            "if send_banner and buy_account_banner:",
            render_account_store,
        )
        self.assertIn(
            "await bot.send_file(event.chat_id, buy_account_banner, caption=caption, buttons=buttons)",
            render_account_store,
        )
        self.assertIn("if len(caption) <= 1024:", render_account_store)
        self.assertNotIn(
            "await bot.send_file(event.chat_id, buy_account_banner)",
            render_account_store,
        )
        self.assertIn(
            "await event.respond(caption, buttons=buttons)",
            render_account_store,
        )

    def test_stats_profile_and_deposit_banners_are_composed_with_existing_ui(self):
        source = Path("james.py").read_text(encoding="utf-8")

        for setting in (
            'MY_STATS_BANNER_SETTING = "my_stats_banner_file_id"',
            'MY_PROFILE_BANNER_SETTING = "my_profile_banner_file_id"',
            'DEPOSIT_BANNER_SETTING = "deposit_banner_file_id"',
        ):
            self.assertIn(setting, source)
        self.assertIn(
            "await bot.send_file(event.chat_id, banner, caption=message, buttons=buttons)",
            source,
        )
        self.assertIn("if not banner or len(message) > 1024:", source)

        deposit_start = source.index("async def deposit_menu")
        deposit_end = source.index("\ndef get_keypad", deposit_start)
        deposit_handler = source[deposit_start:deposit_end]
        self.assertIn(
            "send_bannered_message(event, DEPOSIT_BANNER_SETTING, msg, btns)",
            deposit_handler,
        )
        self.assertIn('"dep_upi"', deposit_handler)
        self.assertIn("await bot.send_message(event.chat_id, msg, buttons=btns)", deposit_handler)

        profile_start = source.index("async def profile_handler")
        profile_end = source.index("\nasync def stats_handler", profile_start)
        profile_handler = source[profile_start:profile_end]
        self.assertIn(
            "send_bannered_message(event, MY_PROFILE_BANNER_SETTING, msg)",
            profile_handler,
        )
        self.assertIn("await bot.send_message(event.chat_id, msg)", profile_handler)

        stats_start = source.index("async def stats_handler")
        stats_end = source.index("\nasync def send_purchase_page", stats_start)
        stats_handler = source[stats_start:stats_end]
        self.assertIn(
            "send_bannered_message(event, MY_STATS_BANNER_SETTING, msg, btns)",
            stats_handler,
        )
        self.assertIn('"page_purchases_1"', stats_handler)
        self.assertIn('"view_referrals"', stats_handler)
        self.assertIn("await event.edit(msg, buttons=btns)", stats_handler)

        banner_menu_start = source.index("async def banner_manager_menu")
        banner_menu_end = source.index("\nasync def store_settings_menu", banner_menu_start)
        banner_menu = source[banner_menu_start:banner_menu_end]
        for label in ("My Stats Banner", "My Profile Banner", "Deposit Banner"):
            self.assertIn(label, banner_menu)

    def test_all_banner_settings_are_independent_and_persisted(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        store = MongoRuntimeStore(repository)
        settings = {
            "banner_photo": "start-banner-reference",
            "buy_account_banner_file_id": "buy-account-banner-reference",
            "my_stats_banner_file_id": "stats-banner-reference",
            "my_profile_banner_file_id": "profile-banner-reference",
            "deposit_banner_file_id": "deposit-banner-reference",
        }
        for key, value in settings.items():
            store.set_setting(key, value)

        restarted_store = MongoRuntimeStore(MongoRepository(database))
        for key, value in settings.items():
            self.assertEqual(restarted_store.get_setting(key), value)

        restarted_store.set_setting("my_stats_banner_file_id", "updated-stats-banner")
        self.assertEqual(
            restarted_store.get_setting("my_stats_banner_file_id"),
            "updated-stats-banner",
        )
        for key, value in settings.items():
            if key != "my_stats_banner_file_id":
                self.assertEqual(restarted_store.get_setting(key), value)

    def test_runtime_store_handles_admins_custom_countries_and_payments(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        store = MongoRuntimeStore(repository)

        store.add_admin(42)
        self.assertTrue(store.is_admin(42))
        self.assertFalse(store.has_permission(42, "p_stats"))
        store.toggle_permission(42, "p_stats")
        self.assertTrue(store.has_permission(42, "p_stats"))

        store.save_custom_country("999", "Testland", "🏳️")
        self.assertEqual(store.custom_country_by_name("Testland")["flag"], "🏳️")
        payment_id = store.add_custom_payment("Test Pay", "<code>pay</code>", "")
        self.assertEqual(store.get_custom_payment("Test Pay")["_id"], payment_id)
        updated_id = store.add_custom_payment("Test Pay", "updated", '{"id": 123}')
        self.assertEqual(updated_id, payment_id)
        self.assertEqual(store.get_custom_payment("Test Pay")["qr_file_id"], '{"id": 123}')
        self.assertEqual(store.custom_payments.count_documents({"name": "Test Pay"}), 1)
        store.delete_custom_payment(payment_id)
        self.assertIsNone(store.get_custom_payment("Test Pay"))

    def test_atomic_balance_deduction_and_insufficient_balance(self):
        _database, store = self.make_store()
        store.ensure_user(21)
        store.set_user_fields(21, {"balance": 100})

        self.assertTrue(store.deduct_balance(21, 70, "debit-one", "test_debit"))
        self.assertFalse(store.deduct_balance(21, 31, "debit-two", "test_debit"))
        self.assertEqual(store.get_balance(21), 30)

    def test_concurrent_balance_deductions_have_only_affordable_winners(self):
        _database, store = self.make_store()
        store.ensure_user(22)
        store.set_user_fields(22, {"balance": 100})
        barrier = threading.Barrier(4)
        results = []

        def deduct(index):
            barrier.wait()
            results.append(
                store.deduct_balance(
                    22,
                    30,
                    f"concurrent-debit-{index}",
                    "test_debit",
                )
            )

        threads = [threading.Thread(target=deduct, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(results), 3)
        self.assertEqual(store.get_balance(22), 10)

    def test_atomic_balance_credit_is_idempotent_and_preserves_related_increment(self):
        _database, store = self.make_store()
        store.ensure_user(23)
        first = store.credit_balance(
            23,
            50,
            "deposit:10:approval",
            "deposit_approval",
            extra_inc={"total_deposited": 50},
        )
        second = store.credit_balance(
            23,
            50,
            "deposit:10:approval",
            "deposit_approval",
            extra_inc={"total_deposited": 50},
        )

        self.assertTrue(first["applied"])
        self.assertTrue(second["already_applied"])
        self.assertEqual(store.get_balance(23), 50)
        self.assertEqual(store.get_user(23)["total_deposited"], 50)
        self.assertEqual(
            store.balance_ledger.count_documents({"_id": "deposit:10:approval"}),
            1,
        )
        with self.assertRaises(ValueError):
            store.credit_balance(
                23,
                60,
                "deposit:10:approval",
                "deposit_approval",
            )

    def test_concurrent_duplicate_balance_credit_has_one_winner(self):
        _database, store = self.make_store()
        store.ensure_user(24)
        barrier = threading.Barrier(5)
        results = []

        def credit():
            barrier.wait()
            results.append(
                store.credit_balance(
                    24,
                    25,
                    "refund:inventory:one",
                    "refund",
                )
            )

        threads = [threading.Thread(target=credit) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(result["applied"] for result in results), 1)
        self.assertEqual(store.get_balance(24), 25)

    def test_deposit_creation_allocates_numeric_ids_and_preserves_fields(self):
        _database, store = self.make_store()
        store.repository.initialize_counter("deposits", 40)
        first = store.create_deposit(
            25,
            100,
            "UPI",
            date="2026-09-03 10:00:00",
            screenshot="screenshots/a.jpg",
            utr="UTR12345678",
        )
        second = store.create_deposit(25, 200, "Manual")

        self.assertEqual(first["_id"], 41)
        self.assertEqual(second["_id"], 42)
        self.assertEqual(store.get_deposit(41)["utr"], "UTR12345678")
        self.assertEqual(store.get_deposit(41)["status"], "pending")
        self.assertEqual(store.get_deposit(42)["amount"], 200)

    def test_deposit_approval_transition_and_credit_are_idempotent(self):
        _database, store = self.make_store()
        store.ensure_user(26)
        deposit = store.create_deposit(26, 75, "UPI")

        first = store.approve_deposit(deposit["_id"], 75)
        second = store.approve_deposit(deposit["_id"], 75)

        self.assertTrue(first["credited"])
        self.assertFalse(second["credited"])
        self.assertEqual(store.get_balance(26), 75)
        self.assertEqual(store.get_user(26)["total_deposited"], 75)
        self.assertEqual(store.get_deposit(deposit["_id"])["status"], "approved")

    def test_deposit_rejection_transition_is_idempotent(self):
        _database, store = self.make_store()
        deposit = store.create_deposit(27, 80, "Manual")

        self.assertTrue(store.transition_deposit(deposit["_id"], "pending", "rejected"))
        self.assertFalse(store.transition_deposit(deposit["_id"], "pending", "rejected"))
        self.assertEqual(store.get_deposit(deposit["_id"])["status"], "rejected")
        self.assertEqual(store.get_balance(27), 0)
        self.assertEqual(store.approve_deposit(deposit["_id"], 80)["status"], "processed")
        self.assertEqual(store.get_deposit(deposit["_id"])["status"], "rejected")

    def test_upi_order_creation_is_idempotent_and_conflict_safe(self):
        _database, store = self.make_store()
        first = store.create_upi_order("ORDER_28_1", 28, 125)
        retry = store.create_upi_order("ORDER_28_1", 28, 125)

        self.assertEqual(first, retry)
        with self.assertRaises(ValueError):
            store.create_upi_order("ORDER_28_1", 29, 125)
        self.assertEqual(store.upi_orders.count_documents({}), 1)

    def test_single_purchase_reservation_can_match_legacy_category_and_dc(self):
        _database, store = self.make_store()
        store.save_inventory(self.inventory_document("9199990006", category="Good", dc="dc1"))

        reserved = store.reserve_inventory_item(
            "India",
            2024,
            100,
            "Standard",
            None,
            match_any_category=True,
            match_any_dc=True,
        )

        self.assertEqual(reserved["phone"], "9199990006")
        self.assertEqual(reserved["available"], 0)
        self.assertEqual(store.count_inventory({"available": 1}), 0)

    def test_runtime_order_creation_preserves_numeric_ids_and_is_retry_safe(self):
        _database, store = self.make_store()
        store.repository.initialize_counter("orders", 40)

        first = store.create_order(
            29,
            "India",
            2024,
            100,
            "9199990007",
            "12345",
            purchase_key="purchase:single:9199990007",
            date="2026-09-03 10:00:00",
        )
        retry = store.create_order(
            29,
            "India",
            2024,
            100,
            "9199990007",
            "12345",
            purchase_key="purchase:single:9199990007",
        )

        self.assertEqual(first["_id"], 41)
        self.assertEqual(retry["_id"], 41)
        self.assertEqual(store.orders.count_documents({}), 1)
        self.assertEqual(store.create_order(
            29, "India", 2024, 100, "9199990008", "SESSION_FILES"
        )["_id"], 42)

    def test_purchase_callback_claim_is_idempotent(self):
        _database, store = self.make_store()

        self.assertTrue(store.claim_purchase_callback(30, 99, 501))
        self.assertFalse(store.claim_purchase_callback(30, 99, 501))
        self.assertTrue(store.claim_purchase_callback(30, 99, 502))
        self.assertEqual(store.pending_workflows.count_documents({}), 2)

    def test_duplicate_buy_now_callback_does_not_double_charge_or_create_order(self):
        _database, store = self.make_store()
        store.ensure_user(31)
        store.set_user_fields(31, {"balance": 100})
        store.save_inventory(self.inventory_document("9199990009"))

        def process_callback():
            if not store.claim_purchase_callback(31, 99, 601):
                return
            reserved = store.reserve_inventory_item("India", 2024, 100, "Good")
            self.assertIsNotNone(reserved)
            phone = reserved["phone"]
            purchase_key = "purchase:single:31:99:601"
            self.assertTrue(
                store.deduct_balance(
                    31,
                    100,
                    event_key=f"{purchase_key}:debit",
                    event_type="purchase_debit",
                )
            )
            store.create_order(
                31,
                "India",
                2024,
                100,
                phone,
                "12345",
                purchase_key=purchase_key,
            )

        process_callback()
        process_callback()

        self.assertEqual(store.get_balance(31), 0)
        self.assertEqual(store.orders.count_documents({}), 1)
        self.assertEqual(store.count_inventory({"available": 0}), 1)

    def test_separate_single_purchases_have_distinct_debit_events(self):
        _database, store = self.make_store()
        store.ensure_user(32)
        store.set_user_fields(32, {"balance": 200})
        store.save_inventory(self.inventory_document("9199990010"))
        store.save_inventory(self.inventory_document("9199990011"))

        first = store.reserve_inventory_item("India", 2024, 100, "Good")
        second = store.reserve_inventory_item("India", 2024, 100, "Good")
        self.assertNotEqual(first["phone"], second["phone"])

        self.assertTrue(
            store.deduct_balance(32, 100, "purchase:single:32:99:701:debit", "purchase_debit")
        )
        self.assertTrue(
            store.deduct_balance(32, 100, "purchase:single:32:99:702:debit", "purchase_debit")
        )
        self.assertEqual(store.get_balance(32), 0)
        self.assertEqual(store.balance_ledger.count_documents({"user_id": 32}), 2)

    def test_released_stock_can_be_purchased_with_a_new_event_key(self):
        _database, store = self.make_store()
        store.ensure_user(33)
        store.set_user_fields(33, {"balance": 100})
        store.save_inventory(self.inventory_document("9199990012"))

        reserved = store.reserve_inventory_item("India", 2024, 100, "Good")
        self.assertTrue(
            store.deduct_balance(33, 100, "purchase:single:33:99:801:debit", "purchase_debit")
        )
        store.credit_balance(33, 100, "purchase:single:33:99:801:refund", "purchase_refund")
        store.release_inventory([reserved["phone"]])

        replacement = store.reserve_inventory_item("India", 2024, 100, "Good")
        self.assertEqual(replacement["phone"], reserved["phone"])
        self.assertTrue(
            store.deduct_balance(33, 100, "purchase:single:33:99:802:debit", "purchase_debit")
        )
        self.assertEqual(store.get_balance(33), 0)

    def make_store(self):
        database = FakeDatabase()
        repository = MongoRepository(database)
        repository.prepare()
        return database, MongoRuntimeStore(repository)

    @staticmethod
    def inventory_document(phone, *, available=1, category="Good", dc=None, price=100):
        return {
            "phone": phone,
            "session_file": f"sessions/{phone}.session",
            "country_name": "India",
            "country_icon": "🇮🇳",
            "account_year": 2024,
            "category": category,
            "price": price,
            "available": available,
            "twofa": "None",
            "data_center": dc,
            "added_date": "2026-09-03 00:00:00",
        }

    def test_inventory_crud_preserves_extra_fields_and_invalid_records_fail(self):
        _database, store = self.make_store()
        document = self.inventory_document("9199990001")
        document["provider_metadata"] = {"source": "legacy"}
        store.save_inventory(document)

        store.inventory.documents[0]["provider_metadata"] = {"source": "legacy"}
        document["price"] = 125
        store.save_inventory(document)
        stored = store.inventory.find_one({"phone": "9199990001"})
        self.assertEqual(stored["price"], 125)
        self.assertEqual(stored["provider_metadata"], {"source": "legacy"})
        self.assertTrue(store.set_inventory_available("9199990001", 0))
        self.assertEqual(store.count_inventory({"available": 0}), 1)
        self.assertEqual(store.delete_inventory("9199990001"), 1)
        self.assertEqual(store.count_inventory(), 0)
        with self.assertRaises(ValueError):
            store.save_inventory({"session_file": "missing-phone.session"})

    def test_inventory_product_lookup_and_grouping(self):
        _database, store = self.make_store()
        store.save_inventory(self.inventory_document("9199990002"))
        store.save_inventory(self.inventory_document("9199990003", dc="dc2"))
        store.save_inventory(self.inventory_document("9199990004", available=0))

        products = store.inventory_products()
        self.assertEqual(len(products), 2)
        self.assertEqual({product["dc"] for product in products}, {None, "dc2"})
        self.assertEqual(store.count_inventory(
            store.inventory_filter(
                country="India",
                year=2024,
                price=100,
                category="Good",
                dc=None,
                available=1,
                country_prefix=False,
            )
        ), 1)
        self.assertEqual(store.list_inventory_countries(), ["India"])
        self.assertEqual(store.list_inventory_years("India"), [2024])

    def test_auto_price_crud_and_upsert(self):
        _database, store = self.make_store()
        store.set_auto_price("India", "2024", 150)
        self.assertEqual(store.get_auto_price("India", 2024), 150)
        store.auto_prices.documents[0]["extra"] = "kept"
        store.set_auto_price("India", "2024", 175)
        self.assertEqual(
            store.auto_prices.count_documents({"country": "India", "year": "2024"}),
            1,
        )
        self.assertEqual(store.get_auto_price("India", "2024"), 175)
        self.assertEqual(store.auto_prices.documents[0]["extra"], "kept")
        self.assertEqual(store.delete_auto_price("India", "2024"), 1)
        self.assertIsNone(store.get_auto_price("India", "2024"))

    def test_auto_price_country_rename_preserves_conflicting_target(self):
        _database, store = self.make_store()
        store.set_auto_price("Oldland", "2024", 100)
        store.set_auto_price("Oldland", "2023", 90)
        store.set_auto_price("Newland", "2024", 200)

        result = store.rename_auto_price_country("Oldland", "Newland")

        self.assertEqual(result, {"updated": 1, "conflicts": 1})
        self.assertEqual(store.get_auto_price("Newland", "2024"), 200)
        self.assertEqual(store.get_auto_price("Newland", "2023"), 90)
        self.assertEqual(store.get_auto_price("Oldland", "2024"), 100)

    def test_atomic_single_item_reservation_allows_only_one_winner(self):
        _database, store = self.make_store()
        store.save_inventory(self.inventory_document("9199990005"))
        barrier = threading.Barrier(2)
        results = []

        def reserve():
            barrier.wait()
            results.append(store.reserve_inventory_item("India", 2024, 100, "Good"))

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["phone"], "9199990005")
        self.assertEqual(store.count_inventory({"available": 1}), 0)

    def test_bulk_reservation_is_concurrency_safe_and_releases_partial_attempts(self):
        _database, store = self.make_store()
        for index in range(5):
            store.save_inventory(self.inventory_document(f"919999001{index}"))
        barrier = threading.Barrier(2)
        results = []

        def reserve_bulk():
            barrier.wait()
            results.append(store.reserve_inventory(3, "India", 2024, 100, "Good"))

        threads = [threading.Thread(target=reserve_bulk) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        completed = [result for result in results if result]
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(completed[0]), 3)
        reserved_phones = {item["phone"] for item in completed[0]}
        self.assertEqual(len(reserved_phones), 3)
        self.assertEqual(store.count_inventory({"available": 0}), 3)
        self.assertEqual(store.count_inventory({"available": 1}), 2)


if __name__ == "__main__":
    unittest.main()