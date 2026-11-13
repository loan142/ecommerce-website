import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from migrations.add_order_shipping_fields import NEW_COLUMNS, migrate


class OrderMigrationTests(TestCase):
    def test_migration_preserves_legacy_orders_and_is_safe_to_repeat(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "legacy.sqlite"
            backup = Path(temporary_directory) / "legacy-backup.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE product (id INTEGER PRIMARY KEY);
                CREATE TABLE user (id INTEGER PRIMARY KEY);
                CREATE TABLE "order" (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    status VARCHAR(30) NOT NULL,
                    total_amount INTEGER NOT NULL
                );
                CREATE TABLE order_item (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price INTEGER NOT NULL
                );
                INSERT INTO product (id) VALUES (7);
                INSERT INTO user (id) VALUES (4);
                INSERT INTO "order" (id, user_id, status, total_amount)
                    VALUES (42, 4, 'pending', 12345);
                INSERT INTO order_item (id, order_id, product_id, quantity, unit_price)
                    VALUES (89, 42, 7, 3, 4115);
                """
            )
            backup_connection = sqlite3.connect(backup)
            connection.backup(backup_connection)
            backup_connection.close()
            connection.close()

            added_columns, counts = migrate(database, backup)
            self.assertEqual(set(added_columns), set(NEW_COLUMNS))
            self.assertEqual(counts, {"product": 1, "user": 1, "order": 1, "order_item": 1})

            connection = sqlite3.connect(database)
            try:
                order = connection.execute(
                    'SELECT id, user_id, status, total_amount, recipient_name, '
                    'recipient_phone, shipping_address, note, payment_method, '
                    'created_at, checkout_token FROM "order"'
                ).fetchone()
                item = connection.execute(
                    "SELECT id, order_id, product_id, quantity, unit_price "
                    "FROM order_item"
                ).fetchone()
                self.assertEqual(order[:4], (42, 4, "pending", 12345))
                self.assertEqual(order[4:], (None, None, None, None, None, None, None))
                self.assertEqual(item, (89, 42, 7, 3, 4115))
            finally:
                connection.close()

            repeated_columns, repeated_counts = migrate(database, backup)
            self.assertEqual(repeated_columns, [])
            self.assertEqual(repeated_counts, counts)

    def test_migration_refuses_a_backup_with_different_record_counts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "live.sqlite"
            backup = Path(temporary_directory) / "mismatched.sqlite"
            for path, order_count in ((database, 1), (backup, 0)):
                connection = sqlite3.connect(path)
                connection.executescript(
                    """
                    CREATE TABLE product (id INTEGER PRIMARY KEY);
                    CREATE TABLE user (id INTEGER PRIMARY KEY);
                    CREATE TABLE "order" (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER,
                        status VARCHAR(30) NOT NULL,
                        total_amount INTEGER NOT NULL
                    );
                    CREATE TABLE order_item (
                        id INTEGER PRIMARY KEY,
                        order_id INTEGER NOT NULL,
                        product_id INTEGER NOT NULL,
                        quantity INTEGER NOT NULL,
                        unit_price INTEGER NOT NULL
                    );
                    """
                )
                if order_count:
                    connection.execute(
                        'INSERT INTO "order" (id, status, total_amount) '
                        "VALUES (1, 'pending', 100)"
                    )
                connection.commit()
                connection.close()

            with self.assertRaisesRegex(RuntimeError, "record counts"):
                migrate(database, backup)

            connection = sqlite3.connect(database)
            try:
                columns = {
                    row[1] for row in connection.execute('PRAGMA table_info("order")')
                }
                self.assertFalse(set(NEW_COLUMNS) & columns)
            finally:
                connection.close()
