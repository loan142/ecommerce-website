import re
from unittest import TestCase

from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import Category, Product, User, db


class AdminProductManagementTests(TestCase):
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
            self.category = Category(name="Bình hoa gốm", slug="binh-hoa-gom")
            db.session.add(self.category)
            db.session.flush()
            self.category_id = self.category.id
            db.session.add_all(
                [
                    User(
                        full_name="Admin Product Test",
                        email="product-admin@example.test",
                        password_hash=generate_password_hash("test-admin-password"),
                        role="admin",
                    ),
                    User(
                        full_name="Customer Product Test",
                        email="product-customer@example.test",
                        password_hash=generate_password_hash("test-customer-password"),
                        role="customer",
                    ),
                    Product(
                        name="Bình gốm thử nghiệm",
                        description="Mô tả thử nghiệm",
                        price=125000,
                        old_price=150000,
                        stock=4,
                        is_active=True,
                        category=self.category,
                    ),
                    Product(
                        name="Đĩa gốm đã ẩn",
                        description="Sản phẩm không hiển thị công khai",
                        price=50000,
                        stock=0,
                        is_active=False,
                    ),
                ]
            )
            db.session.commit()
            self.product_id = Product.query.filter_by(
                name="Bình gốm thử nghiệm"
            ).one().id
            self.hidden_product_id = Product.query.filter_by(
                name="Đĩa gốm đã ẩn"
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
        self, email="product-admin@example.test", password="test-admin-password",
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

    def _valid_product(self, **overrides):
        values = {
            "name": "Bộ ấm trà mới",
            "description": "Gốm thủ công",
            "price": "250000",
            "old_price": "300000",
            "image_url": "/static/images/bo-am-tra.jpg",
            "stock": "7",
            "is_active": "1",
            "category_id": str(self.category_id),
        }
        values.update(overrides)
        return values

    def test_admin_lists_database_products_and_searches_by_name(self):
        self._login()

        response = self.client.get("/admin/products")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Bình gốm thử nghiệm".encode(), response.data)
        self.assertIn("Đĩa gốm đã ẩn".encode(), response.data)

        search_response = self.client.get("/admin/products?q=B%C3%ACnh")
        self.assertEqual(search_response.status_code, 200)
        self.assertIn("Bình gốm thử nghiệm".encode(), search_response.data)
        self.assertNotIn("Đĩa gốm đã ẩn".encode(), search_response.data)

        empty_response = self.client.get("/admin/products?q=khong-co")
        self.assertIn("Không tìm thấy sản phẩm".encode(), empty_response.data)

    def test_admin_can_create_product_without_an_image(self):
        self._login()

        response = self._post(
            "/admin/products/new",
            self._valid_product(image_url="", old_price=""),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/products")
        with self.app.app_context():
            product = Product.query.filter_by(name="Bộ ấm trà mới").one()
            self.assertEqual(product.price, 250000)
            self.assertIsNone(product.old_price)
            self.assertEqual(product.stock, 7)
            self.assertTrue(product.is_active)
            self.assertEqual(product.image_url, "")
        self.assertIn("Bộ ấm trà mới".encode(), self.client.get("/admin/products").data)

    def test_admin_can_edit_product_and_form_shows_existing_values(self):
        self._login()

        form_response = self.client.get(f"/admin/products/{self.product_id}/edit")
        self.assertEqual(form_response.status_code, 200)
        self.assertIn(b'value="125000"', form_response.data)
        self.assertIn(b'value="4"', form_response.data)

        response = self._post(
            f"/admin/products/{self.product_id}/edit",
            self._valid_product(
                name="Bình gốm đã sửa",
                price="175000",
                old_price="200000",
                stock="9",
            ),
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            self.assertEqual(product.name, "Bình gốm đã sửa")
            self.assertEqual(product.price, 175000)
            self.assertEqual(product.stock, 9)

    def test_hide_and_restore_product_affects_customer_catalog_and_detail(self):
        self._login()
        self.assertEqual(self.client.get("/auth/logout").status_code, 302)

        catalog = self.client.get("/products/")
        self.assertEqual(catalog.status_code, 200)
        self.assertIn("Bình gốm thử nghiệm".encode(), catalog.data)
        self.assertNotIn("Đĩa gốm đã ẩn".encode(), catalog.data)
        self.assertEqual(
            self.client.get(f"/products/{self.hidden_product_id}").status_code, 404
        )

        self._login()
        self.assertIn("Đĩa gốm đã ẩn".encode(), self.client.get("/admin/products").data)
        hide_response = self._post(
            f"/admin/products/{self.product_id}/toggle"
        )
        self.assertEqual(hide_response.status_code, 302)
        self.assertEqual(
            self.client.get(f"/products/{self.product_id}").status_code, 404
        )
        self.assertIn("Bình gốm thử nghiệm".encode(), self.client.get("/admin/products").data)

        show_response = self._post(
            f"/admin/products/{self.product_id}/toggle"
        )
        self.assertEqual(show_response.status_code, 302)
        self.assertEqual(
            self.client.get(f"/products/{self.product_id}").status_code, 200
        )

    def test_invalid_product_data_is_rejected_and_form_values_are_preserved(self):
        self._login()
        cases = (
            {"name": "   "},
            {"price": "-1"},
            {"price": "1.5"},
            {"price": "9" * 5000},
            {"old_price": "-10"},
            {"stock": "-1"},
            {"stock": "nhiều"},
            {"image_url": "javascript:alert(1)"},
            {"image_url": "/static/images/../secret.png"},
            {"image_url": "http://example.test/image.jpg"},
        )
        for index, overrides in enumerate(cases):
            with self.subTest(overrides=overrides):
                values = self._valid_product(name=f"Sản phẩm không hợp lệ {index}")
                values.update(overrides)
                response = self._post("/admin/products/new", values)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Vui l\xc3\xb2ng ki\xe1\xbb\x83m tra", response.data)
                self.assertIn(values["name"].encode(), response.data)
        with self.app.app_context():
            self.assertIsNone(
                Product.query.filter_by(name="Sản phẩm không hợp lệ 0").first()
            )

    def test_product_mutations_reject_missing_or_invalid_csrf(self):
        self._login()
        path = f"/admin/products/{self.product_id}/toggle"

        missing_token = self.client.post(path)
        invalid_token = self.client.post(path, data={"csrf_token": "not-the-token"})
        non_ascii_token = self.client.post(path, data={"csrf_token": "không-hợp-lệ"})

        self.assertEqual(missing_token.status_code, 400)
        self.assertEqual(invalid_token.status_code, 400)
        self.assertEqual(non_ascii_token.status_code, 400)
        with self.app.app_context():
            self.assertTrue(db.session.get(Product, self.product_id).is_active)

    def test_anonymous_and_customer_cannot_open_or_mutate_admin_product_pages(self):
        paths = (
            "/admin/products",
            "/admin/products/new",
            f"/admin/products/{self.product_id}/edit",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)
        anonymous_post = self.client.post(
            f"/admin/products/{self.product_id}/toggle"
        )
        self.assertEqual(anonymous_post.status_code, 302)
        self.assertTrue(anonymous_post.headers["Location"].endswith("/admin/login"))

        self._login(
            email="product-customer@example.test",
            password="test-customer-password",
            path="/auth/login",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(
            self._post(f"/admin/products/{self.product_id}/toggle").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                f"/admin/products/{self.product_id}/toggle"
            ).status_code,
            403,
        )

    def test_customer_pages_and_csrf_protected_forms_still_render(self):
        for path in (
            "/",
            "/products/",
            f"/products/{self.product_id}",
            "/cart/",
            "/auth/login",
            "/auth/register",
            "/contact/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

        login_response = self.client.post(
            "/auth/login",
            data={
                "email": "product-customer@example.test",
                "password": "test-customer-password",
                "csrf_token": self._csrf_token(),
            },
        )
        self.assertEqual(login_response.status_code, 302)
        add_to_cart = self._post(
            f"/cart/add/{self.product_id}", {"quantity": "1"}
        )
        self.assertEqual(add_to_cart.status_code, 302)
        self.assertEqual(self.client.get("/cart/").status_code, 200)
        checkout = self._post(
            "/cart/checkout",
            {
                "full_name": "Customer Test",
                "phone": "0900000000",
                "address": "Địa chỉ kiểm thử",
                "payment_method": "cod",
            },
        )
        self.assertEqual(checkout.status_code, 302)

        contact = self._post(
            "/contact/",
            {"name": "Customer Test", "email": "customer@example.test", "message": "Test"},
        )
        self.assertEqual(contact.status_code, 302)


if __name__ == "__main__":
    import unittest

    unittest.main()
