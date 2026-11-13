import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import Order, OrderItem, Product, User, db


class CheckoutTests(TestCase):
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
                        full_name="Checkout Admin",
                        email="checkout-admin@example.test",
                        password_hash=generate_password_hash("test-admin-password"),
                        role="admin",
                    ),
                    Product(
                        name="Bình gốm",
                        price=12000,
                        stock=10,
                        is_active=True,
                    ),
                    Product(
                        name="Đĩa gốm",
                        price=7000,
                        stock=4,
                        is_active=True,
                    ),
                    Product(
                        name="Sản phẩm đã ẩn",
                        price=5000,
                        stock=5,
                        is_active=False,
                    ),
                    Product(
                        name="Sản phẩm hết hàng",
                        price=9000,
                        stock=0,
                        is_active=True,
                    ),
                ]
            )
            db.session.commit()
            self.product_id = Product.query.filter_by(name="Bình gốm").one().id
            self.second_product_id = Product.query.filter_by(name="Đĩa gốm").one().id
            self.hidden_product_id = Product.query.filter_by(
                name="Sản phẩm đã ẩn"
            ).one().id
            self.empty_product_id = Product.query.filter_by(
                name="Sản phẩm hết hàng"
            ).one().id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _set_cart(self, cart):
        with self.client.session_transaction() as browser_session:
            browser_session["cart"] = {
                str(product_id): quantity for product_id, quantity in cart.items()
            }

    def _csrf_token(self, client=None):
        client = client or self.client
        response = client.get("/auth/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _checkout_token(self, client=None):
        client = client or self.client
        response = client.get("/cart/checkout")
        match = re.search(rb'name="checkout_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _valid_data(self):
        return {
            "recipient_name": "Nguyễn Minh An",
            "recipient_phone": "090 123 4567",
            "shipping_address": "12 Đường Gốm, Quận 1, TP. Hồ Chí Minh",
            "note": "Gọi trước khi giao",
            "payment_method": "cod",
        }

    def _login_admin(self):
        self.client.post(
            "/admin/login",
            data={
                "email": "checkout-admin@example.test",
                "password": "test-admin-password",
                "csrf_token": self._csrf_token(),
            },
        )

    def _order_count(self):
        with self.app.app_context():
            return Order.query.count()

    def test_checkout_saves_shipping_details_database_prices_and_clears_cart(self):
        self._set_cart({self.product_id: 2, self.second_product_id: 1})
        token = self._checkout_token()
        data = self._valid_data()
        data.update(
            {
                "csrf_token": self._csrf_token(),
                "checkout_token": token,
                "total_amount": "1",
                "price": "1",
                "quantity": "999",
            }
        )

        response = self.client.post("/cart/checkout", data=data)

        self.assertEqual(response.status_code, 302)
        self.assertRegex(response.headers["Location"], r"/cart/checkout/success/\d+$")
        confirmation = self.client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("Đã nhận đơn hàng của bạn".encode(), confirmation.data)
        self.assertIn("Mã đơn hàng".encode(), confirmation.data)
        with self.app.app_context():
            order = Order.query.one()
            self.assertEqual(order.status, "pending")
            self.assertEqual(order.recipient_name, "Nguyễn Minh An")
            self.assertEqual(order.recipient_phone, "090 123 4567")
            self.assertEqual(
                order.shipping_address, "12 Đường Gốm, Quận 1, TP. Hồ Chí Minh"
            )
            self.assertEqual(order.note, "Gọi trước khi giao")
            self.assertEqual(order.payment_method, "cod")
            self.assertIsNotNone(order.created_at)
            self.assertEqual(order.total_amount, 31000)
            order_id = order.id
            order_items = OrderItem.query.filter_by(order_id=order.id).order_by(
                OrderItem.product_id
            ).all()
            self.assertEqual(
                [(item.quantity, item.unit_price) for item in order_items],
                [(2, 12000), (1, 7000)],
            )
            self.assertEqual(db.session.get(Product, self.product_id).stock, 8)
            self.assertEqual(db.session.get(Product, self.second_product_id).stock, 3)

        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session["cart"], {})

        repeated = self.client.post("/cart/checkout", data=data)
        self.assertEqual(repeated.status_code, 302)
        self.assertTrue(repeated.headers["Location"].endswith(f"/{order_id}"))
        self.assertEqual(self._order_count(), 1)

    def test_admin_can_view_shipping_details_and_old_orders_show_no_invented_data(self):
        self._set_cart({self.product_id: 1})
        data = self._valid_data()
        data.update(
            {
                "csrf_token": self._csrf_token(),
                "checkout_token": self._checkout_token(),
            }
        )
        placed = self.client.post("/cart/checkout", data=data)
        order_id = int(placed.headers["Location"].rsplit("/", 1)[1])

        with self.app.app_context():
            old_order = Order(status="pending", total_amount=100)
            db.session.add(old_order)
            db.session.commit()
            old_order_id = old_order.id
            db.session.execute(
                Order.__table__.update()
                .where(Order.id == old_order_id)
                .values(created_at=None)
            )
            db.session.commit()

        self._login_admin()
        response = self.client.get(f"/admin/orders/{order_id}")
        self.assertEqual(response.status_code, 200)
        for expected in (
            f"#{order_id}",
            "Nguyễn Minh An",
            "090 123 4567",
            "12 Đường Gốm, Quận 1, TP. Hồ Chí Minh",
            "Gọi trước khi giao",
            "Thanh toán khi nhận hàng (COD)",
            "Ngày đặt",
        ):
            self.assertIn(expected.encode(), response.data)

        old_response = self.client.get(f"/admin/orders/{old_order_id}")
        self.assertIn("Đơn hàng cũ chưa có dữ liệu thời gian.".encode(), old_response.data)
        self.assertIn("Đơn hàng cũ chưa có địa chỉ giao hàng.".encode(), old_response.data)
        self.assertNotIn("Nguyễn Minh An".encode(), old_response.data)

    def test_missing_fields_and_invalid_phone_are_reported_and_values_retained(self):
        self._set_cart({self.product_id: 1})
        token = self._checkout_token()
        csrf = self._csrf_token()

        for field, expected_message in (
            ("recipient_name", "Vui lòng nhập họ tên người nhận."),
            ("recipient_phone", "Vui lòng nhập số điện thoại người nhận."),
            ("shipping_address", "Vui lòng nhập địa chỉ giao hàng."),
        ):
            with self.subTest(field=field):
                data = self._valid_data()
                data[field] = ""
                response = self.client.post(
                    "/cart/checkout",
                    data={**data, "csrf_token": csrf, "checkout_token": token},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected_message.encode(), response.data)
                self.assertIn("Gọi trước khi giao".encode(), response.data)

        invalid_phone = self._valid_data()
        invalid_phone["recipient_phone"] = "not-a-phone"
        response = self.client.post(
            "/cart/checkout",
            data={**invalid_phone, "csrf_token": csrf, "checkout_token": token},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Số điện thoại không hợp lệ".encode(), response.data)
        self.assertIn(b'value="not-a-phone"', response.data)
        self.assertEqual(self._order_count(), 0)

    def test_hidden_empty_and_excess_stock_products_cannot_be_ordered(self):
        csrf = self._csrf_token()
        cases = (
            (self.hidden_product_id, 1, "không còn được mở bán"),
            (self.empty_product_id, 1, "đã hết hàng"),
            (self.product_id, 11, "không đủ tồn kho"),
            (self.product_id, 0, "lớn hơn 0"),
        )
        for product_id, quantity, message in cases:
            with self.subTest(product_id=product_id, quantity=quantity):
                self._set_cart({product_id: quantity})
                token = self._checkout_token()
                response = self.client.post(
                    "/cart/checkout",
                    data={
                        **self._valid_data(),
                        "csrf_token": csrf,
                        "checkout_token": token,
                    },
                )
                self.assertIn(response.status_code, (400, 409))
                self.assertIn(message.encode(), response.data)
                self.assertEqual(self._order_count(), 0)

    def test_checkout_post_without_csrf_is_rejected(self):
        self._set_cart({self.product_id: 1})
        response = self.client.post(
            "/cart/checkout",
            data={
                **self._valid_data(),
                "checkout_token": self._checkout_token(),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._order_count(), 0)

    def test_database_failure_rolls_back_order_and_stock(self):
        self._set_cart({self.product_id: 2})
        data = {
            **self._valid_data(),
            "csrf_token": self._csrf_token(),
            "checkout_token": self._checkout_token(),
        }
        with self.app.app_context():
            current_session = db.session()
            with patch.object(
                current_session, "commit", side_effect=SQLAlchemyError("test failure")
            ):
                response = self.client.post("/cart/checkout", data=data)

        self.assertEqual(response.status_code, 500)
        self.assertIn("Giỏ hàng của bạn vẫn được giữ lại".encode(), response.data)
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 0)
            self.assertEqual(OrderItem.query.count(), 0)
            self.assertEqual(db.session.get(Product, self.product_id).stock, 10)

    def test_dashboard_reflects_new_pending_order_and_cancellation_does_not_add_revenue(self):
        self._set_cart({self.product_id: 1})
        self.client.post(
            "/cart/checkout",
            data={
                **self._valid_data(),
                "csrf_token": self._csrf_token(),
                "checkout_token": self._checkout_token(),
            },
        )
        with self.app.app_context():
            order_id = Order.query.one().id

        self._login_admin()
        dashboard = self.client.get("/admin/")
        self.assertIn("<span>Tổng đơn hàng</span><strong>1</strong>".encode(), dashboard.data)
        self.assertIn("<span>Chờ xác nhận</span><strong>1</strong>".encode(), dashboard.data)
        self.assertIn('<span>Doanh thu hoàn thành</span><strong>0₫</strong>'.encode(), dashboard.data)

        csrf = self._csrf_token()
        cancelled = self.client.post(
            f"/admin/orders/{order_id}/status",
            data={"status": "cancelled", "csrf_token": csrf},
        )
        self.assertEqual(cancelled.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Product, self.product_id).stock, 10)
        self.client.post(
            f"/admin/orders/{order_id}/status",
            data={"status": "cancelled", "csrf_token": csrf},
        )
        with self.app.app_context():
            self.assertEqual(db.session.get(Product, self.product_id).stock, 10)

    def test_simultaneous_checkouts_cannot_oversell_sqlite_stock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "concurrent.sqlite"
            database_uri = "sqlite:///" + str(database_path).replace("\\", "/")
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "concurrency-test-secret",
                    "SQLALCHEMY_DATABASE_URI": database_uri,
                }
            )
            with app.app_context():
                db.session.add(
                    Product(name="One item", price=100, stock=1, is_active=True)
                )
                db.session.commit()
                product_id = Product.query.one().id

            barrier = Barrier(2)

            def place_order():
                client = app.test_client()
                with client.session_transaction() as browser_session:
                    browser_session["cart"] = {str(product_id): 1}
                checkout_page = client.get("/cart/checkout")
                token = re.search(
                    rb'name="checkout_token" value="([^"]+)"', checkout_page.data
                ).group(1).decode()
                csrf = self._csrf_token(client)
                barrier.wait(timeout=10)
                return client.post(
                    "/cart/checkout",
                    data={
                        **self._valid_data(),
                        "checkout_token": token,
                        "csrf_token": csrf,
                    },
                )

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda _: place_order(), range(2)))
                successful = [
                    response
                    for response in responses
                    if "/cart/checkout/success/" in response.headers.get("Location", "")
                ]
                self.assertEqual(len(successful), 1)
                with app.app_context():
                    self.assertEqual(Order.query.count(), 1)
                    self.assertEqual(OrderItem.query.count(), 1)
                    self.assertEqual(Product.query.one().stock, 0)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()


if __name__ == "__main__":
    import unittest

    unittest.main()
