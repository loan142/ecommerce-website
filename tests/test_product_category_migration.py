import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import TestCase

from migrations.add_product_categories import STARTER_CATEGORIES, migrate


class ProductCategoryMigrationTests(TestCase):
    def _create_legacy_database(self, path):
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE category_placeholder (id INTEGER PRIMARY KEY);
            CREATE TABLE product (
                id INTEGER PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                description TEXT,
                price INTEGER NOT NULL DEFAULT 0,
                old_price INTEGER,
                image_url VARCHAR(500),
                stock INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
            CREATE TABLE user (id INTEGER PRIMARY KEY);
            CREATE TABLE "order" (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                status VARCHAR(30) NOT NULL,
                total_amount INTEGER NOT NULL,
                recipient_name VARCHAR(120),
                recipient_phone VARCHAR(30),
                shipping_address VARCHAR(500),
                note TEXT,
                payment_method VARCHAR(30),
                created_at DATETIME,
                checkout_token VARCHAR(64)
            );
            CREATE TABLE order_item (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price INTEGER NOT NULL
            );
            INSERT INTO product
                (id, name, description, price, old_price, image_url, stock, is_active)
                VALUES
                (7, 'Sản phẩm cũ', 'Mô tả cũ', 120000, NULL, '', 4, 1),
                (8, 'Sản phẩm ẩn', 'Không tự phân loại', 80000, NULL, '', 0, 0);
            INSERT INTO user (id) VALUES (4);
            INSERT INTO "order"
                (id, user_id, status, total_amount, recipient_name, checkout_token)
                VALUES (42, 4, 'pending', 12345, 'Khách cũ', 'old-token');
            INSERT INTO order_item
                (id, order_id, product_id, quantity, unit_price)
                VALUES (89, 42, 7, 3, 4115);
            """
        )
        connection.commit()
        connection.close()

    def test_migration_preserves_existing_data_and_is_safe_to_repeat(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "legacy.sqlite"
            backup = Path(temporary_directory) / "legacy-backup.sqlite"
            self._create_legacy_database(database)
            with closing(sqlite3.connect(database)) as source, closing(
                sqlite3.connect(backup)
            ) as target:
                source.backup(target)

            added_column, original_counts = migrate(database, backup)
            self.assertTrue(added_column)
            self.assertEqual(original_counts["product"], 2)

            with closing(sqlite3.connect(database)) as connection:
                products = connection.execute(
                    "SELECT id, name, description, price, stock, is_active, category_id "
                    "FROM product ORDER BY id"
                ).fetchall()
                self.assertEqual(
                    products,
                    [
                        (7, "Sản phẩm cũ", "Mô tả cũ", 120000, 4, 1, None),
                        (8, "Sản phẩm ẩn", "Không tự phân loại", 80000, 0, 0, None),
                    ],
                )
                self.assertEqual(
                    connection.execute('SELECT id, status, total_amount FROM "order"').fetchall(),
                    [(42, "pending", 12345)],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT id, order_id, product_id, quantity, unit_price "
                        "FROM order_item"
                    ).fetchall(),
                    [(89, 42, 7, 3, 4115)],
                )
                self.assertEqual(
                    connection.execute("SELECT name, slug FROM category ORDER BY id").fetchall(),
                    list(STARTER_CATEGORIES),
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "INSERT INTO category (name, slug) VALUES (?, ?)",
                    ("Gốm nghệ thuật", "gom-nghe-thuat"),
                )
                connection.execute(
                    "UPDATE product SET category_id = "
                    "(SELECT id FROM category WHERE slug = ?) WHERE id = 7",
                    ("gom-nghe-thuat",),
                )
                connection.commit()
                assigned_product = connection.execute(
                    "SELECT id, name, category_id FROM product WHERE id = 7"
                ).fetchone()

            repeated_column, repeated_counts = migrate(database, backup)
            self.assertFalse(repeated_column)
            self.assertEqual(repeated_counts, original_counts)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM category").fetchone()[0],
                    len(STARTER_CATEGORIES) + 1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT id, name, category_id FROM product WHERE id = 7"
                    ).fetchone(),
                    assigned_product,
                )

    def test_migration_refuses_a_mismatched_backup_without_mutating_live_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "live.sqlite"
            backup = Path(temporary_directory) / "mismatched.sqlite"
            self._create_legacy_database(database)
            self._create_legacy_database(backup)
            with closing(sqlite3.connect(backup)) as connection:
                connection.execute("DELETE FROM product WHERE id = 8")
                connection.commit()

            with self.assertRaisesRegex(RuntimeError, "record counts"):
                migrate(database, backup)

            with closing(sqlite3.connect(database)) as connection:
                columns = {
                    row[1] for row in connection.execute('PRAGMA table_info("product")')
                }
                self.assertNotIn("category_id", columns)
                self.assertFalse(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='category'"
                    ).fetchone()
                )
