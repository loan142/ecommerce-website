import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import TestCase

from migrations.add_product_soft_delete import migrate


class ProductSoftDeleteMigrationTests(TestCase):
    def _create_database(self, path):
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE category (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL
                );
                CREATE TABLE product (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    price INTEGER NOT NULL,
                    old_price INTEGER,
                    image_url TEXT,
                    stock INTEGER NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    category_id INTEGER REFERENCES category(id)
                );
                CREATE TABLE "order" (
                    id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    total_amount INTEGER NOT NULL
                );
                CREATE TABLE order_item (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL REFERENCES "order"(id),
                    product_id INTEGER NOT NULL REFERENCES product(id),
                    quantity INTEGER NOT NULL,
                    unit_price INTEGER NOT NULL
                );
                CREATE TABLE product_image (
                    id INTEGER PRIMARY KEY,
                    product_id INTEGER NOT NULL REFERENCES product(id),
                    image_path TEXT NOT NULL
                );
                CREATE TABLE review (
                    id INTEGER PRIMARY KEY,
                    product_id INTEGER NOT NULL REFERENCES product(id),
                    rating INTEGER NOT NULL
                );
                INSERT INTO category VALUES (1, 'Gốm', 'gom');
                INSERT INTO product VALUES
                    (7, 'Đang hiện', 'Mô tả', 120000, 150000, 'anh.jpg', 4, 1, 1),
                    (8, 'Đã ẩn', 'Khác', 80000, NULL, '', 0, 0, NULL);
                INSERT INTO "order" VALUES (42, 'pending', 240000);
                INSERT INTO order_item VALUES (89, 42, 7, 2, 120000);
                INSERT INTO product_image VALUES (12, 7, 'uploads/anh.jpg');
                INSERT INTO review VALUES (15, 7, 5);
                """
            )
            connection.commit()

    def _backup(self, database, backup):
        with closing(sqlite3.connect(database)) as source, closing(
            sqlite3.connect(backup)
        ) as target:
            source.backup(target)

    def test_migration_preserves_data_and_can_be_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "live.sqlite"
            backup = Path(directory) / "backup.sqlite"
            self._create_database(database)
            self._backup(database, backup)

            added, counts = migrate(database, backup)
            self.assertTrue(added)
            self.assertEqual(counts["product"], 2)

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT id, name, category_id, stock, is_active, deleted_at "
                        "FROM product ORDER BY id"
                    ).fetchall(),
                    [
                        (7, "Đang hiện", 1, 4, 1, None),
                        (8, "Đã ẩn", None, 0, 0, None),
                    ],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT product_id, quantity, unit_price FROM order_item"
                    ).fetchall(),
                    [(7, 2, 120000)],
                )
                self.assertEqual(
                    connection.execute("SELECT image_path FROM product_image").fetchall(),
                    [("uploads/anh.jpg",)],
                )
                self.assertEqual(
                    connection.execute("SELECT product_id, rating FROM review").fetchall(),
                    [(7, 5)],
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            added_again, repeated_counts = migrate(database, backup)
            self.assertFalse(added_again)
            self.assertEqual(repeated_counts, counts)

    def test_migration_refuses_a_mismatched_backup_without_mutating_live_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "live.sqlite"
            backup = Path(directory) / "backup.sqlite"
            self._create_database(database)
            self._backup(database, backup)
            with closing(sqlite3.connect(backup)) as connection:
                connection.execute("DELETE FROM product WHERE id = 8")
                connection.commit()

            with self.assertRaisesRegex(RuntimeError, "record counts"):
                migrate(database, backup)

            with closing(sqlite3.connect(database)) as connection:
                self.assertNotIn(
                    "deleted_at",
                    {
                        row[1]
                        for row in connection.execute('PRAGMA table_info("product")')
                    },
                )
