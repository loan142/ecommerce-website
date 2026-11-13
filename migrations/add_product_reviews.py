import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TABLES = {"product", "user", "order", "order_item", "review"}
REVIEW_COLUMNS = {
    "status": "VARCHAR(20) NOT NULL DEFAULT 'pending'",
    "purchase_verified": "BOOLEAN NOT NULL DEFAULT 0",
    "created_at": "DATETIME",
    "updated_at": "DATETIME",
}
PRESERVED_TABLES = ("product", "user", "order", "order_item", "review")


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


def _table_columns(connection, table):
    return {
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


def _row_snapshots(connection, columns_by_table=None):
    snapshots = {}
    for table in PRESERVED_TABLES:
        columns = (
            columns_by_table[table]
            if columns_by_table is not None
            else sorted(_table_columns(connection, table))
        )
        selected = ", ".join(f'"{column}"' for column in columns)
        snapshots[table] = connection.execute(
            f'SELECT {selected} FROM "{table}" ORDER BY rowid'
        ).fetchall()
    return snapshots


def migrate(database_path, backup_path):
    database_path = Path(database_path).resolve()
    backup_path = Path(backup_path).resolve()
    if database_path == backup_path:
        raise RuntimeError("The live database and its backup must be different files.")
    if not database_path.is_file() or not backup_path.is_file():
        raise RuntimeError("Both the live database and verified backup must exist.")

    with closing(_readonly_connection(database_path)) as live, closing(
        _readonly_connection(backup_path)
    ) as backup:
        live_tables = _table_names(live)
        backup_tables = _table_names(backup)
        if not REQUIRED_TABLES.issubset(live_tables & backup_tables):
            raise RuntimeError("The database or backup is missing required tables.")
        if (
            live_tables - backup_tables not in (set(), {"product_comment"})
            or backup_tables - live_tables
        ):
            raise RuntimeError("The backup tables do not match the live database.")
        if live.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The live database failed SQLite integrity_check.")
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The backup failed SQLite integrity_check.")
        if live.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("The live database already has foreign-key violations.")
        if backup.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("The backup already has foreign-key violations.")

        columns_before = {
            table: sorted(
                _table_columns(live, table) & _table_columns(backup, table)
            )
            for table in PRESERVED_TABLES
        }
        before = _row_snapshots(live, columns_before)
        if before != _row_snapshots(backup, columns_before):
            raise RuntimeError("The backup data does not match the live database.")
        required_review_columns = {"id", "product_id", "user_id", "rating", "comment"}
        if not required_review_columns.issubset(_table_columns(live, "review")):
            raise RuntimeError('The "review" table has an incompatible schema.')
        duplicates = live.execute(
            "SELECT product_id, user_id, COUNT(*) FROM review "
            "WHERE user_id IS NOT NULL GROUP BY product_id, user_id HAVING COUNT(*) > 1"
        ).fetchall()
        if duplicates:
            raise RuntimeError(
                "Existing duplicate customer/product reviews prevent the unique "
                f"constraint: {duplicates}"
            )

    connection = sqlite3.connect(str(database_path), timeout=30)
    added_columns = []
    created_comment_table = False
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        columns = _table_columns(connection, "review")
        for name, definition in REVIEW_COLUMNS.items():
            if name not in columns:
                connection.execute(
                    f'ALTER TABLE review ADD COLUMN "{name}" {definition}'
                )
                added_columns.append(name)

        comment_exists = "product_comment" in _table_names(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS product_comment (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES product(id),
                user_id INTEGER NOT NULL REFERENCES user(id),
                kind VARCHAR(20) NOT NULL DEFAULT 'comment'
                    CHECK (kind IN ('comment', 'question')),
                content TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'hidden')),
                admin_reply TEXT,
                replied_by_user_id INTEGER REFERENCES user(id),
                replied_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        created_comment_table = not comment_exists
        connection.execute(
            'CREATE INDEX IF NOT EXISTS "ix_review_product_id" ON review (product_id)'
        )
        connection.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS "uq_review_product_customer" '
            "ON review (product_id, user_id) WHERE user_id IS NOT NULL"
        )
        connection.execute(
            'CREATE INDEX IF NOT EXISTS "ix_product_comment_product_id" '
            "ON product_comment (product_id)"
        )
        connection.execute(
            'CREATE INDEX IF NOT EXISTS "ix_product_comment_user_id" '
            "ON product_comment (user_id)"
        )
        connection.execute(
            'CREATE INDEX IF NOT EXISTS "ix_product_comment_status" '
            "ON product_comment (status)"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    with closing(_readonly_connection(database_path)) as migrated:
        after = _row_snapshots(migrated, columns_before)
        if after != before:
            raise RuntimeError("Existing product, account, order, or review data changed.")
        columns_after = _table_columns(migrated, "review")
        missing_columns = set(REVIEW_COLUMNS) - columns_after
        if missing_columns:
            raise RuntimeError(
                f'Migration did not add review columns: {sorted(missing_columns)}'
            )
        if "product_comment" not in _table_names(migrated):
            raise RuntimeError('Migration did not create "product_comment".')
        integrity = migrated.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_issues = migrated.execute("PRAGMA foreign_key_check").fetchall()
        unique_index = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'uq_review_product_customer'"
        ).fetchone()
        if unique_index is None or "UNIQUE" not in unique_index[0].upper():
            raise RuntimeError("The unique customer/product review index is missing.")

    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity_check failed after migration: {integrity}")
    if foreign_key_issues:
        raise RuntimeError("SQLite foreign_key_check reported integrity issues.")
    return added_columns, created_comment_table


def main():
    parser = argparse.ArgumentParser(
        description="Safely add product ratings and comments to SQLite."
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
        help="Verified SQLite backup; migration stops if existing data differs.",
    )
    args = parser.parse_args()

    added_columns, created_comment_table = migrate(args.database, args.backup)
    if added_columns:
        print(f"Added Review columns: {', '.join(added_columns)}")
    else:
        print("Review columns already existed; no columns were added.")
    if created_comment_table:
        print("Created product_comment table.")
    else:
        print("product_comment table already existed.")
    print("Existing rows were preserved; legacy reviews remain unverified.")
    print("SQLite integrity_check and foreign_key_check passed.")


if __name__ == "__main__":
    main()
