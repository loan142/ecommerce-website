import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
        "category_id",
    ),
    "category": ("id", "name", "slug"),
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
PRODUCT_IMAGE_COLUMNS = {
    "id",
    "product_id",
    "image_path",
    "sort_order",
    "is_primary",
}


def _readonly_connection(path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def _table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _record_counts(connection, tables):
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in sorted(tables)
    }


def _snapshot(connection, table, columns):
    available = {
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
    }
    if not set(columns).issubset(available):
        raise RuntimeError(f'The "{table}" table does not match the expected schema.')
    selected = ", ".join(f'"{column}"' for column in columns)
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

        live_tables = _table_names(live_read)
        backup_tables = _table_names(backup_read)
        if not {"product", "category", "order", "order_item"}.issubset(live_tables):
            raise RuntimeError("The database is missing a required product or order table.")
        if not {"product", "category", "order", "order_item"}.issubset(backup_tables):
            raise RuntimeError("The backup is missing a required product or order table.")

        already_migrated = "product_image" in live_tables
        comparison_tables = live_tables - {"product_image"}
        backup_comparison_tables = backup_tables - {"product_image"}
        if not already_migrated and live_tables != backup_tables:
            raise RuntimeError("The backup tables do not match the live database.")
        if comparison_tables != backup_comparison_tables:
            raise RuntimeError("The backup tables do not match the live database.")

        live_counts = _record_counts(live_read, comparison_tables)
        backup_counts = _record_counts(backup_read, backup_comparison_tables)
        if live_counts != backup_counts:
            raise RuntimeError("The backup record counts do not match the live database.")
        before = {
            table: _snapshot(live_read, table, columns)
            for table, columns in PRESERVED_TABLE_COLUMNS.items()
        }
        backup_before = {
            table: _snapshot(backup_read, table, columns)
            for table, columns in PRESERVED_TABLE_COLUMNS.items()
        }
        if before != backup_before:
            raise RuntimeError("The backup data does not match the live database.")

        if already_migrated:
            image_columns = {
                row[1]
                for row in live_read.execute('PRAGMA table_info("product_image")')
            }
            if not PRODUCT_IMAGE_COLUMNS.issubset(image_columns):
                raise RuntimeError('The existing "product_image" table is incompatible.')

    connection = sqlite3.connect(str(database_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS product_image (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL
                    REFERENCES product(id) ON DELETE CASCADE,
                image_path VARCHAR(500) NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_primary BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("product_image")')
        }
        if not PRODUCT_IMAGE_COLUMNS.issubset(columns):
            raise RuntimeError('The existing "product_image" table is incompatible.')
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_product_image_product_id "
            "ON product_image (product_id)"
        )
        connection.execute(
            """
            INSERT INTO product_image (product_id, image_path, sort_order, is_primary)
            SELECT product.id, product.image_url, 0, 1
            FROM product
            WHERE product.image_url IS NOT NULL AND trim(product.image_url) != ''
              AND NOT EXISTS (
                  SELECT 1 FROM product_image
                  WHERE product_image.product_id = product.id
              )
            """
        )
        after = {
            table: _snapshot(connection, table, columns)
            for table, columns in PRESERVED_TABLE_COLUMNS.items()
        }
        if before != after:
            raise RuntimeError("Product, category, or order data changed during migration.")
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
        if migrated.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity_check failed after migration.")
        if migrated.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("SQLite foreign_key_check reported integrity issues.")
        missing_legacy_images = migrated.execute(
            """
            SELECT COUNT(*) FROM product
            WHERE image_url IS NOT NULL AND trim(image_url) != ''
              AND NOT EXISTS (
                  SELECT 1 FROM product_image
                  WHERE product_image.product_id = product.id
                    AND product_image.image_path = product.image_url
              )
            """
        ).fetchone()[0]
        if missing_legacy_images:
            raise RuntimeError("Some existing Product.image_url values were not preserved.")

    return before


def main():
    parser = argparse.ArgumentParser(
        description="Safely add a product image gallery to an existing SQLite database."
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
        help="Verified SQLite backup; migration stops if it differs from live product/order data.",
    )
    args = parser.parse_args()

    before = migrate(args.database, args.backup)
    print("Created or verified the ProductImage table and index.")
    print(
        "Legacy Product.image_url values were retained and copied as primary gallery "
        "references; no image files were moved or deleted."
    )
    print(
        "Verified preserved record counts: "
        + str({table: len(rows) for table, rows in before.items()})
    )
    print("SQLite integrity_check and foreign_key_check passed.")


if __name__ == "__main__":
    main()
