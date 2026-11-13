import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from migrations.add_product_reviews import REVIEW_COLUMNS, migrate


class ProductReviewsMigrationTests(TestCase):
    def _create_legacy_database(self, database):
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE product (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE user (id INTEGER PRIMARY KEY, full_name TEXT NOT NULL);
            CREATE TABLE "order" (
                id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT NOT NULL,
                total_amount INTEGER NOT NULL
            );
            CREATE TABLE order_item (
                id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
                unit_price INTEGER NOT NULL
            );
            CREATE TABLE review (
                id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,
                user_id INTEGER, rating INTEGER NOT NULL, comment TEXT
            );
            INSERT INTO product VALUES (7, 'Bình cũ');
            INSERT INTO user VALUES (4, 'Khách cũ');
            INSERT INTO "order" VALUES (42, 4, 'completed', 12345);
            INSERT INTO order_item VALUES (89, 42, 7, 3, 4115);
            INSERT INTO review VALUES (19, 7, 4, 5, 'Đánh giá lịch sử');
            """
        )
        connection.commit()
        return connection

    def test_migration_preserves_existing_data_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "legacy.sqlite"
            backup = Path(temporary_directory) / "legacy-backup.sqlite"
            source = self._create_legacy_database(database)
            backup_connection = sqlite3.connect(backup)
            source.backup(backup_connection)
            backup_connection.close()
            source.close()

            columns, created_comments = migrate(database, backup)
            self.assertEqual(set(columns), set(REVIEW_COLUMNS))
            self.assertTrue(created_comments)

            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT id, product_id, user_id, rating, comment, "
                        "status, purchase_verified, created_at, updated_at FROM review"
                    ).fetchone(),
                    (19, 7, 4, 5, "Đánh giá lịch sử", "pending", 0, None, None),
                )
                self.assertEqual(
                    connection.execute('SELECT * FROM "order"').fetchone(),
                    (42, 4, "completed", 12345),
                )
                self.assertEqual(
                    connection.execute("SELECT * FROM order_item").fetchone(),
                    (89, 42, 7, 3, 4115),
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM product_comment").fetchone()[0],
                    0,
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())
                indexes = {
                    row[1]: row[2]
                    for row in connection.execute("PRAGMA index_list(review)")
                }
                self.assertTrue(indexes["uq_review_product_customer"])
            finally:
                connection.close()

            repeated_columns, repeated_comments = migrate(database, backup)
            self.assertEqual(repeated_columns, [])
            self.assertFalse(repeated_comments)

    def test_migration_refuses_mismatched_backup_without_mutating_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "live.sqlite"
            backup = Path(temporary_directory) / "backup.sqlite"
            source = self._create_legacy_database(database)
            backup_connection = sqlite3.connect(backup)
            source.backup(backup_connection)
            backup_connection.execute("DELETE FROM review")
            backup_connection.commit()
            source.close()
            backup_connection.close()

            with self.assertRaisesRegex(RuntimeError, "data does not match"):
                migrate(database, backup)

            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    {row[1] for row in connection.execute("PRAGMA table_info(review)")},
                    {"id", "product_id", "user_id", "rating", "comment"},
                )
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM review").fetchone()[0], 1)
            finally:
                connection.close()


if __name__ == "__main__":
    import unittest

    unittest.main()
