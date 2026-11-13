import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STARTER_CATEGORIES = (
    ("Bình hoa gốm", "binh-hoa-gom"),
    ("Bộ ấm chén", "bo-am-chen"),
    ("Chén đĩa gốm", "chen-dia-gom"),
    ("Gốm trang trí", "gom-trang-tri"),
    ("Quà tặng gốm", "qua-tang-gom"),
)
PRESERVED_TABLE_COLUMNS = {
    "product": (
        "id",
        "name",
        "description",
        "price",
        "old_price",
        "image_url",
        "stock",
        "is_active",
    ),
    "order": (
        "id",
        "user_id",
        "status",
        "total_amount",
        "recipient_name",
        "recipient_phone",
        "shipping_address",
        "note",
        "payment_method",
        "created_at",
        "checkout_token",
    ),
    "order_item": ("id", "order_id", "product_id", "quantity", "unit_price"),
}


def _readonly_connection(path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def _table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _record_counts(connection):
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in sorted(_table_names(connection))
    }


def _snapshot(connection, table, columns):
    available = {
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
    }
    required_columns = {
        "product": {"id", "name", "price", "stock", "is_active"},
        "order": {"id", "status", "total_amount"},
        "order_item": {"id", "order_id", "product_id", "quantity", "unit_price"},
    }[table]
    if not required_columns.issubset(available):
        raise RuntimeError(f'The "{table}" table does not match the expected schema.')
    selected_columns = tuple(column for column in columns if column in available)
    selected = ", ".join(f'"{column}"' for column in selected_columns)
    return connection.execute(
        f'SELECT {selected} FROM "{table}" ORDER BY id'
    ).fetchall()


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
        if live_read.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The live database failed SQLite integrity_check.")
        if backup_read.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The backup failed SQLite integrity_check.")
        current_counts = _record_counts(live_read)
        backup_counts = _record_counts(backup_read)
        live_tables = _table_names(live_read)
        backup_tables = _table_names(backup_read)
        if "product" not in live_tables or "product" not in backup_tables:
            raise RuntimeError('The database is missing the required "product" table.')
        product_columns = {
            row[1] for row in live_read.execute('PRAGMA table_info("product")')
        }
        already_migrated = (
            "category_id" in product_columns and "category" in live_tables
        )
        original_counts = {
            table: count for table, count in current_counts.items() if table != "category"
        }
        if not already_migrated:
            if current_counts != backup_counts:
                raise RuntimeError(
                    "The backup record counts do not match the live database."
                )
            if live_tables != backup_tables:
                raise RuntimeError("The backup tables do not match the live database.")
            before = {
                table: _snapshot(live_read, table, columns)
                for table, columns in PRESERVED_TABLE_COLUMNS.items()
                if table in live_tables
            }
            snapshot_columns = {
                table: tuple(
                    row[1]
                    for row in live_read.execute(f'PRAGMA table_info("{table}")')
                    if row[1] in PRESERVED_TABLE_COLUMNS[table]
                )
                for table in before
            }
            backup_before = {
                table: _snapshot(backup_read, table, snapshot_columns[table])
                for table in before
            }
            if before != backup_before:
                raise RuntimeError("The backup data does not match the live database.")
            if "product" not in before:
                raise RuntimeError('The database is missing the required "product" table.')
        else:
            before = {}
            snapshot_columns = {}

    connection = sqlite3.connect(str(database_path), timeout=30)
    added_product_column = False
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS category (
                id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                slug VARCHAR(120) NOT NULL UNIQUE
            )
            """
        )
        category_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("category")')
        }
        if not {"id", "name", "slug"}.issubset(category_columns):
            raise RuntimeError('The existing "category" table has an incompatible schema.')

        product_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("product")')
        }
        if "category_id" not in product_columns:
            connection.execute(
                "ALTER TABLE product ADD COLUMN category_id INTEGER "
                "REFERENCES category(id)"
            )
            added_product_column = True
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_product_category_id "
            "ON product (category_id)"
        )
        connection.executemany(
            "INSERT OR IGNORE INTO category (name, slug) VALUES (?, ?)",
            STARTER_CATEGORIES,
        )

        after = {
            table: _snapshot(connection, table, snapshot_columns[table])
            for table in before
        }
        if before != after:
            raise RuntimeError("Product or order data changed during migration.")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity_check failed after migration.")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("SQLite foreign_key_check reported integrity issues.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    with closing(_readonly_connection(database_path)) as migrated:
        expected_categories = {
            row[1] for row in STARTER_CATEGORIES
        }
        actual_categories = {
            row[0] for row in migrated.execute("SELECT slug FROM category")
        }
        if not expected_categories.issubset(actual_categories):
            raise RuntimeError("The starter categories were not fully created.")
        if migrated.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity_check failed after migration.")
        if migrated.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("SQLite foreign_key_check reported integrity issues.")

    return added_product_column, original_counts


def main():
    parser = argparse.ArgumentParser(
        description="Safely add product categories to an existing SQLite database."
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
        help="Verified SQLite backup; migration stops if it differs from the live database.",
    )
    args = parser.parse_args()

    added_column, record_counts = migrate(args.database, args.backup)
    if added_column:
        print("Added nullable Product.category_id foreign key.")
    else:
        print("Product.category_id already exists; no column was added.")
    print(f"Starter categories ensured: {len(STARTER_CATEGORIES)}")
    print(f"Original record counts verified: {record_counts}")
    print("Product/order data, SQLite integrity_check, and foreign_key_check passed.")


if __name__ == "__main__":
    main()
