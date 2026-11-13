import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = ("product", "user", "order", "order_item")
NEW_COLUMNS = {
    "recipient_name": "VARCHAR(120)",
    "recipient_phone": "VARCHAR(30)",
    "shipping_address": "VARCHAR(500)",
    "note": "TEXT",
    "payment_method": "VARCHAR(30)",
    "created_at": "DATETIME",
    "checkout_token": "VARCHAR(64)",
}


def _readonly_connection(path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def _table_counts(connection):
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = set(EXPECTED_TABLES) - tables
    if missing:
        raise RuntimeError(f"Database is missing required tables: {sorted(missing)}")
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in EXPECTED_TABLES
    }


def _order_preservation_snapshot(connection):
    order_columns = {
        row[1] for row in connection.execute('PRAGMA table_info("order")')
    }
    required_order_columns = {"id", "user_id", "status", "total_amount"}
    if not required_order_columns.issubset(order_columns):
        raise RuntimeError("The order table does not match the expected schema.")

    orders = connection.execute(
        'SELECT id, user_id, status, total_amount FROM "order" ORDER BY id'
    ).fetchall()
    order_items = connection.execute(
        "SELECT id, order_id, product_id, quantity, unit_price "
        "FROM order_item ORDER BY id"
    ).fetchall()
    return orders, order_items


def migrate(database_path, backup_path):
    database_path = Path(database_path).resolve()
    backup_path = Path(backup_path).resolve()
    if database_path == backup_path:
        raise RuntimeError("The live database and its backup must be different files.")
    if not database_path.is_file() or not backup_path.is_file():
        raise RuntimeError("Both the live database and verified backup must exist.")

    with closing(_readonly_connection(database_path)) as live_read, closing(
        _readonly_connection(backup_path)
    ) as backup_read:
        live_counts = _table_counts(live_read)
        backup_counts = _table_counts(backup_read)
        if live_counts != backup_counts:
            raise RuntimeError("The backup record counts do not match the live database.")
        if live_read.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The live database failed SQLite integrity_check.")
        if backup_read.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The backup failed SQLite integrity_check.")
        before_snapshot = _order_preservation_snapshot(live_read)

    connection = sqlite3.connect(str(database_path), timeout=30)
    added_columns = []
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("order")')
        }
        for name, definition in NEW_COLUMNS.items():
            if name not in columns:
                connection.execute(
                    f'ALTER TABLE "order" ADD COLUMN "{name}" {definition}'
                )
                added_columns.append(name)

        connection.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS "uq_order_checkout_token" '
            'ON "order" ("checkout_token")'
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    with closing(_readonly_connection(database_path)) as migrated:
        after_snapshot = _order_preservation_snapshot(migrated)
        missing_columns = set(NEW_COLUMNS) - {
            row[1] for row in migrated.execute('PRAGMA table_info("order")')
        }
        integrity = migrated.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_issues = migrated.execute("PRAGMA foreign_key_check").fetchall()

    if before_snapshot != after_snapshot:
        raise RuntimeError("Order or OrderItem records changed during migration.")
    if missing_columns:
        raise RuntimeError(f"Migration did not add columns: {sorted(missing_columns)}")
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity_check failed after migration: {integrity}")
    if foreign_key_issues:
        raise RuntimeError("SQLite foreign_key_check reported integrity issues.")
    return added_columns, live_counts


def main():
    parser = argparse.ArgumentParser(
        description="Safely add nullable shipping and idempotency fields to SQLite orders."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "instance" / "ecommerce.db",
        help="SQLite database to migrate (default: instance/ecommerce.db).",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        required=True,
        help="Verified SQLite backup; migration stops if its record counts differ.",
    )
    args = parser.parse_args()

    added_columns, record_counts = migrate(args.database, args.backup)
    if added_columns:
        print(f"Added nullable Order columns: {', '.join(added_columns)}")
    else:
        print("Order columns were already present; no columns were added.")
    print(f"Record counts verified: {record_counts}")
    print("SQLite integrity_check and foreign_key_check passed.")


if __name__ == "__main__":
    main()
