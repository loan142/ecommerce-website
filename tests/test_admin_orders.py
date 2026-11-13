import re
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import Order, OrderItem, Product, User, db


class AdminOrderManagementTests(TestCase):
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
                        full_name="Order Admin",
                        email="order-admin@example.test",
                        password_hash=generate_password_hash("test-admin-password"),
                        role="admin",
                    ),
                    User(
                        full_name="Order Customer",
                        email="order-customer@example.test",
                        password_hash=generate_password_hash("test-customer-password"),
                        role="customer",
                    ),
                    Product(
                        name="Bình gốm còn bán",
                        description="Sản phẩm kiểm thử",
                        price=125000,
                        stock=10,
                        is_active=True,
                    ),
                    Product(
                        name="Đĩa gốm đã ẩn",
                        description="Sản phẩm bị ẩn",
                        price=50000,
                        stock=5,
                        is_active=False,
                    ),
                ]
            )
            db.session.commit()
            self.product_id = Product.query.filter_by(
                name="Bình gốm còn bán"
            ).one().id
            self.hidden_product_id = Product.query.filter_by(
                name="Đĩa gốm đã ẩn"
            ).one().id
            self.customer_id = User.query.filter_by(
                email="order-customer@example.test"
            ).one().id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _csrf_token(self):
        response = self.client.get("/auth/login")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _login(
        self, email="order-admin@example.test", password="test-admin-password",
        path="/admin/login",
    ):
        return self.client.post(
            path,
            data={
                "email": email,
                "password": password,
                "csrf_token": self._csrf_token(),
            },
        )

    def _create_order(self, status="pending", user_id=None, product_id=None):
        with self.app.app_context():
            order = Order(user_id=user_id, status=status, total_amount=250000)
            db.session.add(order)
            db.session.flush()
            db.session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product_id or self.product_id,
                    quantity=2,
                    unit_price=125000,
                )
            )
            db.session.commit()
            return order.id

    def _update_status(self, order_id, status, include_csrf=True):
        data = {"status": status}
        if include_csrf:
            data["csrf_token"] = self._csrf_token()
        return self.client.post(f"/admin/orders/{order_id}/status", data=data)

    def test_orders_page_shows_empty_state_and_filters_database_orders(self):
        self._login()
        empty = self.client.get("/admin/orders")
        self.assertEqual(empty.status_code, 200)
        self.assertIn("Chưa có đơn hàng".encode(), empty.data)

        pending_id = self._create_order(user_id=self.customer_id)
        completed_id = self._create_order(status="completed")

        listing = self.client.get("/admin/orders")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(f"#{pending_id}".encode(), listing.data)
        self.assertIn(f"#{completed_id}".encode(), listing.data)
        self.assertIn("Order Customer".encode(), listing.data)
        self.assertIn("250,000₫".encode(), listing.data)
        self.assertIn(b">2</td>", listing.data)

        filtered = self.client.get("/admin/orders?status=completed")
        self.assertEqual(filtered.status_code, 200)
        self.assertIn(f"#{completed_id}".encode(), filtered.data)
        self.assertNotIn(f"#{pending_id}".encode(), filtered.data)

    def test_order_detail_handles_guest_and_hidden_product_with_order_price_snapshot(self):
        order_id = self._create_order(product_id=self.hidden_product_id)
        self._login()

        response = self.client.get(f"/admin/orders/{order_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Khách vãng lai".encode(), response.data)
        self.assertIn("Đĩa gốm đã ẩn".encode(), response.data)
        self.assertIn("Đơn giá lúc đặt".encode(), response.data)
        self.assertIn("250,000₫".encode(), response.data)
        self.assertIn("Đơn hàng cũ chưa có địa chỉ giao hàng.".encode(), response.data)
        self.assertNotIn("Test address".encode(), response.data)

    def test_admin_can_advance_statuses_and_terminal_orders_cannot_change(self):
        order_id = self._create_order()
        self._login()

        for status in ("confirmed", "shipping", "completed"):
            response = self._update_status(order_id, status)
            self.assertEqual(response.status_code, 302)
            with self.app.app_context():
                self.assertEqual(db.session.get(Order, order_id).status, status)

        terminal = self._update_status(order_id, "cancelled")
        self.assertEqual(terminal.status_code, 302)
        self.assertIn("trạng thái cuối cùng".encode(), self.client.get(
            f"/admin/orders/{order_id}"
        ).data)
        with self.app.app_context():
            self.assertEqual(db.session.get(Order, order_id).status, "completed")

    def test_checkout_deducts_stock_and_cancellation_restores_it_only_once(self):
        with self.client.session_transaction() as browser_session:
            browser_session["cart"] = {str(self.product_id): 3}

        checkout_page = self.client.get("/cart/checkout")
        self.assertEqual(checkout_page.status_code, 200)
        placed = self.client.post(
            "/cart/checkout",
            data={
                "full_name": "Guest Buyer",
                "phone": "0900000000",
                "address": "Test address",
                "payment_method": "cod",
                "csrf_token": self._csrf_token(),
            },
        )
        self.assertEqual(placed.status_code, 302)
        with self.app.app_context():
            order = Order.query.one()
            order_id = order.id
            self.assertIsNone(order.user_id)
            self.assertEqual(db.session.get(Product, self.product_id).stock, 7)
            self.assertEqual(OrderItem.query.filter_by(order_id=order_id).one().quantity, 3)

        self._login()
        cancelled = self._update_status(order_id, "cancelled")
        self.assertEqual(cancelled.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Order, order_id).status, "cancelled")
            self.assertEqual(db.session.get(Product, self.product_id).stock, 10)

        repeated_cancel = self._update_status(order_id, "cancelled")
        self.assertEqual(repeated_cancel.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Product, self.product_id).stock, 10)

    def test_invalid_transitions_and_missing_or_invalid_csrf_do_not_change_order(self):
        order_id = self._create_order()
        self._login()

        self.assertEqual(
            self.client.post(
                f"/admin/orders/{order_id}/status",
                data={"status": "completed"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                f"/admin/orders/{order_id}/status",
                data={"status": "cancelled", "csrf_token": "invalid"},
            ).status_code,
            400,
        )
        self.assertEqual(self._update_status(order_id, "not-a-status").status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Order, order_id).status, "pending")
            self.assertEqual(db.session.get(Product, self.product_id).stock, 10)

    def test_only_admin_can_manage_orders_and_missing_orders_return_404(self):
        order_id = self._create_order()
        self.assertEqual(self.client.get("/admin/orders").status_code, 302)
        self.assertEqual(self.client.get(f"/admin/orders/{order_id}").status_code, 302)
        self.assertEqual(self._update_status(order_id, "confirmed").status_code, 302)

        self._login(
            email="order-customer@example.test",
            password="test-customer-password",
            path="/auth/login",
        )
        self.assertEqual(self.client.get("/admin/orders").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/orders/{order_id}").status_code, 403)
        self.assertEqual(self._update_status(order_id, "confirmed").status_code, 403)

        self.client.get("/auth/logout")
        self._login()
        self.assertEqual(self.client.get("/admin/orders/9999").status_code, 404)
        self.assertEqual(self._update_status(9999, "confirmed").status_code, 404)

    def test_database_failure_rolls_back_status_and_stock_refund(self):
        order_id = self._create_order()
        self._login()
        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            product.stock = 8
            db.session.commit()
            session = db.session()
            with patch.object(
                session, "commit", side_effect=SQLAlchemyError("test failure")
            ):
                response = self._update_status(order_id, "cancelled")

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Order, order_id).status, "pending")
            self.assertEqual(db.session.get(Product, self.product_id).stock, 8)
