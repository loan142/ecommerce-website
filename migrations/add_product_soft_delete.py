import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TABLES = {"product", "order", "order_item"}


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
    return [
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
    ]


def _record_counts(connection, tables):
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in sorted(tables)
    }


def _snapshot(connection, table, columns):
    available = set(_table_columns(connection, table))
    selected_columns = [column for column in columns if column in available]
    if not selected_columns:
        return ()
    selected = ", ".join(f'"{column}"' for column in selected_columns)
    return connection.execute(
        f'SELECT {selected} FROM "{table}" ORDER BY rowid'
    ).fetchall()


def _snapshots(connection, tables, columns_by_table=None):
    return {
        table: _snapshot(
            connection,
            table,
            columns_by_table[table]
            if columns_by_table is not None
            else _table_columns(connection, table),
        )
        for table in sorted(tables)
    }


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
        if live.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The live database failed SQLite integrity_check.")
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The backup failed SQLite integrity_check.")

        live_tables = _table_names(live)
        backup_tables = _table_names(backup)
        if not REQUIRED_TABLES.issubset(live_tables & backup_tables):
            raise RuntimeError("The database or backup is missing required tables.")
        if live_tables != backup_tables:
            raise RuntimeError("The backup tables do not match the live database.")

        counts = _record_counts(live, live_tables)
        if counts != _record_counts(backup, backup_tables):
            raise RuntimeError("The backup record counts do not match the live database.")

        if live.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("The live database already has foreign-key violations.")
        if backup.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("The backup already has foreign-key violations.")

        columns_by_table = {
            table: [
                column
                for column in _table_columns(live, table)
                if column in _table_columns(backup, table)
            ]
            for table in live_tables
        }
        live_before = _snapshots(live, live_tables, columns_by_table)
        backup_before = _snapshots(backup, backup_tables, columns_by_table)
        if live_before != backup_before:
            raise RuntimeError("The backup data does not match the live database.")
        product_columns = set(_table_columns(live, "product"))
        if not {"id", "name", "price", "stock", "is_active"}.issubset(
            product_columns
        ):
            raise RuntimeError('The "product" table has an incompatible schema.')
        columns_before = columns_by_table

    connection = sqlite3.connect(str(database_path), timeout=30)
    added_column = False
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        columns = set(_table_columns(connection, "product"))
        if "deleted_at" not in columns:
            connection.execute(
                "ALTER TABLE product ADD COLUMN deleted_at DATETIME"
            )
            added_column = True
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    with closing(_readonly_connection(database_path)) as migrated:
        if _record_counts(migrated, live_tables) != counts:
            raise RuntimeError("Record counts changed during the product migration.")
        after = _snapshots(migrated, live_tables, columns_before)
        if after != live_before:
            raise RuntimeError("Existing database records changed during migration.")
        if "deleted_at" not in _table_columns(migrated, "product"):
            raise RuntimeError('Migration did not add "product.deleted_at".')
        integrity = migrated.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_issues = migrated.execute("PRAGMA foreign_key_check").fetchall()

    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity_check failed after migration: {integrity}")
    if foreign_key_issues:
        raise RuntimeError("SQLite foreign_key_check reported integrity issues.")
    return added_column, counts


def main():
    parser = argparse.ArgumentParser(
        description="Safely add the nullable product soft-delete timestamp to SQLite."
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
        help="Verified SQLite backup; migration stops if data differs.",
    )
    args = parser.parse_args()

    added_column, record_counts = migrate(args.database, args.backup)
    if added_column:
        print("Added nullable Product.deleted_at column.")
    else:
        print("Product.deleted_at was already present; no column was added.")
    print(f"Record counts verified: {record_counts}")
    print("SQLite integrity_check and foreign_key_check passed.")


if __name__ == "__main__":
    main()
