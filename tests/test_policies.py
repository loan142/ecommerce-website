from unittest import TestCase

from backend.app import create_app
from backend.models import db


class PoliciesPageTests(TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_policies_page_renders_all_sections_and_contact_links(self):
        response = self.client.get("/policies")

        self.assertEqual(response.status_code, 200)
        for section_id in (
            "dat-hang",
            "thanh-toan",
            "van-chuyen",
            "doi-tra",
            "quy-trinh-doi-tra",
            "hoan-tien",
            "bao-quan",
            "bao-mat",
            "khieu-nai",
            "lien-he-hieu-luc",
        ):
            with self.subTest(section_id=section_id):
                self.assertIn(f'id="{section_id}"'.encode(), response.data)

        self.assertIn(b'href="/contact/"', response.data)
        self.assertNotIn("Nhóm cập nhật thời gian".encode(), response.data)

    def test_policies_page_preserves_key_terms_and_disclaimers(self):
        response = self.client.get("/policies")
        for phrase in (
            "Thanh toán khi nhận hàng (COD)",
            "07 ngày kể từ ngày nhận hàng",
            "48 giờ sau khi nhận",
            "02 ngày làm việc",
            "07–10 ngày làm việc",
            "Website hiện chưa hỗ trợ thanh toán trực tuyến.",
            "không làm mất các quyền hợp pháp của người tiêu dùng",
            "theo quy định pháp luật",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.encode(), response.data)


if __name__ == "__main__":
    import unittest

    unittest.main()
