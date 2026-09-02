#!/usr/bin/env python3
"""Run the safe, repeatable SQLite-to-MongoDB infrastructure migration.

Usage:
    python migrate_sqlite_to_mongo.py --sqlite-path otp_bot_final.db

The MongoDB URI is read from MONGODB_URI and is never printed. Existing Mongo
documents are only read or compared; conflicting documents are reported and
never updated or deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mongo_persistence import (
    MongoRepository,
    SQLiteMongoMigrator,
    create_mongo_client,
    sqlite_connection_read_only,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely copy SQLite business data into MongoDB without overwrites."
    )
    parser.add_argument(
        "--sqlite-path",
        default="otp_bot_final.db",
        help="Path to the SQLite database (opened read-only).",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MONGODB_DB", "otp_seller_bot"),
        help="MongoDB database name (default: MONGODB_DB or otp_seller_bot).",
    )
    parser.add_argument(
        "--inventory-collection",
        default=os.getenv("MONGODB_COLLECTION", "inventory"),
        help="Existing inventory collection name (default: MONGODB_COLLECTION or inventory).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    uri = os.getenv("MONGODB_URI", "").strip()
    connection = None
    client = None
    try:
        connection = sqlite_connection_read_only(args.sqlite_path)
        client = create_mongo_client(uri)
        client.admin.command("ping")
        repository = MongoRepository(
            client[args.db],
            inventory_collection_name=args.inventory_collection,
        )
        prepare_report = repository.prepare()
        migration_report = SQLiteMongoMigrator(repository, connection).migrate(
            prepare_report=prepare_report
        )
        print(json.dumps(migration_report, indent=2, sort_keys=True, default=str))
        return 0
    except Exception as exc:
        print(f"Migration did not run: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())