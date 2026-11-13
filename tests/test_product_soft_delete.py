import re
from unittest import TestCase

from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import (
    Category,
    Order,
    OrderItem,
    Product,
    ProductImage,
    Review,
    User,
    db,
)


class ProductSoftDeleteTests(TestCase):
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
            category = Category(name="Gốm thử nghiệm", slug="gom-thu-nghiem")
            db.session.add(category)
            db.session.flush()
            self.category_id = category.id
            db.session.add_all(
                [
                    User(
                        full_name="Soft Delete Admin",
                        email="soft-delete-admin@example.test",
                        password_hash=generate_password_hash("test-admin-password"),
                        role="admin",
                    ),
                    User(
                        full_name="Soft Delete Customer",
                        email="soft-delete-customer@example.test",
                        password_hash=generate_password_hash(
                            "test-customer-password"
                        ),
                        role="customer",
                    ),
                ]
            )
            product = Product(
                name="Bình còn mở",
                description="Mô tả sản phẩm cần xóa",
                price=125000,
                old_price=150000,
                stock=7,
                is_active=True,
                category_id=category.id,
                image_url="/static/images/binh.jpg",
            )
            hidden_product = Product(
                name="Đĩa đã ẩn",
                price=80000,
                stock=3,
                is_active=False,
                category_id=category.id,
            )
            db.session.add_all([product, hidden_product])
            db.session.flush()
            self.product_id = product.id
            self.hidden_product_id = hidden_product.id
            db.session.add_all(
                [
                    ProductImage(
                        product_id=product.id,
                        image_path="uploads/binh.jpg",
                        sort_order=0,
                        is_primary=True,
                    ),
                    Order(status="pending", total_amount=250000),
                    Review(
                        product_id=product.id,
                        rating=5,
                        comment="Đẹp",
                    ),
                ]
            )
            db.session.flush()
            order = Order.query.one()
            db.session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=2,
                    unit_price=125000,
                )
            )
            db.session.commit()
            self.order_id = order.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _csrf_token(self):
        response = self.client.get("/auth/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _login(
        self, email="soft-delete-admin@example.test", password="test-admin-password",
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

    def _post(self, path, data=None, csrf=True):
        values = dict(data or {})
        if csrf:
            values["csrf_token"] = self._csrf_token()
        return self.client.post(path, data=values)

    def test_admin_layout_navigation_and_customer_layout_are_separate(self):
        self._login()
        for path, current in (
            ("/admin/", "Dashboard"),
            ("/admin/products", "Sản phẩm"),
            ("/admin/categories", "Danh mục"),
            ("/admin/orders", "Đơn hàng"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"admin-sidebar", response.data)
                self.assertIn(f">{current}</a>".encode(), response.data)
                self.assertNotIn(b"class=\"main-nav\"", response.data)
                self.assertNotIn(b"header-icon", response.data)
                self.assertIn(b'target="_blank"', response.data)
                self.assertIn(b'rel="noopener noreferrer"', response.data)
        for path in (
            "/admin/products/new",
            f"/admin/products/{self.product_id}/edit",
            f"/admin/categories/{self.category_id}/edit",
            f"/admin/orders/{self.order_id}",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"admin-sidebar", response.data)
        self.assertIn(
            b'href="/admin/categories"',
            self.client.get("/admin/").data,
        )

        self.client.get("/auth/logout")
        customer_home = self.client.get("/")
        self.assertNotIn(b"admin-sidebar", customer_home.data)
        self.assertIn(b"class=\"main-nav\"", customer_home.data)

    def test_delete_is_soft_and_customer_surfaces_and_checkout_reject_product(self):
        self._login()
        response = self._post(f"/admin/products/{self.product_id}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("view=trash"))

        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            self.assertIsNotNone(product.deleted_at)
            deleted_at = product.deleted_at
            self.assertEqual(product.stock, 7)
            self.assertEqual(product.category_id, self.category_id)
            self.assertEqual(len(product.images), 1)
            self.assertEqual(OrderItem.query.filter_by(product_id=self.product_id).count(), 1)
            self.assertEqual(Review.query.filter_by(product_id=self.product_id).count(), 1)

        trash = self.client.get("/admin/products?view=trash")
        self.assertIn("Bình còn mở".encode(), trash.data)
        self.assertIn("Khôi phục".encode(), trash.data)
        self.assertIn(deleted_at.strftime("%d/%m/%Y").encode(), trash.data)
        self.assertNotIn("Bình còn mở".encode(), self.client.get("/admin/products").data)
        self.assertIn("Đĩa đã ẩn".encode(), self.client.get("/admin/products?view=hidden").data)

        self.client.get("/auth/logout")
        self.assertNotIn(
            "Bình còn mở".encode(),
            self.client.get("/").data,
        )
        self.assertNotIn(
            "Bình còn mở".encode(),
            self.client.get("/products/?q=Bình").data,
        )
        self.assertEqual(self.client.get(f"/products/{self.product_id}").status_code, 404)
        self.assertEqual(
            self._post(
                f"/cart/add/{self.product_id}",
                csrf=True,
            ).status_code,
            404,
        )

        with self.client.session_transaction() as browser_session:
            browser_session["cart"] = {str(self.product_id): 1}
        checkout = self.client.get("/cart/checkout")
        self.assertEqual(checkout.status_code, 200)
        self.assertIn("không còn được mở bán".encode(), checkout.data)
        self.assertEqual(
            self._post(
                "/cart/checkout",
                {
                    "recipient_name": "Người nhận",
                    "recipient_phone": "0901234567",
                    "shipping_address": "Địa chỉ thử",
                    "payment_method": "cod",
                },
            ).status_code,
            409,
        )
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 1)

    def test_restore_keeps_hidden_state_stock_category_and_images(self):
        self._login()
        self._post(f"/admin/products/{self.hidden_product_id}/delete")
        response = self._post(f"/admin/products/{self.hidden_product_id}/restore")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/products"))

        with self.app.app_context():
            product = db.session.get(Product, self.hidden_product_id)
            self.assertIsNone(product.deleted_at)
            self.assertFalse(product.is_active)
            self.assertEqual(product.stock, 3)
            self.assertEqual(product.category_id, self.category_id)

    def test_repeated_delete_preserves_timestamp_and_dashboard_excludes_trash(self):
        self._login()
        self._post(f"/admin/products/{self.product_id}/delete")
        with self.app.app_context():
            original_deleted_at = db.session.get(Product, self.product_id).deleted_at

        repeated = self._post(f"/admin/products/{self.product_id}/delete")
        self.assertEqual(repeated.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Product, self.product_id).deleted_at,
                original_deleted_at,
            )
        dashboard = self.client.get("/admin/").data.decode()
        self.assertIn("<span>Tổng sản phẩm</span><strong>1</strong>", dashboard)
        self.assertIn("<span>Trong thùng rác</span><strong>1</strong>", dashboard)
        self.assertIn("<span>Đang hiển thị</span><strong>0</strong>", dashboard)

    def test_customer_cannot_delete_or_restore_and_admin_post_requires_csrf(self):
        self._login(
            "soft-delete-customer@example.test",
            "test-customer-password",
            path="/auth/login",
        )
        for action in ("delete", "restore"):
            response = self.client.post(
                f"/admin/products/{self.product_id}/{action}",
                data={"csrf_token": self._csrf_token()},
            )
            self.assertEqual(response.status_code, 403)

        self.client.get("/auth/logout")
        self._login()
        for action in ("delete", "restore"):
            missing_csrf = self._post(
                f"/admin/products/{self.product_id}/{action}", csrf=False
            )
            self.assertEqual(missing_csrf.status_code, 400)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Product, self.product_id).deleted_at)

    def test_deleted_product_remains_visible_in_order_history(self):
        self._login()
        self._post(f"/admin/products/{self.product_id}/delete")
        response = self.client.get(f"/admin/orders/{self.order_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Bình còn mở".encode(), response.data)
        with self.app.app_context():
            self.assertEqual(OrderItem.query.count(), 1)
