"""MongoDB infrastructure for the staged SQLite-to-MongoDB migration.

This module deliberately does not import or modify the Telegram bot runtime.
It provides:

* the collection and field definitions used by the migration;
* safe collection/index preparation;
* conflict-aware document comparison;
* a non-destructive counter initializer; and
* runtime helpers for the selected user/settings/admin/custom-definition cutover.

Inventory, deposits, orders, and balance mutations remain SQLite-backed until
their dedicated migration phases.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


COLLECTION_NAMES = (
    "users",
    "settings",
    "inventory",
    "auto_prices",
    "deposits",
    "upi_orders",
    "orders",
    "custom_payments",
    "admins",
    "custom_countries",
    "counters",
    "balance_ledger",
    "pending_workflows",
)

COUNTER_TABLES = ("deposits", "orders", "custom_payments")


@dataclass(frozen=True)
class IndexDefinition:
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False


@dataclass(frozen=True)
class CollectionDefinition:
    name: str
    managed_fields: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    identity_fields: tuple[str, ...] = ()


COLLECTION_DEFINITIONS = {
    "users": CollectionDefinition(
        "users",
        managed_fields=(
            "_id",
            "balance",
            "referred_by",
            "total_deposited",
            "joined_date",
            "banned",
            "discount",
            "terms_accepted",
        ),
        required_fields=("_id",),
        identity_fields=("_id",),
    ),
    "settings": CollectionDefinition(
        "settings",
        managed_fields=("_id", "value"),
        required_fields=("_id",),
        identity_fields=("_id",),
    ),
    "inventory": CollectionDefinition(
        "inventory",
        managed_fields=(
            "phone",
            "session_file",
            "country_name",
            "country_icon",
            "account_year",
            "category",
            "price",
            "available",
            "twofa",
            "added_date",
            "data_center",
        ),
        required_fields=("phone", "session_file", "country_name", "price", "available"),
        identity_fields=("phone",),
    ),
    "auto_prices": CollectionDefinition(
        "auto_prices",
        managed_fields=("country", "year", "price"),
        required_fields=("country", "year", "price"),
        identity_fields=("country", "year"),
    ),
    "deposits": CollectionDefinition(
        "deposits",
        managed_fields=(
            "_id",
            "user_id",
            "amount",
            "method_name",
            "status",
            "date",
            "screenshot",
            "utr",
        ),
        required_fields=("_id", "user_id", "amount", "method_name", "status"),
        identity_fields=("_id",),
    ),
    "upi_orders": CollectionDefinition(
        "upi_orders",
        managed_fields=("_id", "order_id", "user_id", "amount", "status", "date"),
        required_fields=("_id", "order_id", "user_id", "amount", "status"),
        identity_fields=("_id",),
    ),
    "orders": CollectionDefinition(
        "orders",
        managed_fields=("_id", "user_id", "country", "year", "price", "phone", "otp", "date"),
        required_fields=("_id", "user_id", "country", "price", "phone"),
        identity_fields=("_id",),
    ),
    "custom_payments": CollectionDefinition(
        "custom_payments",
        managed_fields=("_id", "name", "caption", "qr_file_id"),
        required_fields=("_id", "name", "caption"),
        identity_fields=("_id",),
    ),
    "admins": CollectionDefinition(
        "admins",
        managed_fields=(
            "_id",
            "user_id",
            "p_add_stock",
            "p_manage_stock",
            "p_stats",
            "p_bal",
            "p_settings",
        ),
        required_fields=("_id", "user_id"),
        identity_fields=("_id",),
    ),
    "custom_countries": CollectionDefinition(
        "custom_countries",
        managed_fields=("_id", "code", "name", "flag"),
        required_fields=("_id", "code", "name", "flag"),
        identity_fields=("_id",),
    ),
    # These collections have no SQLite source rows in this phase. They are
    # prepared for later idempotent balance/workflow persistence.
    "counters": CollectionDefinition("counters", managed_fields=("_id", "value")),
    "balance_ledger": CollectionDefinition("balance_ledger"),
    "pending_workflows": CollectionDefinition("pending_workflows"),
}


INDEX_DEFINITIONS = {
    "users": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
        IndexDefinition("users_referred_by", (("referred_by", 1),)),
        IndexDefinition("users_banned", (("banned", 1),)),
    ),
    "settings": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
    ),
    "inventory": (
        IndexDefinition("inventory_phone_unique", (("phone", 1),), unique=True),
        IndexDefinition("inventory_available", (("available", 1),)),
        IndexDefinition(
            "inventory_available_product",
            (
                ("available", 1),
                ("country_name", 1),
                ("account_year", 1),
                ("category", 1),
                ("price", 1),
                ("data_center", 1),
            ),
        ),
    ),
    "auto_prices": (
        IndexDefinition(
            "auto_prices_country_year_unique",
            (("country", 1), ("year", 1)),
            unique=True,
        ),
    ),
    "deposits": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
        IndexDefinition("deposits_user_status", (("user_id", 1), ("status", 1))),
        IndexDefinition("deposits_status_date", (("status", 1), ("date", 1))),
    ),
    "upi_orders": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
        IndexDefinition("upi_orders_user_status", (("user_id", 1), ("status", 1))),
    ),
    "orders": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
        IndexDefinition("orders_user_date", (("user_id", 1), ("date", 1))),
        IndexDefinition("orders_phone", (("phone", 1),)),
    ),
    "custom_payments": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
        IndexDefinition("custom_payments_name", (("name", 1),)),
    ),
    "admins": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
    ),
    "custom_countries": (
        IndexDefinition("_id_", (("_id", 1),), unique=True),
        IndexDefinition("custom_countries_name", (("name", 1),)),
    ),
    "counters": (),
    "balance_ledger": (),
    "pending_workflows": (),
}


def _safe_value(value: Any) -> Any:
    """Return a stable value for reports without changing stored values."""
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    try:
        import json

        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _index_keys(index: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    raw_keys = index.get("key", {})
    if hasattr(raw_keys, "items"):
        return tuple((str(key), int(value)) for key, value in raw_keys.items())
    return tuple((str(key), int(value)) for key, value in raw_keys)


def _has_value(document: Mapping[str, Any], field: str) -> bool:
    value = document.get(field)
    return value is not None and value != ""


def validate_document(collection_name: str, document: Mapping[str, Any]) -> list[str]:
    """Validate only fields required for safe migration, not Mongo schema."""
    definition = COLLECTION_DEFINITIONS[collection_name]
    return [
        field
        for field in definition.required_fields
        if not _has_value(document, field)
    ]


def managed_projection(collection_name: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """Compare source-owned fields while ignoring existing Mongo extras."""
    fields = COLLECTION_DEFINITIONS[collection_name].managed_fields
    return {field: document.get(field) for field in fields if field in document}


def sqlite_connection_read_only(path: str | Path) -> sqlite3.Connection:
    """Open an SQLite file without permitting writes through this connection."""
    database_path = Path(path).expanduser().resolve()
    if not database_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    uri = f"{database_path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def sqlite_row_to_document(collection_name: str, row: sqlite3.Row) -> dict[str, Any]:
    """Map one current SQLite row without converting setting values."""
    if collection_name == "users":
        return {
            "_id": _row_value(row, "user_id"),
            "balance": _row_value(row, "balance"),
            "referred_by": _row_value(row, "referred_by"),
            "total_deposited": _row_value(row, "total_deposited"),
            "joined_date": _row_value(row, "joined_date"),
            "banned": _row_value(row, "banned"),
            "discount": _row_value(row, "discount"),
            "terms_accepted": _row_value(row, "terms_accepted"),
        }
    if collection_name == "settings":
        return {"_id": _row_value(row, "key"), "value": _row_value(row, "value")}
    if collection_name == "inventory":
        document = {
            "phone": _row_value(row, "phone"),
            "session_file": _row_value(row, "session_file"),
            "country_name": _row_value(row, "country_name"),
            "country_icon": _row_value(row, "country_icon") or "🌍",
            "account_year": _row_value(row, "account_year"),
            "category": _row_value(row, "category") or "Good",
            "price": (
                int(_row_value(row, "price"))
                if _row_value(row, "price") is not None
                else 0
            ),
            "available": (
                int(_row_value(row, "available"))
                if _row_value(row, "available") is not None
                else 1
            ),
            "twofa": _row_value(row, "twofa") or "None",
            "added_date": _row_value(row, "added_date"),
        }
        data_center = _row_value(row, "data_center")
        if data_center is not None:
            document["data_center"] = data_center
        return document
    if collection_name == "auto_prices":
        return {
            "country": _row_value(row, "country"),
            "year": _row_value(row, "year"),
            "price": _row_value(row, "price"),
        }
    if collection_name == "deposits":
        return {
            "_id": _row_value(row, "id"),
            "user_id": _row_value(row, "user_id"),
            "amount": _row_value(row, "amount"),
            "method_name": _row_value(row, "method_name"),
            "status": _row_value(row, "status"),
            "date": _row_value(row, "date"),
            "screenshot": _row_value(row, "screenshot"),
            "utr": _row_value(row, "utr"),
        }
    if collection_name == "upi_orders":
        order_id = _row_value(row, "order_id")
        return {
            "_id": order_id,
            "order_id": order_id,
            "user_id": _row_value(row, "user_id"),
            "amount": _row_value(row, "amount"),
            "status": _row_value(row, "status"),
            "date": _row_value(row, "date"),
        }
    if collection_name == "orders":
        return {
            "_id": _row_value(row, "id"),
            "user_id": _row_value(row, "user_id"),
            "country": _row_value(row, "country"),
            "year": _row_value(row, "year"),
            "price": _row_value(row, "price"),
            "phone": _row_value(row, "phone"),
            "otp": _row_value(row, "otp"),
            "date": _row_value(row, "date"),
        }
    if collection_name == "custom_payments":
        return {
            "_id": _row_value(row, "id"),
            "name": _row_value(row, "name"),
            "caption": _row_value(row, "caption"),
            "qr_file_id": _row_value(row, "qr_file_id"),
        }
    if collection_name == "admins":
        user_id = _row_value(row, "user_id")
        return {
            "_id": user_id,
            "user_id": user_id,
            "p_add_stock": _row_value(row, "p_add_stock"),
            "p_manage_stock": _row_value(row, "p_manage_stock"),
            "p_stats": _row_value(row, "p_stats"),
            "p_bal": _row_value(row, "p_bal"),
            "p_settings": _row_value(row, "p_settings"),
        }
    if collection_name == "custom_countries":
        code = _row_value(row, "code")
        return {
            "_id": code,
            "code": code,
            "name": _row_value(row, "name"),
            "flag": _row_value(row, "flag"),
        }
    raise KeyError(f"SQLite rows are not sourced for collection: {collection_name}")


def _identity_filter(collection_name: str, document: Mapping[str, Any]) -> dict[str, Any]:
    identity_fields = COLLECTION_DEFINITIONS[collection_name].identity_fields
    return {field: document[field] for field in identity_fields}


class MongoRepository:
    """Collection access and safe index preparation for the future cutover."""

    def __init__(self, database: Any, inventory_collection_name: str = "inventory"):
        self.database = database
        self.inventory_collection_name = inventory_collection_name or "inventory"

    def actual_collection_name(self, logical_name: str) -> str:
        if logical_name == "inventory":
            return self.inventory_collection_name
        return logical_name

    def collection(self, logical_name: str) -> Any:
        if logical_name not in COLLECTION_NAMES:
            raise KeyError(f"Unknown Mongo collection: {logical_name}")
        return self.database[self.actual_collection_name(logical_name)]

    def collections(self) -> dict[str, Any]:
        return {name: self.collection(name) for name in COLLECTION_NAMES}

    def _ensure_collection(self, logical_name: str, existing_names: set[str]) -> dict[str, Any]:
        actual_name = self.actual_collection_name(logical_name)
        created = False
        if actual_name not in existing_names:
            try:
                self.database.create_collection(actual_name)
                created = True
            except Exception as exc:
                # A concurrent creator may have won the race. Do not drop or
                # replace anything; report other creation failures.
                if "already exists" not in str(exc).lower():
                    return {"name": actual_name, "created": False, "error": str(exc)}
        return {"name": actual_name, "created": created}

    def duplicate_groups(
        self,
        logical_name: str,
        fields: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Find duplicate keys before a unique index is requested."""
        fields = tuple(fields)
        if fields == ("_id",):
            # MongoDB always has a unique _id index.
            return []
        collection = self.collection(logical_name)
        group_id = {field: f"${field}" for field in fields}
        pipeline = [
            {
                "$group": {
                    "_id": group_id,
                    "count": {"$sum": 1},
                    "ids": {"$push": "$_id"},
                }
            },
            {"$match": {"count": {"$gt": 1}}},
        ]
        return [_safe_value(group) for group in collection.aggregate(pipeline)]

    def _list_indexes(self, collection: Any) -> list[Mapping[str, Any]]:
        return list(collection.list_indexes())

    def _index_is_present(
        self,
        indexes: Iterable[Mapping[str, Any]],
        definition: IndexDefinition,
    ) -> bool:
        for index in indexes:
            if _index_keys(index) != definition.keys:
                continue
            if definition.unique and not bool(index.get("unique", False)):
                continue
            return True
        return False

    @staticmethod
    def _is_builtin_id_index(definition: IndexDefinition) -> bool:
        return definition.keys == (("_id", 1),)

    def prepare(self) -> dict[str, Any]:
        """Create missing collections and safe indexes, never deleting data."""
        try:
            existing_names = set(self.database.list_collection_names())
        except Exception:
            existing_names = set()

        collection_report = {}
        for logical_name in COLLECTION_NAMES:
            collection_report[logical_name] = self._ensure_collection(
                logical_name, existing_names
            )

        index_report: dict[str, Any] = {}
        duplicate_report: dict[str, Any] = {}
        for logical_name, definitions in INDEX_DEFINITIONS.items():
            collection = self.collection(logical_name)
            try:
                indexes = self._list_indexes(collection)
            except Exception as exc:
                index_report[logical_name] = {"error": str(exc)}
                continue

            created = []
            verified = []
            skipped_duplicates = []
            errors = []
            for definition in definitions:
                if self._is_builtin_id_index(definition):
                    # MongoDB creates this unique index for every collection.
                    # It must not be sent to create_index with unique=True.
                    verified.append(definition.name)
                    continue
                if self._index_is_present(indexes, definition):
                    verified.append(definition.name)
                    continue

                duplicates = []
                if definition.unique:
                    try:
                        duplicates = self.duplicate_groups(
                            logical_name, (key for key, _direction in definition.keys)
                        )
                    except Exception as exc:
                        errors.append(f"{definition.name}: duplicate check failed: {exc}")
                        continue
                    if duplicates:
                        duplicate_report.setdefault(logical_name, {})[definition.name] = duplicates
                        skipped_duplicates.append(definition.name)
                        continue

                try:
                    collection.create_index(
                        list(definition.keys),
                        name=definition.name,
                        unique=definition.unique,
                    )
                    created.append(definition.name)
                    indexes = self._list_indexes(collection)
                    if not self._index_is_present(indexes, definition):
                        errors.append(f"{definition.name}: not visible after creation")
                except Exception as exc:
                    errors.append(f"{definition.name}: {exc}")

            index_report[logical_name] = {
                "verified": verified,
                "created": created,
                "skipped_duplicates": skipped_duplicates,
                "errors": errors,
            }

        return {
            "collections": collection_report,
            "indexes": index_report,
            "duplicates": duplicate_report,
        }

    def initialize_counter(
        self,
        counter_name: str,
        sqlite_max_value: int,
    ) -> dict[str, Any]:
        """Insert a missing counter, but never lower or overwrite one."""
        collection = self.collection("counters")
        existing = collection.find_one({"_id": counter_name})
        if existing is None:
            document = {
                "_id": counter_name,
                "value": int(sqlite_max_value),
                "source": "sqlite_max_id",
            }
            collection.insert_one(document)
            return {"status": "inserted", "value": int(sqlite_max_value)}

        existing_value = existing.get("value")
        if not isinstance(existing_value, int):
            return {"status": "conflict", "existing": _safe_value(existing)}
        if existing_value < sqlite_max_value:
            return {
                "status": "conflict",
                "existing_value": existing_value,
                "sqlite_max_value": int(sqlite_max_value),
                "reason": "existing counter is below migrated SQLite maximum",
            }
        return {"status": "identical", "value": existing_value}

    def allocate_counter(self, counter_name: str) -> int:
        """Atomically allocate the next value for future numeric IDs."""
        result = self.collection("counters").find_one_and_update(
            {"_id": counter_name},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=1,  # pymongo ReturnDocument.AFTER
        )
        if not result or not isinstance(result.get("value"), int):
            raise RuntimeError(f"Counter did not return an integer: {counter_name}")
        return int(result["value"])


class MongoRuntimeStore:
    """Runtime accessors for the collections migrated in the first cutover.

    This intentionally covers user metadata, settings, admin permissions,
    custom countries, and custom payment definitions only. Inventory,
    deposits, orders, and balance mutations remain in the legacy SQLite
    runtime until their dedicated migration phases.
    """

    ADMIN_PERMISSIONS = {
        "p_add_stock",
        "p_manage_stock",
        "p_stats",
        "p_bal",
        "p_settings",
    }

    USER_DEFAULTS = {
        "balance": 0,
        "referred_by": None,
        "total_deposited": 0,
        "banned": 0,
        "joined_date": None,
        "discount": 0,
        "terms_accepted": 0,
    }

    def __init__(self, repository: MongoRepository):
        self.repository = repository

    @property
    def users(self) -> Any:
        return self.repository.collection("users")

    @property
    def settings(self) -> Any:
        return self.repository.collection("settings")

    @property
    def admins(self) -> Any:
        return self.repository.collection("admins")

    @property
    def custom_countries(self) -> Any:
        return self.repository.collection("custom_countries")

    @property
    def custom_payments(self) -> Any:
        return self.repository.collection("custom_payments")

    def ensure_user(self, user_id: int) -> None:
        """Create a user only when absent; never replace existing fields."""
        self.users.update_one(
            {"_id": int(user_id)},
            {"$setOnInsert": dict(self.USER_DEFAULTS, _id=int(user_id))},
            upsert=True,
        )

    def get_user(self, user_id: int, projection: Mapping[str, int] | None = None) -> Mapping[str, Any] | None:
        return self.users.find_one({"_id": int(user_id)}, projection)

    def list_users(self) -> list[Mapping[str, Any]]:
        return list(self.users.find({}))

    def list_user_ids(self) -> list[int]:
        return [int(document["_id"]) for document in self.users.find({}, {"_id": 1})]

    def count_users(self, filters: Mapping[str, Any] | None = None) -> int:
        return int(self.users.count_documents(dict(filters or {})))

    def sum_user_field(self, field: str) -> int:
        result = list(
            self.users.aggregate(
                [
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": {"$ifNull": [f"${field}", 0]}},
                        }
                    }
                ]
            )
        )
        return int(result[0]["total"]) if result else 0

    def count_referrals(self, referred_by: int | None = None) -> int:
        if referred_by is None:
            filters = {"referred_by": {"$ne": None}}
        else:
            filters = {"referred_by": int(referred_by)}
        return self.count_users(filters)

    def top_referrers(self, limit: int = 3) -> list[tuple[Any, int]]:
        result = self.users.aggregate(
            [
                {"$match": {"referred_by": {"$ne": None}}},
                {"$group": {"_id": "$referred_by", "referrals": {"$sum": 1}}},
                {"$sort": {"referrals": -1}},
                {"$limit": int(limit)},
            ]
        )
        return [
            (document.get("_id"), int(document.get("referrals", 0)))
            for document in result
        ]

    def set_user_fields(self, user_id: int, fields: Mapping[str, Any]) -> bool:
        if not fields:
            return False
        result = self.users.update_one({"_id": int(user_id)}, {"$set": dict(fields)})
        return bool(result.matched_count)

    def increment_user_field(self, user_id: int, field: str, amount: int) -> bool:
        if field not in {"total_deposited"}:
            raise ValueError(f"Unknown incrementable user field: {field}")
        result = self.users.update_one(
            {"_id": int(user_id)},
            {"$inc": {field: int(amount)}},
        )
        return bool(result.matched_count)

    def set_referred_by_if_empty(self, user_id: int, referred_by: int) -> bool:
        result = self.users.update_one(
            {
                "_id": int(user_id),
                "$or": [{"referred_by": None}, {"referred_by": {"$exists": False}}],
            },
            {"$set": {"referred_by": int(referred_by)}},
        )
        return bool(result.modified_count)

    def insert_user_if_missing(self, document: Mapping[str, Any]) -> bool:
        """Insert a backup row only when absent; conflicting data is untouched."""
        if "_id" not in document:
            raise ValueError("user backup document is missing _id")
        result = self.users.update_one(
            {"_id": document["_id"]},
            {"$setOnInsert": dict(document)},
            upsert=True,
        )
        return bool(getattr(result, "upserted_id", None) is not None)

    def get_setting(self, key: str, default: Any = None) -> Any:
        document = self.settings.find_one({"_id": str(key)}, {"value": 1})
        return document["value"] if document and "value" in document else default

    def set_setting(self, key: str, value: Any) -> None:
        # SQLite's TEXT column stores numeric inputs as text. Match that
        # behavior while leaving already-stored Mongo values untouched.
        stored_value = value if isinstance(value, str) else str(value)
        self.settings.update_one(
            {"_id": str(key)},
            {"$set": {"value": stored_value}},
            upsert=True,
        )

    def delete_setting(self, key: str) -> None:
        self.settings.delete_one({"_id": str(key)})

    def is_admin(self, user_id: int) -> bool:
        return self.admins.find_one({"_id": int(user_id)}, {"_id": 1}) is not None

    def has_permission(self, user_id: int, permission: str) -> bool:
        if permission not in self.ADMIN_PERMISSIONS:
            raise ValueError(f"Unknown admin permission: {permission}")
        document = self.admins.find_one({"_id": int(user_id)}, {permission: 1})
        return bool(document and document.get(permission) == 1)

    def list_admin_ids(self) -> list[int]:
        return [int(document["_id"]) for document in self.admins.find({}, {"_id": 1})]

    def get_admin(self, user_id: int) -> Mapping[str, Any] | None:
        return self.admins.find_one({"_id": int(user_id)})

    def add_admin(self, user_id: int) -> None:
        self.admins.update_one(
            {"_id": int(user_id)},
            {
                "$setOnInsert": {
                    "_id": int(user_id),
                    "user_id": int(user_id),
                    "p_add_stock": 0,
                    "p_manage_stock": 0,
                    "p_stats": 0,
                    "p_bal": 0,
                    "p_settings": 0,
                }
            },
            upsert=True,
        )

    def toggle_permission(self, user_id: int, permission: str) -> None:
        if permission not in self.ADMIN_PERMISSIONS:
            raise ValueError(f"Unknown admin permission: {permission}")
        self.admins.update_one(
            {"_id": int(user_id)},
            {"$bit": {permission: {"xor": 1}}},
        )

    def delete_admin(self, user_id: int) -> None:
        self.admins.delete_one({"_id": int(user_id)})

    def custom_country_by_name(self, name: str) -> Mapping[str, Any] | None:
        return self.custom_countries.find_one({"name": name})

    def list_custom_countries(self) -> list[Mapping[str, Any]]:
        return list(self.custom_countries.find({}))

    def save_custom_country(self, code: str, name: str, flag: str) -> None:
        self.custom_countries.update_one(
            {"_id": str(code)},
            {
                "$set": {
                    "code": str(code),
                    "name": name,
                    "flag": flag,
                },
                "$setOnInsert": {"_id": str(code)},
            },
            upsert=True,
        )

    def list_custom_payment_names(self) -> list[str]:
        return [
            document["name"]
            for document in self.custom_payments.find({}, {"name": 1})
            if document.get("name") is not None
        ]

    def get_custom_payment(self, name: str) -> Mapping[str, Any] | None:
        return self.custom_payments.find_one({"name": name})

    def list_custom_payments(self) -> list[Mapping[str, Any]]:
        return list(self.custom_payments.find({}))

    def add_custom_payment(self, name: str, caption: str, qr_file_id: str) -> Any:
        document = {
            "name": name,
            "caption": caption,
            "qr_file_id": qr_file_id,
        }
        result = self.custom_payments.insert_one(document)
        return result.inserted_id

    def get_custom_payment_by_id(self, payment_id: Any) -> Mapping[str, Any] | None:
        candidates = [payment_id]
        text_id = str(payment_id).strip()
        if text_id.isdigit():
            candidates.append(int(text_id))
        try:
            from bson import ObjectId

            if ObjectId.is_valid(text_id):
                candidates.append(ObjectId(text_id))
        except ImportError:
            pass
        for candidate in candidates:
            document = self.custom_payments.find_one({"_id": candidate})
            if document is not None:
                return document
        return None

    def delete_custom_payment(self, payment_id: Any) -> None:
        self.custom_payments.delete_one({"_id": payment_id})


def sqlite_max_ids(connection: sqlite3.Connection) -> dict[str, int]:
    values = {}
    for table in COUNTER_TABLES:
        row = connection.execute(f"SELECT MAX(id) AS max_id FROM {table}").fetchone()
        values[table] = int(row["max_id"] or 0)
    return values


class SQLiteMongoMigrator:
    """Repeatable, insert-only/conflict-aware SQLite-to-Mongo migrator."""

    SOURCE_COLLECTIONS = (
        "users",
        "settings",
        "inventory",
        "auto_prices",
        "deposits",
        "upi_orders",
        "orders",
        "custom_payments",
        "admins",
        "custom_countries",
    )

    def __init__(self, repository: MongoRepository, connection: sqlite3.Connection):
        self.repository = repository
        self.connection = connection

    @staticmethod
    def _empty_stats() -> dict[str, Any]:
        return {
            "inserted": 0,
            "identical": 0,
            "conflicts": 0,
            "invalid": 0,
            "errors": [],
        }

    @staticmethod
    def _add_issue(stats: dict[str, Any], message: str) -> None:
        if len(stats["errors"]) < 100:
            stats["errors"].append(message)

    def _read_rows(self, collection_name: str) -> list[sqlite3.Row]:
        table = "stock" if collection_name == "inventory" else collection_name
        columns = [
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        if not columns:
            raise RuntimeError(f"SQLite table is missing: {table}")

        selected = {
            "inventory": (
                "phone, session_file, country_name, country_icon, account_year, "
                "category, price, available, twofa, added_date"
                + (", data_center" if "data_center" in columns else "")
            ),
            "users": (
                "user_id, balance, referred_by, total_deposited, joined_date, "
                "banned, discount, terms_accepted"
            ),
            "settings": "key, value",
            "auto_prices": "country, year, price",
            "deposits": "id, user_id, amount, method_name, status, date, screenshot, utr",
            "upi_orders": "order_id, user_id, amount, status, date",
            "orders": "id, user_id, country, year, price, phone, otp, date",
            "custom_payments": "id, name, caption, qr_file_id",
            "admins": "user_id, p_add_stock, p_manage_stock, p_stats, p_bal, p_settings",
            "custom_countries": "code, name, flag",
        }[collection_name]
        return self.connection.execute(f"SELECT {selected} FROM {table}").fetchall()

    def _find_existing(self, collection_name: str, identity: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return list(self.repository.collection(collection_name).find(dict(identity)))

    def _migrate_collection(self, collection_name: str, stats: dict[str, Any]) -> None:
        try:
            rows = self._read_rows(collection_name)
        except Exception as exc:
            stats["invalid"] += 1
            self._add_issue(stats, str(exc))
            return

        seen_identities: set[tuple[Any, ...]] = set()
        definition = COLLECTION_DEFINITIONS[collection_name]
        for row in rows:
            try:
                document = sqlite_row_to_document(collection_name, row)
                missing = validate_document(collection_name, document)
                if missing:
                    stats["invalid"] += 1
                    self._add_issue(
                        stats,
                        f"missing required fields {missing}: {_safe_value(document)}",
                    )
                    continue

                identity = _identity_filter(collection_name, document)
                identity_key = tuple(identity.values())
                if identity_key in seen_identities:
                    stats["invalid"] += 1
                    self._add_issue(
                        stats,
                        f"duplicate SQLite identity: {_safe_value(identity)}",
                    )
                    continue
                seen_identities.add(identity_key)

                existing_documents = self._find_existing(collection_name, identity)
                if len(existing_documents) > 1:
                    stats["conflicts"] += 1
                    self._add_issue(
                        stats,
                        f"multiple Mongo documents match {_safe_value(identity)}",
                    )
                    continue
                if existing_documents:
                    existing = existing_documents[0]
                    if managed_projection(collection_name, existing) == managed_projection(
                        collection_name, document
                    ):
                        stats["identical"] += 1
                    else:
                        stats["conflicts"] += 1
                        self._add_issue(
                            stats,
                            f"Mongo conflict for {_safe_value(identity)}",
                        )
                    continue

                self.repository.collection(collection_name).insert_one(document)
                stats["inserted"] += 1
            except Exception as exc:
                # No update or delete is attempted on any exception.
                stats["errors"].append(
                    f"migration error in {collection_name}: {exc}"
                )

    def migrate(self, prepare_report: Mapping[str, Any] | None = None) -> dict[str, Any]:
        report = {
            "collections": {
                name: self._empty_stats() for name in COLLECTION_NAMES
            },
            "counters": {},
            "sqlite_sequence": "not migrated",
            "prepare": prepare_report,
        }
        for collection_name in self.SOURCE_COLLECTIONS:
            self._migrate_collection(collection_name, report["collections"][collection_name])

        try:
            max_ids = sqlite_max_ids(self.connection)
            for table, maximum in max_ids.items():
                result = self.repository.initialize_counter(table, maximum)
                report["counters"][table] = result
                if result["status"] == "inserted":
                    report["collections"]["counters"]["inserted"] += 1
                elif result["status"] == "identical":
                    report["collections"]["counters"]["identical"] += 1
                else:
                    report["collections"]["counters"]["conflicts"] += 1
        except Exception as exc:
            report["collections"]["counters"]["invalid"] += 1
            self._add_issue(report["collections"]["counters"], str(exc))

        # These collections are intentionally empty in this SQLite migration.
        report["collections"]["balance_ledger"]["errors"].append(
            "no SQLite source; prepared for future balance events"
        )
        report["collections"]["pending_workflows"]["errors"].append(
            "no SQLite source; prepared for future workflow state"
        )
        return report


def create_mongo_client(uri: str, timeout_ms: int = 3000) -> Any:
    """Create a PyMongo client lazily so offline tests need no live Mongo."""
    if not uri or not uri.strip():
        raise RuntimeError("MONGODB_URI is required for the migration tool")
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError(
            "PyMongo is not installed; install requirements.txt before migrating"
        ) from exc
    return MongoClient(uri.strip(), serverSelectionTimeoutMS=timeout_ms)