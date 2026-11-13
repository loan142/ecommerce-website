from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import tempfile
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import (
    Category,
    Order,
    OrderItem,
    Product,
    ProductImage,
    User,
    db,
)


class CustomerOrderTests(TestCase):
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
            customer_a = User(
                full_name="Khách hàng Một",
                email="customer-one@example.test",
                password_hash=generate_password_hash("test-customer-password"),
                role="customer",
            )
            customer_b = User(
                full_name="Khách hàng Hai",
                email="customer-two@example.test",
                password_hash=generate_password_hash("test-customer-password"),
                role="customer",
            )
            admin = User(
                full_name="Quản trị",
                email="orders-admin@example.test",
                password_hash=generate_password_hash("test-admin-password"),
                role="admin",
            )
            self.empty_user = User(
                full_name="Chưa có đơn",
                email="empty-customer@example.test",
                password_hash=generate_password_hash("test-customer-password"),
                role="customer",
            )
            category = Category(name="Gốm thủ công", slug="gom-thu-cong")
            product = Product(
                name="Bình hoa của khách",
                description="Một chiếc bình gốm",
                price=120000,
                image_url="/static/images/legacy-vase.jpg",
                stock=8,
                is_active=True,
                category=category,
            )
            db.session.add_all(
                [customer_a, customer_b, admin, self.empty_user, category, product]
            )
            db.session.flush()
            self.customer_a_id = customer_a.id
            self.customer_b_id = customer_b.id
            self.empty_user_id = self.empty_user.id
            self.admin_id = admin.id
            self.product_id = product.id
            db.session.add(
                ProductImage(
                    product_id=product.id,
                    image_path="uploads/products/vase-primary.jpg",
                    sort_order=0,
                    is_primary=True,
                )
            )
            self.older_order_id = self._add_order(
                self.customer_a_id,
                "pending",
                datetime(2026, 1, 1, 9, 0),
                "Người nhận A",
                "0901111111",
                "Địa chỉ riêng của khách A",
                "Gọi trước khi giao",
                240000,
                [(2, 120000)],
            )
            self.cancelled_order_id = self._add_order(
                self.customer_a_id,
                "cancelled",
                datetime(2026, 2, 1, 10, 0),
                "Người nhận A",
                "0901111111",
                "Địa chỉ riêng của khách A",
                None,
                120000,
                [(1, 120000)],
            )
            self.undated_order_id = self._add_order(
                self.customer_a_id,
                "completed",
                None,
                None,
                None,
                None,
                None,
                0,
                [],
            )
            self.other_customer_order_id = self._add_order(
                self.customer_b_id,
                "confirmed",
                datetime(2026, 3, 1, 11, 0),
                "Người nhận B",
                "0902222222",
                "Địa chỉ riêng của khách B",
                None,
                360000,
                [(3, 120000)],
            )
            self.guest_order_id = self._add_order(
                None,
                "pending",
                datetime(2026, 4, 1, 12, 0),
                "Khách vãng lai",
                "0900000000",
                "Địa chỉ khách vãng lai",
                None,
                120000,
                [(1, 120000)],
            )
            db.session.commit()

    def _add_order(
        self,
        user_id,
        status,
        created_at,
        recipient_name,
        recipient_phone,
        shipping_address,
        note,
        total_amount,
        items,
    ):
        order = Order(
            user_id=user_id,
            status=status,
            created_at=created_at,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            shipping_address=shipping_address,
            note=note,
            payment_method="cod",
            total_amount=total_amount,
        )
        db.session.add(order)
        db.session.flush()
        if created_at is None:
            db.session.execute(
                Order.__table__.update()
                .where(Order.id == order.id)
                .values(created_at=None)
            )
        for quantity, unit_price in items:
            db.session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=self.product_id,
                    quantity=quantity,
                    unit_price=unit_price,
                )
            )
        return order.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _csrf_token(self, client=None):
        client = client or self.client
        response = client.get("/auth/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _login(
        self, email="customer-one@example.test", password="test-customer-password",
        path="/auth/login",
    ):
        return self.client.post(
            path,
            data={
                "email": email,
                "password": password,
                "csrf_token": self._csrf_token(),
            },
        )

    def _checkout_token(self):
        response = self.client.get("/cart/checkout")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="checkout_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    @staticmethod
    def _checkout_data():
        return {
            "recipient_name": "Người nhận sau checkout",
            "recipient_phone": "0903333333",
            "shipping_address": "Địa chỉ giao hàng mới",
            "note": "",
            "payment_method": "cod",
        }

    def test_requires_login_and_customer_navigation_links_to_order_history(self):
        response = self.client.get("/my-orders")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/auth/login"))

        self._login()
        page = self.client.get("/")
        self.assertIn(b"href=\"/my-orders\"", page.data)
        self.assertIn("Đơn hàng của tôi".encode(), page.data)

    def test_list_only_shows_owned_orders_sorted_and_never_guest_or_other_customer(self):
        self._login()

        response = self.client.get("/my-orders")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"#{self.older_order_id}".encode(), response.data)
        self.assertIn(f"#{self.cancelled_order_id}".encode(), response.data)
        self.assertIn(f"#{self.undated_order_id}".encode(), response.data)
        self.assertNotIn(f"#{self.other_customer_order_id}".encode(), response.data)
        self.assertNotIn(f"#{self.guest_order_id}".encode(), response.data)
        self.assertNotIn("Địa chỉ riêng của khách B".encode(), response.data)
        undated_position = response.data.index(f"#{self.undated_order_id}".encode())
        newest_position = response.data.index(f"#{self.cancelled_order_id}".encode())
        oldest_position = response.data.index(f"#{self.older_order_id}".encode())
        self.assertLess(newest_position, oldest_position)
        self.assertLess(oldest_position, undated_position)
        self.assertIn(b">2</dd>", response.data)

    def test_two_customers_see_only_their_own_orders(self):
        self._login()
        customer_a = self.client.get("/my-orders")
        self.assertIn(f"#{self.older_order_id}".encode(), customer_a.data)
        self.assertNotIn(f"#{self.other_customer_order_id}".encode(), customer_a.data)

        self.client.get("/auth/logout")
        self._login("customer-two@example.test")
        customer_b = self.client.get("/my-orders")
        self.assertIn(f"#{self.other_customer_order_id}".encode(), customer_b.data)
        self.assertNotIn(f"#{self.older_order_id}".encode(), customer_b.data)
        self.assertNotIn(f"#{self.guest_order_id}".encode(), customer_b.data)

    def test_status_filter_and_empty_account_state(self):
        self._login()
        cancelled = self.client.get("/my-orders?status=cancelled")
        self.assertEqual(cancelled.status_code, 200)
        self.assertIn(f"#{self.cancelled_order_id}".encode(), cancelled.data)
        self.assertNotIn(f"#{self.older_order_id}".encode(), cancelled.data)

        self.client.get("/auth/logout")
        self._login("empty-customer@example.test")
        empty = self.client.get("/my-orders")
        self.assertEqual(empty.status_code, 200)
        self.assertIn("Bạn chưa có đơn hàng nào".encode(), empty.data)

    def test_ownership_check_returns_404_for_another_customers_and_guests_orders(self):
        self._login()
        for order_id in (self.other_customer_order_id, self.guest_order_id, 999999):
            with self.subTest(order_id=order_id):
                response = self.client.get(f"/my-orders/{order_id}")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Địa chỉ riêng của khách B".encode(), response.data)

    def test_detail_shows_owned_shipping_items_prices_images_and_safe_legacy_values(self):
        self._login()

        response = self.client.get(f"/my-orders/{self.older_order_id}")

        self.assertEqual(response.status_code, 200)
        for expected in (
            f"#{self.older_order_id}",
            "Người nhận A",
            "0901111111",
            "Địa chỉ riêng của khách A",
            "Gọi trước khi giao",
            "Thanh toán khi nhận hàng (COD)",
            "Bình hoa của khách",
            "2 sản phẩm",
            "120,000₫",
            "240,000₫",
            "/static/uploads/products/vase-primary.jpg",
        ):
            self.assertIn(expected.encode(), response.data)
        self.assertNotIn("Địa chỉ riêng của khách B".encode(), response.data)

        old_order = self.client.get(f"/my-orders/{self.undated_order_id}")
        self.assertEqual(old_order.status_code, 200)
        self.assertIn("Đơn hàng cũ chưa lưu thời gian đặt.".encode(), old_order.data)
        self.assertIn("Chưa lưu địa chỉ giao hàng.".encode(), old_order.data)
        self.assertNotIn("None".encode(), old_order.data)

    def test_checkout_links_logged_in_order_and_keeps_guest_order_unclaimed(self):
        self._login()
        with self.client.session_transaction() as browser_session:
            browser_session["cart"] = {str(self.product_id): 2}
        order_response = self.client.post(
            "/cart/checkout",
            data={
                **self._checkout_data(),
                "checkout_token": self._checkout_token(),
                "csrf_token": self._csrf_token(),
            },
        )
        self.assertEqual(order_response.status_code, 302)
        order_id = int(order_response.headers["Location"].rsplit("/", 1)[1])
        with self.app.app_context():
            order = db.session.get(Order, order_id)
            self.assertEqual(order.user_id, self.customer_a_id)
            self.assertEqual(OrderItem.query.filter_by(order_id=order_id).one().quantity, 2)
        history = self.client.get("/my-orders")
        self.assertIn(f"#{order_id}".encode(), history.data)
        detail = self.client.get(f"/my-orders/{order_id}")
        self.assertIn("Địa chỉ giao hàng mới".encode(), detail.data)
        self.assertEqual(self.client.get(f"/my-orders/{self.guest_order_id}").status_code, 404)

        self.client.get("/auth/logout")
        with self.client.session_transaction() as browser_session:
            browser_session["cart"] = {str(self.product_id): 1}
        guest_order_response = self.client.post(
            "/cart/checkout",
            data={
                **self._checkout_data(),
                "checkout_token": self._checkout_token(),
                "csrf_token": self._csrf_token(),
            },
        )
        self.assertEqual(guest_order_response.status_code, 302)
        guest_order_id = int(guest_order_response.headers["Location"].rsplit("/", 1)[1])
        with self.app.app_context():
            self.assertIsNone(db.session.get(Order, guest_order_id).user_id)
        self._login("customer-two@example.test")
        self.assertEqual(
            self.client.get(f"/my-orders/{guest_order_id}").status_code, 404
        )

    def test_customer_observes_admin_status_change_but_cannot_change_status(self):
        self._login()
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Order, self.older_order_id).status, "pending"
            )
        self.assertEqual(
            self.client.post(
                f"/my-orders/{self.older_order_id}",
                data={"csrf_token": self._csrf_token()},
            ).status_code,
            405,
        )

        self.client.get("/auth/logout")
        self._login(
            "orders-admin@example.test",
            "test-admin-password",
            path="/admin/login",
        )
        response = self.client.post(
            f"/admin/orders/{self.older_order_id}/status",
            data={"status": "confirmed", "csrf_token": self._csrf_token()},
        )
        self.assertEqual(response.status_code, 302)

        self.client.get("/auth/logout")
        self._login()
        updated_history = self.client.get("/my-orders")
        self.assertIn("Đã xác nhận".encode(), updated_history.data)
        updated_detail = self.client.get(f"/my-orders/{self.older_order_id}")
        self.assertIn("Đã xác nhận".encode(), updated_detail.data)

    def test_customer_cancels_pending_order_once_and_order_remains_in_history(self):
        self._login()
        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            product.stock = 5
            db.session.commit()

        response = self.client.post(
            f"/my-orders/{self.older_order_id}/cancel",
            data={"csrf_token": self._csrf_token()},
        )
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(response.headers["Location"])
        self.assertIn(
            f"Đơn hàng #{self.older_order_id} đã được hủy".encode(), detail.data
        )
        self.assertIn("Đã hủy".encode(), detail.data)
        history = self.client.get("/my-orders")
        self.assertIn(f"#{self.older_order_id}".encode(), history.data)
        self.assertIn("Đã hủy".encode(), history.data)
        self.assertNotIn(
            f"/my-orders/{self.older_order_id}/cancel".encode(), history.data
        )
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Order, self.older_order_id).status, "cancelled"
            )
            self.assertEqual(db.session.get(Product, self.product_id).stock, 7)

        repeated = self.client.post(
            f"/my-orders/{self.older_order_id}/cancel",
            data={"csrf_token": self._csrf_token()},
        )
        self.assertEqual(repeated.status_code, 302)
        repeated_detail = self.client.get(repeated.headers["Location"])
        self.assertIn("Chỉ đơn hàng đang chờ xác nhận".encode(), repeated_detail.data)
        with self.app.app_context():
            self.assertEqual(db.session.get(Product, self.product_id).stock, 7)

    def test_customer_cancellation_hides_other_orders_and_allows_only_pending(self):
        with self.app.app_context():
            self.shipping_order_id = self._add_order(
                self.customer_a_id,
                "shipping",
                datetime(2026, 5, 1, 10, 0),
                "Người nhận A",
                "0901111111",
                "Địa chỉ riêng của khách A",
                None,
                120000,
                [(1, 120000)],
            )
            self.confirmed_order_id = self._add_order(
                self.customer_a_id,
                "confirmed",
                datetime(2026, 5, 2, 10, 0),
                "Người nhận A",
                "0901111111",
                "Địa chỉ riêng của khách A",
                None,
                120000,
                [(1, 120000)],
            )
            db.session.commit()
        self._login()
        csrf = self._csrf_token()

        for order_id in (
            self.other_customer_order_id,
            self.guest_order_id,
            999999,
        ):
            with self.subTest(order_id=order_id):
                response = self.client.post(
                    f"/my-orders/{order_id}/cancel",
                    data={"csrf_token": csrf},
                )
                self.assertEqual(response.status_code, 404)

        for order_id, status in (
            (self.confirmed_order_id, "confirmed"),
            (self.shipping_order_id, "shipping"),
            (self.undated_order_id, "completed"),
            (self.cancelled_order_id, "cancelled"),
        ):
            with self.subTest(status=status):
                response = self.client.post(
                    f"/my-orders/{order_id}/cancel",
                    data={"csrf_token": csrf},
                )
                self.assertEqual(response.status_code, 302)
                detail = self.client.get(response.headers["Location"])
                if status == "confirmed":
                    self.assertIn(
                        "đã được Admin xác nhận".encode(), detail.data
                    )
                else:
                    self.assertIn(
                        "Chỉ đơn hàng đang chờ xác nhận".encode(), detail.data
                    )
                with self.app.app_context():
                    self.assertEqual(db.session.get(Order, order_id).status, status)
        with self.app.app_context():
            self.assertEqual(db.session.get(Product, self.product_id).stock, 8)

    def test_cancel_route_requires_login_and_csrf(self):
        self.assertEqual(
            self.client.get(
                f"/my-orders/{self.older_order_id}/cancel"
            ).status_code,
            405,
        )
        self.client.get("/auth/login")
        with self.client.session_transaction() as browser_session:
            csrf = browser_session["csrf_token"]
        anonymous = self.client.post(
            f"/my-orders/{self.older_order_id}/cancel",
            data={"csrf_token": csrf},
        )
        self.assertEqual(anonymous.status_code, 302)
        self.assertTrue(anonymous.headers["Location"].endswith("/auth/login"))

        self._login()
        for data in ({}, {"csrf_token": "invalid-token"}):
            with self.subTest(data=data):
                response = self.client.post(
                    f"/my-orders/{self.older_order_id}/cancel",
                    data=data,
                )
                self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Order, self.older_order_id).status, "pending"
            )
            self.assertEqual(db.session.get(Product, self.product_id).stock, 8)

    def test_cancel_transaction_rolls_back_status_and_stock_on_commit_error(self):
        self._login()
        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            product.stock = 5
            db.session.commit()
            current_session = db.session()
            with patch.object(
                current_session, "commit", side_effect=SQLAlchemyError("test failure")
            ):
                response = self.client.post(
                    f"/my-orders/{self.older_order_id}/cancel",
                    data={"csrf_token": self._csrf_token()},
                )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Order, self.older_order_id).status, "pending"
            )
            self.assertEqual(db.session.get(Product, self.product_id).stock, 5)

    def test_admin_dashboard_and_order_list_show_customer_cancellation(self):
        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            product.stock = 5
            db.session.commit()
        self._login()
        response = self.client.post(
            f"/my-orders/{self.older_order_id}/cancel",
            data={"csrf_token": self._csrf_token()},
        )
        self.assertEqual(response.status_code, 302)
        self.client.get("/auth/logout")
        self._login(
            "orders-admin@example.test",
            "test-admin-password",
            path="/admin/login",
        )

        dashboard = self.client.get("/admin/")
        orders = self.client.get("/admin/orders?status=cancelled")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Đã hủy: 2 đơn".encode(), dashboard.data)
        self.assertEqual(orders.status_code, 200)
        self.assertIn(f"#{self.older_order_id}".encode(), orders.data)

    def test_admin_cancellation_uses_same_safe_transition_and_restores_stock(self):
        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            product.stock = 5
            db.session.commit()
        self._login(
            "orders-admin@example.test",
            "test-admin-password",
            path="/admin/login",
        )

        first = self.client.post(
            f"/admin/orders/{self.older_order_id}/status",
            data={"status": "cancelled", "csrf_token": self._csrf_token()},
        )
        second = self.client.post(
            f"/admin/orders/{self.older_order_id}/status",
            data={"status": "cancelled", "csrf_token": self._csrf_token()},
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Order, self.older_order_id).status, "cancelled"
            )
            self.assertEqual(db.session.get(Product, self.product_id).stock, 7)


class ConcurrentOrderTransitionTests(TestCase):
    def test_admin_confirmation_racing_customer_cancel_never_double_restores_stock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "orders.sqlite"
            database_uri = "sqlite:///" + str(database_path).replace("\\", "/")
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "concurrency-test-secret",
                    "SQLALCHEMY_DATABASE_URI": database_uri,
                }
            )
            with app.app_context():
                customer = User(
                    full_name="Race Customer",
                    email="race-customer@example.test",
                    password_hash=generate_password_hash("test-password"),
                    role="customer",
                )
                admin = User(
                    full_name="Race Admin",
                    email="race-admin@example.test",
                    password_hash=generate_password_hash("test-password"),
                    role="admin",
                )
                product = Product(
                    name="Race Product", price=100, stock=7, is_active=True
                )
                db.session.add_all([customer, admin, product])
                db.session.flush()
                order = Order(
                    user_id=customer.id,
                    status="pending",
                    total_amount=200,
                    payment_method="cod",
                )
                db.session.add(order)
                db.session.flush()
                db.session.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=product.id,
                        quantity=2,
                        unit_price=100,
                    )
                )
                order_id, customer_id, admin_id, product_id = (
                    order.id,
                    customer.id,
                    admin.id,
                    product.id,
                )
                db.session.commit()

            customer_client = app.test_client()
            admin_client = app.test_client()
            with customer_client.session_transaction() as client_session:
                client_session["user_id"] = customer_id
                client_session["csrf_token"] = "customer-csrf-token"
            with admin_client.session_transaction() as client_session:
                client_session["user_id"] = admin_id
                client_session["csrf_token"] = "admin-csrf-token"

            barrier = Barrier(2)

            def cancel_order():
                barrier.wait(timeout=10)
                return customer_client.post(
                    f"/my-orders/{order_id}/cancel",
                    data={"csrf_token": "customer-csrf-token"},
                )

            def confirm_order():
                barrier.wait(timeout=10)
                return admin_client.post(
                    f"/admin/orders/{order_id}/status",
                    data={
                        "status": "confirmed",
                        "csrf_token": "admin-csrf-token",
                    },
                )

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    cancel_result, confirm_result = list(
                        executor.map(
                            lambda operation: operation(),
                            (cancel_order, confirm_order),
                        )
                    )
                self.assertEqual(cancel_result.status_code, 302)
                self.assertEqual(confirm_result.status_code, 302)
                with app.app_context():
                    final_order = db.session.get(Order, order_id)
                    final_stock = db.session.get(Product, product_id).stock
                    self.assertIn(final_order.status, {"cancelled", "confirmed"})
                    if final_order.status == "cancelled":
                        self.assertEqual(final_stock, 9)
                    else:
                        self.assertEqual(final_stock, 7)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
