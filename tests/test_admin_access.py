from contextlib import redirect_stdout
from io import StringIO
import re
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import check_password_hash, generate_password_hash

import create_admin
import reset_admin_password
from backend.app import create_app
from backend.models import Product, User, db


class AdminAccessTests(TestCase):
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
                        full_name="Customer Test",
                        email="customer@example.test",
                        password_hash=generate_password_hash("customer-test-password"),
                        role="customer",
                    ),
                    User(
                        full_name="Admin Test",
                        email="admin@example.test",
                        password_hash=generate_password_hash("admin-test-password"),
                        role="admin",
                    ),
                    User(
                        full_name="Second Admin",
                        email="second-admin@example.test",
                        password_hash=generate_password_hash("second-admin-password"),
                        role="admin",
                    ),
                    Product(
                        name="Test ceramic",
                        description="Test product",
                        price=100,
                        stock=2,
                    ),
                ]
            )
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.drop_all()
            db.session.remove()
            db.engine.dispose()

    def _login(self, email, password, path="/auth/login", client=None):
        client = client or self.client
        response = client.get(path)
        csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        return client.post(
            path,
            data={
                "email": email,
                "password": password,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

    def test_anonymous_user_is_redirected_to_login(self):
        for path in (
            "/admin",
            "/admin/",
            "/admin/products",
            "/admin/categories",
            "/admin/orders",
            "/admin/not-a-route",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/admin/login"))
        unknown_admin_post = self.client.post("/admin/not-a-route")
        self.assertEqual(unknown_admin_post.status_code, 302)
        self.assertTrue(
            unknown_admin_post.headers["Location"].endswith("/admin/login")
        )

    def test_customer_is_forbidden_even_if_session_claims_admin_role(self):
        self._login("customer@example.test", "customer-test-password")
        with self.client.session_transaction() as client_session:
            client_session["role"] = "admin"

        for path in ("/admin/", "/admin/products", "/admin/categories", "/admin/orders"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_role_changes_in_database_apply_to_existing_session(self):
        self._login(
            "admin@example.test", "admin-test-password", path="/admin/login"
        )
        with self.app.app_context():
            admin = User.query.filter_by(email="admin@example.test").one()
            admin.role = "customer"
            db.session.commit()

        self.assertEqual(self.client.get("/admin/").status_code, 403)

    def test_admin_can_access_all_existing_admin_pages(self):
        self._login(
            "admin@example.test", "admin-test-password", path="/admin/login"
        )

        for path in ("/admin/", "/admin/products", "/admin/categories", "/admin/orders"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_admin_login_has_separate_page_without_sidebar(self):
        response = self.client.get("/admin/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"csrf_token", response.data)
        self.assertIn(b'name="email"', response.data)
        self.assertIn(b'name="password"', response.data)
        self.assertNotIn(b"admin-sidebar", response.data)

    def test_admin_login_authenticates_admin_and_redirects_to_dashboard(self):
        response = self._login(
            "admin@example.test",
            "admin-test-password",
            path="/admin/login",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/"))
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_customer_login_at_admin_page_has_generic_failure(self):
        page = self.client.get("/admin/login")
        csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"', page.data
        ).group(1).decode()
        customer_response = self.client.post(
            "/admin/login",
            data={
                "email": "customer@example.test",
                "password": "customer-test-password",
                "csrf_token": csrf_token,
            },
        )
        unknown_response = self.client.post(
            "/admin/login",
            data={
                "email": "missing@example.test",
                "password": "wrong-password",
                "csrf_token": csrf_token,
            },
        )

        self.assertEqual(customer_response.status_code, 200)
        self.assertEqual(unknown_response.status_code, 200)
        self.assertIn("Email hoặc mật khẩu chưa đúng.".encode(), customer_response.data)
        self.assertIn("Email hoặc mật khẩu chưa đúng.".encode(), unknown_response.data)
        with self.client.session_transaction() as client_session:
            self.assertNotIn("user_id", client_session)

    def test_customer_session_cannot_open_admin_login_or_admin_routes(self):
        self._login("customer@example.test", "customer-test-password")

        admin_login_page = self.client.get("/admin/login")
        self.assertEqual(admin_login_page.status_code, 403)
        csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"', admin_login_page.data
        ).group(1).decode()
        with self.client.session_transaction() as client_session:
            customer_user_id = client_session["user_id"]
        admin_post = self.client.post(
            "/admin/login",
            data={
                "email": "admin@example.test",
                "password": "admin-test-password",
                "csrf_token": csrf_token,
            },
        )
        self.assertEqual(admin_post.status_code, 403)
        with self.client.session_transaction() as client_session:
            self.assertEqual(client_session["user_id"], customer_user_id)
        for path in ("/admin/", "/admin/products", "/admin/categories", "/admin/orders"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_signed_in_admin_is_redirected_from_admin_login(self):
        self._login(
            "admin@example.test", "admin-test-password", path="/admin/login"
        )

        response = self.client.get("/admin/login")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/"))

    def test_customer_and_admin_auth_login_are_separate_role_portals(self):
        customer_client = self.app.test_client()
        admin_client = self.app.test_client()

        customer_response = self._login(
            "customer@example.test",
            "customer-test-password",
            client=customer_client,
        )
        admin_response = self._login(
            "admin@example.test",
            "admin-test-password",
            client=admin_client,
        )

        self.assertEqual(customer_response.status_code, 302)
        self.assertTrue(customer_response.headers["Location"].endswith("/"))
        with customer_client.session_transaction() as customer_session:
            self.assertIn("user_id", customer_session)
            self.assertEqual(
                customer_session["user_id"],
                self._user_id("customer@example.test"),
            )
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn(
            "Thông tin đăng nhập không hợp lệ cho cổng khách hàng.".encode(),
            admin_response.data,
        )
        with admin_client.session_transaction() as admin_session:
            self.assertNotIn("user_id", admin_session)

    def test_auth_login_get_does_not_redirect_admin_or_customer_sessions(self):
        for email, password in (
            ("admin@example.test", "admin-test-password"),
            ("customer@example.test", "customer-test-password"),
        ):
            with self.subTest(email=email):
                client = self.app.test_client()
                login_path = (
                    "/admin/login"
                    if email == "admin@example.test"
                    else "/auth/login"
                )
                self._login(email, password, path=login_path, client=client)

                response = client.get("/auth/login")

                self.assertEqual(response.status_code, 200)
                self.assertIn("Chưa có tài khoản?".encode(), response.data)
                with client.session_transaction() as client_session:
                    self.assertEqual(
                        client_session["user_id"],
                        self._user_id(email),
                    )

    def test_admin_credentials_at_customer_login_do_not_replace_existing_session(self):
        self._login("customer@example.test", "customer-test-password")
        with self.client.session_transaction() as client_session:
            customer_user_id = client_session["user_id"]
        response = self._login(
            "admin@example.test",
            "admin-test-password",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Thông tin đăng nhập không hợp lệ cho cổng khách hàng.".encode(),
            response.data,
        )
        with self.client.session_transaction() as client_session:
            self.assertEqual(client_session["user_id"], customer_user_id)
        self.assertEqual(self.client.get("/admin/").status_code, 403)

    def test_customer_credentials_at_admin_login_do_not_create_admin_session(self):
        response = self._login(
            "customer@example.test",
            "customer-test-password",
            path="/admin/login",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Email hoặc mật khẩu chưa đúng.".encode(), response.data)
        with self.client.session_transaction() as client_session:
            self.assertNotIn("user_id", client_session)

    def test_admin_and_customer_clients_keep_independent_sessions_through_logout(self):
        admin_a = self.app.test_client()
        admin_b = self.app.test_client()
        customer_a = self.app.test_client()

        self._login(
            "admin@example.test",
            "admin-test-password",
            path="/admin/login",
            client=admin_a,
        )
        self._login(
            "second-admin@example.test",
            "second-admin-password",
            path="/admin/login",
            client=admin_b,
        )
        self._login(
            "customer@example.test",
            "customer-test-password",
            client=customer_a,
        )

        with admin_a.session_transaction() as session_a:
            admin_a_id = session_a["user_id"]
        with admin_b.session_transaction() as session_b:
            admin_b_id = session_b["user_id"]
        with customer_a.session_transaction() as customer_session:
            customer_id = customer_session["user_id"]

        self.assertNotEqual(admin_a_id, admin_b_id)
        self.assertNotIn(customer_id, (admin_a_id, admin_b_id))
        customer_a.get("/auth/logout")
        self.assertEqual(admin_a.get("/admin/").status_code, 200)
        self.assertEqual(admin_b.get("/admin/").status_code, 200)

        admin_a.get("/admin/logout")
        self.assertEqual(admin_a.get("/admin/").status_code, 302)
        self.assertEqual(admin_b.get("/admin/").status_code, 200)

    def _user_id(self, email):
        with self.app.app_context():
            return User.query.filter_by(email=email).one().id

    def test_login_forms_require_csrf_tokens(self):
        for path, email, password in (
            ("/auth/login", "customer@example.test", "customer-test-password"),
            ("/admin/login", "admin@example.test", "admin-test-password"),
        ):
            with self.subTest(path=path):
                self.client.get(path)
                response = self.client.post(
                    path, data={"email": email, "password": password}
                )
                self.assertEqual(response.status_code, 400)

    def test_login_ignores_external_next_parameters(self):
        customer_path = "/auth/login?next=https://attacker.example/"
        customer_login = self._login(
            "customer@example.test",
            "customer-test-password",
            path=customer_path,
        )
        self.assertTrue(customer_login.headers["Location"].endswith("/"))

        self.client.get("/auth/logout")
        admin_path = "/admin/login?next=https://attacker.example/"
        admin_login = self._login(
            "admin@example.test",
            "admin-test-password",
            path=admin_path,
        )
        self.assertTrue(admin_login.headers["Location"].endswith("/admin/"))

    def test_dashboard_and_navigation_show_signed_in_admin_without_stale_warning(self):
        self._login(
            "admin@example.test", "admin-test-password", path="/admin/login"
        )

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin Test", response.data)
        self.assertIn(b"href=\"/admin/logout\"", response.data)
        self.assertNotIn("Bản mẫu: cần bổ sung xác thực".encode(), response.data)

    def test_customer_navigation_shows_account_and_logout(self):
        self._login("customer@example.test", "customer-test-password")

        response = self.client.get("/")

        self.assertIn(b"Customer Test", response.data)
        self.assertIn(b"href=\"/auth/logout\"", response.data)
        self.assertNotIn(
            b'<a class="header-login" href="/auth/login">', response.data
        )

    def test_logout_removes_admin_access(self):
        self._login(
            "admin@example.test", "admin-test-password", path="/admin/login"
        )
        self.assertEqual(self.client.get("/admin/").status_code, 200)

        logout_response = self.client.get("/admin/logout")
        self.assertEqual(logout_response.status_code, 302)
        self.assertTrue(logout_response.headers["Location"].endswith("/admin/login"))
        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/login"))
        login_page = self.client.get("/")
        self.assertIn("Đăng nhập".encode(), login_page.data)
        self.assertNotIn(b"Admin Test", login_page.data)

    def test_customer_logout_does_not_affect_admin_clients(self):
        admin_client_one = self.app.test_client()
        admin_client_two = self.app.test_client()
        customer_client = self.app.test_client()

        self._login(
            "admin@example.test",
            "admin-test-password",
            path="/admin/login",
            client=admin_client_one,
        )
        self._login(
            "second-admin@example.test",
            "second-admin-password",
            path="/admin/login",
            client=admin_client_two,
        )
        self._login(
            "customer@example.test",
            "customer-test-password",
            client=customer_client,
        )

        logout_response = customer_client.get("/auth/logout")

        self.assertTrue(logout_response.headers["Location"].endswith("/auth/login"))
        self.assertEqual(admin_client_one.get("/admin/").status_code, 200)
        self.assertEqual(admin_client_two.get("/admin/").status_code, 200)

    def test_home_products_and_cart_remain_available(self):
        for path in ("/", "/products/", "/cart/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_admin_cli_creates_hashed_admin_without_printing_password(self):
        password = "local-admin-test-password"
        output = StringIO()
        with (
            patch("create_admin.create_app", return_value=self.app),
            patch("builtins.input", side_effect=["local-admin@example.test", "Local Admin"]),
            patch("create_admin.getpass", side_effect=[password, password]),
            redirect_stdout(output),
        ):
            result = create_admin.main()

        self.assertEqual(result, 0)
        self.assertNotIn(password, output.getvalue())
        with self.app.app_context():
            admin = User.query.filter_by(email="local-admin@example.test").one()
            self.assertEqual(admin.role, "admin")
            self.assertNotEqual(admin.password_hash, password)
            self.assertTrue(check_password_hash(admin.password_hash, password))

        self.assertEqual(
            self._login(
                "local-admin@example.test",
                password,
                path="/admin/login",
            ).status_code,
            302,
        )
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_admin_password_reset_cli_hashes_password_and_does_not_print_it(self):
        password = "new-local-admin-password"
        output = StringIO()
        with (
            patch("reset_admin_password.create_app", return_value=self.app),
            patch("builtins.input", return_value="admin@example.test"),
            patch("reset_admin_password.getpass", side_effect=[password, password]),
            redirect_stdout(output),
        ):
            result = reset_admin_password.main()

        self.assertEqual(result, 0)
        self.assertNotIn(password, output.getvalue())
        with self.app.app_context():
            admin = User.query.filter_by(email="admin@example.test").one()
            self.assertNotEqual(admin.password_hash, password)
            self.assertTrue(check_password_hash(admin.password_hash, password))

    def test_admin_password_reset_cli_refuses_customer_accounts(self):
        output = StringIO()
        with (
            patch("reset_admin_password.create_app", return_value=self.app),
            patch("builtins.input", return_value="customer@example.test"),
            patch("reset_admin_password.getpass") as getpass_mock,
            redirect_stdout(output),
        ):
            result = reset_admin_password.main()

        self.assertEqual(result, 1)
        getpass_mock.assert_not_called()
        self.assertIn("Không tìm thấy tài khoản Admin", output.getvalue())


if __name__ == "__main__":
    import unittest

    unittest.main()
