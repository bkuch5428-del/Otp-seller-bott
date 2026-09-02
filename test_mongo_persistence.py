import sqlite3
import tempfile
import unittest
from pathlib import Path

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

    @staticmethod
    def matches(document, query):
        for key, value in (query or {}).items():
            if key == "$or":
                if not any(FakeCollection.matches(document, item) for item in value):
                    return False
                continue
            actual = document.get(key)
            if isinstance(value, dict):
                if "$ne" in value and actual == value["$ne"]:
                    return False
                if "$exists" in value and (key in document) != value["$exists"]:
                    return False
                continue
            if actual != value:
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
        document.update(update.get("$set", {}))
        for key, amount in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + amount
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

    def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if self.matches(document, query):
                self.documents.pop(index)
                return type("DeleteResult", (), {"deleted_count": 1})()
        return type("DeleteResult", (), {"deleted_count": 0})()

    def count_documents(self, query=None):
        return len(self.find(query))

    def create_index(self, keys, name, unique=False):
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

    def find_one_and_update(self, query, update, upsert=False, return_document=1):
        document = self.find_one(query)
        if document is None and upsert:
            document = {"_id": query["_id"], "value": 0}
            self.documents.append(document)
        if document is None:
            return None
        document["value"] += update["$inc"]["value"]
        return document.copy()


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
        store.delete_custom_payment(payment_id)
        self.assertIsNone(store.get_custom_payment("Test Pay"))


if __name__ == "__main__":
    unittest.main()