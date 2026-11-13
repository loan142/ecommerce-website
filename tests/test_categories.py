import re
from unittest import TestCase

from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import Category, Order, OrderItem, Product, User, db


class ProductCategoryTests(TestCase):
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
            self.vase_category = Category(
                name="Bình hoa gốm", slug="binh-hoa-gom"
            )
            self.tea_category = Category(name="Bộ ấm chén", slug="bo-am-chen")
            admin = User(
                full_name="Category Admin",
                email="category-admin@example.test",
                password_hash=generate_password_hash("test-admin-password"),
                role="admin",
            )
            customer = User(
                full_name="Category Customer",
                email="category-customer@example.test",
                password_hash=generate_password_hash("test-customer-password"),
                role="customer",
            )
            db.session.add_all([self.vase_category, self.tea_category, admin, customer])
            db.session.flush()
            self.vase_product = Product(
                name="Lọ hoa đất nung",
                description="Lọ hoa thủ công màu đất",
                price=20000,
                stock=8,
                category=self.vase_category,
            )
            self.expensive_vase = Product(
                name="Bình hoa men lam",
                description="Bình gốm trang trí",
                price=50000,
                stock=5,
                category=self.vase_category,
            )
            self.tea_product = Product(
                name="Bộ ấm chén thủ công",
                description="Bộ trà dùng hằng ngày",
                price=35000,
                stock=6,
                category=self.tea_category,
            )
            self.hidden_product = Product(
                name="Sản phẩm đã ẩn",
                description="Không được xuất hiện",
                price=10000,
                stock=2,
                category=self.vase_category,
                is_active=False,
            )
            self.unclassified_product = Product(
                name="Đĩa cũ chưa phân loại",
                description="Sản phẩm chưa có danh mục",
                price=15000,
                stock=3,
            )
            db.session.add_all(
                [
                    self.vase_product,
                    self.expensive_vase,
                    self.tea_product,
                    self.hidden_product,
                    self.unclassified_product,
                ]
            )
            db.session.commit()
            self.vase_category_id = self.vase_category.id
            self.tea_category_id = self.tea_category.id
            self.vase_product_id = self.vase_product.id
            self.tea_product_id = self.tea_product.id
            self.hidden_product_id = Product.query.filter_by(
                name="Sản phẩm đã ẩn"
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
        self, email="category-admin@example.test", password="test-admin-password",
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

    def _post(self, path, data=None, include_csrf=True):
        payload = dict(data or {})
        if include_csrf:
            payload["csrf_token"] = self._csrf_token()
        return self.client.post(path, data=payload)

    def test_customer_catalog_filters_searches_sorts_and_resets(self):
        response = self.client.get("/products/")
        self.assertEqual(response.status_code, 200)
        for name in (
            "Lọ hoa đất nung",
            "Bình hoa men lam",
            "Bộ ấm chén thủ công",
            "Đĩa cũ chưa phân loại",
        ):
            self.assertIn(name.encode(), response.data)
        self.assertNotIn("Sản phẩm đã ẩn".encode(), response.data)

        selected = self.client.get(
            "/products/?category=binh-hoa-gom&q=hoa&sort=price_desc"
        )
        self.assertEqual(selected.status_code, 200)
        self.assertIn("Bình hoa men lam".encode(), selected.data)
        self.assertIn("Lọ hoa đất nung".encode(), selected.data)
        self.assertNotIn("Bộ ấm chén thủ công".encode(), selected.data)
        self.assertLess(
            selected.data.index("Bình hoa men lam".encode()),
            selected.data.index("Lọ hoa đất nung".encode()),
        )

        search_in_description = self.client.get("/products/?q=d%C3%B9ng")
        self.assertIn("Bộ ấm chén thủ công".encode(), search_in_description.data)
        self.assertNotIn("Lọ hoa đất nung".encode(), search_in_description.data)

        reset = self.client.get("/products/")
        self.assertIn("Đĩa cũ chưa phân loại".encode(), reset.data)
        self.assertNotIn("Đặt lại".encode(), reset.data)

    def test_invalid_category_is_rejected_and_inactive_product_never_leaks(self):
        self.assertEqual(
            self.client.get("/products/?category=not-a-category").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/products/{self.hidden_product_id}").status_code, 404
        )
        self.assertNotIn(
            "Sản phẩm đã ẩn".encode(),
            self.client.get("/products/?sort=price_asc").data,
        )

    def test_admin_can_create_and_edit_categories_without_orphaning_products(self):
        self._login()
        response = self._post("/admin/categories", {"name": "Quà tặng gốm"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            category = Category.query.filter_by(slug="qua-tang-gom").one()
            category_id = category.id

        edit_response = self._post(
            f"/admin/categories/{self.vase_category_id}/edit",
            {"name": "Bình hoa thủ công"},
        )
        self.assertEqual(edit_response.status_code, 302)
        with self.app.app_context():
            category = db.session.get(Category, self.vase_category_id)
            product = db.session.get(Product, self.vase_product_id)
            self.assertEqual(category.slug, "binh-hoa-thu-cong")
            self.assertEqual(product.category_id, self.vase_category_id)
            self.assertIsNotNone(db.session.get(Category, category_id))

        self.assertEqual(
            self.client.delete(
                f"/admin/categories/{self.vase_category_id}",
                data={"csrf_token": self._csrf_token()},
            ).status_code,
            404,
        )

    def test_admin_assigns_categories_and_can_filter_unclassified_products(self):
        self._login()
        created = self._post(
            "/admin/products/new",
            {
                "name": "Bình mới",
                "description": "Bình mới mô tả",
                "price": "25000",
                "old_price": "",
                "image_url": "",
                "stock": "4",
                "is_active": "1",
                "category_id": str(self.vase_category_id),
            },
        )
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            created_product = Product.query.filter_by(name="Bình mới").one()
            self.assertEqual(created_product.category.name, "Bình hoa gốm")

        category_filter = self.client.get(
            f"/admin/products?category={self.vase_category_id}"
        )
        self.assertEqual(category_filter.status_code, 200)
        self.assertIn("Lọ hoa đất nung".encode(), category_filter.data)
        self.assertNotIn("Bộ ấm chén thủ công".encode(), category_filter.data)

        unclassified = self.client.get("/admin/products?category=uncategorized")
        self.assertIn("Đĩa cũ chưa phân loại".encode(), unclassified.data)
        self.assertIn("Chưa phân loại".encode(), unclassified.data)

        invalid_category = self._post(
            "/admin/products/new",
            {
                "name": "Bình sai danh mục",
                "description": "",
                "price": "1000",
                "old_price": "",
                "image_url": "",
                "stock": "1",
                "category_id": "999999",
            },
        )
        self.assertEqual(invalid_category.status_code, 400)
        with self.app.app_context():
            self.assertIsNone(Product.query.filter_by(name="Bình sai danh mục").first())

    def test_categories_are_admin_only_and_mutations_require_csrf(self):
        self._login()
        self.assertEqual(
            self.client.post("/admin/categories", data={"name": "Thiếu CSRF"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                f"/admin/categories/{self.vase_category_id}/edit",
                data={"name": "Thiếu CSRF"},
            ).status_code,
            400,
        )

        self.client.get("/auth/logout")
        self._login(
            "category-customer@example.test",
            "test-customer-password",
            path="/auth/login",
        )
        self.assertEqual(self.client.get("/admin/categories").status_code, 403)
        self.assertEqual(
            self._post("/admin/categories", {"name": "Không được phép"}).status_code,
            403,
        )
        with self.app.app_context():
            self.assertIsNone(Category.query.filter_by(name="Không được phép").first())

    def test_checkout_and_orders_keep_working_with_categorized_products(self):
        with self.client.session_transaction() as browser_session:
            browser_session["cart"] = {str(self.vase_product_id): 2}

        checkout_page = self.client.get("/cart/checkout")
        self.assertEqual(checkout_page.status_code, 200)
        checkout_token = re.search(
            rb'name="checkout_token" value="([^"]+)"', checkout_page.data
        ).group(1).decode()
        placed = self.client.post(
            "/cart/checkout",
            data={
                "csrf_token": self._csrf_token(),
                "checkout_token": checkout_token,
                "recipient_name": "Nguyễn An",
                "recipient_phone": "0901234567",
                "shipping_address": "12 Đường Gốm",
                "note": "",
                "payment_method": "cod",
            },
        )
        self.assertEqual(placed.status_code, 302)
        with self.app.app_context():
            order = Order.query.one()
            item = OrderItem.query.filter_by(order_id=order.id).one()
            self.assertEqual(item.product_id, self.vase_product_id)
            self.assertEqual(item.quantity, 2)
            self.assertEqual(db.session.get(Product, self.vase_product_id).stock, 6)
