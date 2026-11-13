import re
from unittest import TestCase

from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import Order, Product, User, db


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.session.add_all(
                [
                    User(
                        full_name="Dashboard Admin",
                        email="dashboard-admin@example.test",
                        password_hash=generate_password_hash("test-admin-password"),
                        role="admin",
                    ),
                    User(
                        full_name="Dashboard Customer",
                        email="dashboard-customer@example.test",
                        password_hash=generate_password_hash("test-customer-password"),
                        role="customer",
                    ),
                ]
            )
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _login(self, email, password, path="/admin/login"):
        response = self.client.get(path)
        csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        return self.client.post(
            path,
            data={
                "email": email,
                "password": password,
                "csrf_token": csrf_token,
            },
        )

    def test_empty_database_renders_zero_statistics_and_empty_states(self):
        self._login("dashboard-admin@example.test", "test-admin-password")

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<strong>0</strong>", response.data)
        self.assertIn("Chưa có đơn hàng".encode(), response.data)
        self.assertIn("Chưa có cảnh báo tồn kho".encode(), response.data)
        self.assertIn("0₫".encode(), response.data)

    def test_statistics_revenue_chart_and_recent_order_ordering(self):
        statuses = (
            "pending",
            "pending",
            "confirmed",
            "shipping",
            "completed",
            "cancelled",
            "completed",
            "completed",
            "shipping",
            "cancelled",
        )
        amounts = (1000, 2000, 3000, 4000, 100, 99999, 150, 250, 5000, 88888)
        with self.app.app_context():
            db.session.add_all(
                [
                    Product(name="Active plenty", price=100, stock=10, is_active=True),
                    Product(name="Active low one", price=100, stock=1, is_active=True),
                    Product(name="Active low five", price=100, stock=5, is_active=True),
                    Product(name="Active empty", price=100, stock=0, is_active=True),
                    Product(name="Hidden empty", price=100, stock=0, is_active=False),
                    Product(name="Hidden low", price=100, stock=4, is_active=False),
                ]
            )
            db.session.add_all(
                [
                    Order(status=status, total_amount=amount)
                    for status, amount in zip(statuses, amounts)
                ]
            )
            db.session.commit()

        self._login("dashboard-admin@example.test", "test-admin-password")
        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn("<span>Tổng sản phẩm</span><strong>6</strong>", html)
        self.assertIn("<span>Đang hiển thị</span><strong>4</strong>", html)
        self.assertIn("<span>Sắp hết hàng</span><strong>2</strong>", html)
        self.assertIn("<span>Hết hàng</span><strong>2</strong>", html)
        self.assertIn("<span>Tổng đơn hàng</span><strong>10</strong>", html)
        self.assertIn("<span>Chờ xác nhận</span><strong>2</strong>", html)
        self.assertIn("<span>Đang giao</span><strong>2</strong>", html)
        self.assertIn("<span>Hoàn thành</span><strong>3</strong>", html)
        self.assertIn(
            '<span>Doanh thu hoàn thành</span><strong>500₫</strong>', html
        )
        for expected in (
            "Chờ xác nhận: 2 đơn",
            "Đã xác nhận: 1 đơn",
            "Đang giao: 2 đơn",
            "Hoàn thành: 3 đơn",
            "Đã hủy: 2 đơn",
        ):
            self.assertIn(expected, html)

        self.assertIn("Active low one", html)
        self.assertIn("Active empty", html)
        self.assertIn("Hidden empty", html)
        self.assertNotIn("Hidden low", html)
        self.assertIn('href="/admin/orders/10"', html)
        self.assertIn("<td>#10</td>", html)
        self.assertIn("<td>#3</td>", html)
        self.assertNotIn("<td>#2</td>", html)

    def test_dashboard_requires_login_and_forbids_customer(self):
        anonymous_response = self.client.get("/admin/")
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertTrue(anonymous_response.headers["Location"].endswith("/admin/login"))

        self._login(
            "dashboard-customer@example.test",
            "test-customer-password",
            path="/auth/login",
        )
        self.assertEqual(self.client.get("/admin/").status_code, 403)
