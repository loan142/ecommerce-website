import re
from datetime import datetime
from unittest import TestCase

from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import Order, OrderItem, Product, ProductComment, Review, User, db


class ProductReviewTests(TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
            }
        )
        self.customer = self.app.test_client()
        self.other_customer = self.app.test_client()
        self.admin = self.app.test_client()
        with self.app.app_context():
            customer_a = User(
                full_name="Customer A",
                email="customer-a@example.test",
                password_hash=generate_password_hash("customer-password"),
                role="customer",
            )
            customer_b = User(
                full_name="Customer B",
                email="customer-b@example.test",
                password_hash=generate_password_hash("customer-password"),
                role="customer",
            )
            admin = User(
                full_name="Admin A",
                email="admin-a@example.test",
                password_hash=generate_password_hash("admin-password"),
                role="admin",
            )
            active = Product(name="Bình gốm", price=100, stock=5)
            hidden = Product(name="Bình ẩn", price=100, stock=5, is_active=False)
            deleted = Product(
                name="Bình đã xóa", price=100, stock=5, deleted_at=datetime.now()
            )
            db.session.add_all([customer_a, customer_b, admin, active, hidden, deleted])
            db.session.flush()
            completed = Order(
                user_id=customer_a.id,
                status="completed",
                total_amount=100,
                recipient_name="Customer A",
            )
            pending = Order(
                user_id=customer_b.id,
                status="pending",
                total_amount=100,
                recipient_name="Customer B",
            )
            db.session.add_all([completed, pending])
            db.session.flush()
            db.session.add_all(
                [
                    OrderItem(
                        order_id=completed.id,
                        product_id=active.id,
                        quantity=1,
                        unit_price=100,
                    ),
                    OrderItem(
                        order_id=pending.id,
                        product_id=active.id,
                        quantity=1,
                        unit_price=100,
                    ),
                ]
            )
            db.session.commit()
            self.customer_id = customer_a.id
            self.other_customer_id = customer_b.id
            self.admin_id = admin.id
            self.product_id = active.id
            self.hidden_product_id = hidden.id
            self.deleted_product_id = deleted.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _csrf(self, client):
        response = client.get("/products/{}".format(self.product_id))
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _login(self, client, email, password, path="/auth/login"):
        page = client.get(path)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        return client.post(
            path,
            data={"email": email, "password": password, "csrf_token": token},
            follow_redirects=False,
        )

    def _login_users(self):
        self._login(
            self.customer,
            "customer-a@example.test",
            "customer-password",
        )
        self._login(
            self.other_customer,
            "customer-b@example.test",
            "customer-password",
        )
        self._login(
            self.admin,
            "admin-a@example.test",
            "admin-password",
            path="/admin/login",
        )

    def _submit_review(self, client, comment="Gốm đẹp", rating="5", csrf=True):
        data = {"rating": rating, "comment": comment}
        if csrf:
            data["csrf_token"] = self._csrf(client)
        return client.post(
            f"/products/{self.product_id}/reviews",
            data=data,
            follow_redirects=False,
        )

    def _submit_comment(self, client, content="Còn hàng không?", kind="question"):
        return client.post(
            f"/products/{self.product_id}/comments",
            data={
                "content": content,
                "kind": kind,
                "csrf_token": self._csrf(client),
            },
            follow_redirects=False,
        )

    def test_completed_buyer_can_submit_one_verified_review_and_admin_can_approve(self):
        self._login_users()
        response = self._submit_review(self.customer)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            review = Review.query.one()
            self.assertEqual(review.user_id, self.customer_id)
            self.assertEqual(review.status, "pending")
            self.assertTrue(review.purchase_verified)
            self.assertEqual(review.rating, 5)

        public = self.other_customer.get(f"/products/{self.product_id}")
        self.assertIn(
            "Chưa có đánh giá được duyệt".encode(),
            public.data,
        )
        self.assertIn(b"<strong>0.0</strong>", public.data)
        self.assertNotIn(b"G\xe1\xbb\x91m \xc4\x91\xe1\xba\xb9p", public.data)

        page = self.admin.get("/admin/reviews")
        self.assertEqual(page.status_code, 200)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        moderation = self.admin.post(
            f"/admin/reviews/review/{review.id}/moderate",
            data={"status": "approved", "csrf_token": token},
        )
        self.assertEqual(moderation.status_code, 302)

        public = self.other_customer.get(f"/products/{self.product_id}")
        self.assertIn("Gốm đẹp".encode(), public.data)
        self.assertIn("Đã mua hàng".encode(), public.data)
        self.assertIn(b"<strong>5.0</strong>", public.data)
        self.assertIn("1 đánh giá đã duyệt".encode(), public.data)

    def test_customer_without_completed_purchase_cannot_rate_and_customer_cannot_moderate(self):
        self._login_users()
        response = self._submit_review(self.other_customer)
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(Review.query.count(), 0)
        admin_page = self.other_customer.get("/admin/reviews")
        self.assertEqual(admin_page.status_code, 403)

    def test_customer_can_edit_own_approved_review_and_it_returns_to_pending(self):
        self._login_users()
        self._submit_review(self.customer, "Trước sửa")
        with self.app.app_context():
            review = Review.query.one()
            review.status = "approved"
            db.session.commit()

        self._submit_review(self.customer, "Sau sửa", rating="4")
        with self.app.app_context():
            review = Review.query.one()
            self.assertEqual(review.comment, "Sau sửa")
            self.assertEqual(review.rating, 4)
            self.assertEqual(review.status, "pending")
            self.assertTrue(review.purchase_verified)
        public = self.other_customer.get(f"/products/{self.product_id}")
        self.assertNotIn("Sau sửa".encode(), public.data)

    def test_database_unique_constraint_blocks_duplicate_customer_product_reviews(self):
        self._login_users()
        self._submit_review(self.customer)
        with self.app.app_context():
            db.session.add(
                Review(
                    product_id=self.product_id,
                    user_id=self.customer_id,
                    rating=4,
                    comment="Trùng",
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            self.assertEqual(Review.query.filter_by(user_id=self.customer_id).count(), 1)

    def test_guest_can_read_but_cannot_submit_and_customer_can_comment(self):
        guest_page = self.customer.get(f"/products/{self.product_id}")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', guest_page.data).group(1).decode()
        guest_response = self.customer.post(
            f"/products/{self.product_id}/comments",
            data={"content": "Hỏi khách", "kind": "question", "csrf_token": token},
        )
        self.assertEqual(guest_response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(ProductComment.query.count(), 0)

        self._login(self.customer, "customer-a@example.test", "customer-password")
        response = self._submit_comment(self.customer)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            comment = ProductComment.query.one()
            self.assertEqual(comment.status, "pending")
            comment_id = comment.id
        own_page = self.customer.get(f"/products/{self.product_id}")
        self.assertIn("Chờ duyệt".encode(), own_page.data)
        public_page = self.other_customer.get(f"/products/{self.product_id}")
        self.assertNotIn("Còn hàng không?".encode(), public_page.data)

        with self.app.app_context():
            comment = db.session.get(ProductComment, comment_id)
            comment.status = "approved"
            db.session.commit()
        public_page = self.other_customer.get(f"/products/{self.product_id}")
        self.assertIn("Còn hàng không?".encode(), public_page.data)

    def test_guest_cannot_submit_review_and_published_content_is_paginated(self):
        guest_page = self.customer.get(f"/products/{self.product_id}")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', guest_page.data).group(1).decode()
        denied = self.customer.post(
            f"/products/{self.product_id}/reviews",
            data={"rating": "5", "comment": "Khách vãng lai", "csrf_token": token},
        )
        self.assertEqual(denied.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Review.query.count(), 0)

            db.session.add_all(
                [
                    ProductComment(
                        product_id=self.product_id,
                        user_id=self.customer_id,
                        kind="comment",
                        content=f"Bình luận đã duyệt {index}",
                        status="approved",
                    )
                    for index in range(9)
                ]
            )
            db.session.commit()

        page_one = self.customer.get(f"/products/{self.product_id}?comment_page=1")
        page_two = self.customer.get(f"/products/{self.product_id}?comment_page=2")
        self.assertEqual(page_one.data.count(b'class="feedback-card"'), 8)
        self.assertEqual(page_two.data.count(b'class="feedback-card"'), 1)
        self.assertIn("Bình luận đã duyệt 8".encode(), page_one.data)

    def test_customer_can_only_edit_or_hide_own_comment_and_edits_need_moderation(self):
        self._login_users()
        self._submit_comment(self.customer, content="Câu hỏi của tôi")
        with self.app.app_context():
            comment = ProductComment.query.one()
            comment.status = "approved"
            db.session.commit()
            comment_id = comment.id

        other_token = self._csrf(self.other_customer)
        denied_edit = self.other_customer.post(
            f"/products/comments/{comment_id}/edit",
            data={"content": "Sửa người khác", "kind": "comment", "csrf_token": other_token},
        )
        denied_hide = self.other_customer.post(
            f"/products/comments/{comment_id}/delete",
            data={"csrf_token": other_token},
        )
        self.assertEqual(denied_edit.status_code, 403)
        self.assertEqual(denied_hide.status_code, 403)

        own_token = self._csrf(self.customer)
        edited = self.customer.post(
            f"/products/comments/{comment_id}/edit",
            data={"content": "Câu hỏi đã sửa", "kind": "question", "csrf_token": own_token},
        )
        self.assertEqual(edited.status_code, 302)
        with self.app.app_context():
            comment = db.session.get(ProductComment, comment_id)
            self.assertEqual(comment.content, "Câu hỏi đã sửa")
            self.assertEqual(comment.status, "pending")

        own_token = self._csrf(self.customer)
        hidden = self.customer.post(
            f"/products/comments/{comment_id}/delete",
            data={"csrf_token": own_token},
        )
        self.assertEqual(hidden.status_code, 302)
        with self.app.app_context():
            comment = db.session.get(ProductComment, comment_id)
            self.assertEqual(comment.status, "hidden")
            self.assertEqual(comment.content, "Câu hỏi đã sửa")

    def test_admin_can_approve_hide_and_reply_to_comment(self):
        self._login_users()
        self._submit_comment(self.customer, content="Câu hỏi về chất liệu")
        with self.app.app_context():
            comment = ProductComment.query.one()
            comment_id = comment.id
        page = self.admin.get("/admin/reviews")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        reply = self.admin.post(
            f"/admin/reviews/comments/{comment_id}/reply",
            data={"reply": "Sản phẩm làm từ gốm thủ công.", "csrf_token": token},
        )
        self.assertEqual(reply.status_code, 302)
        approve = self.admin.post(
            f"/admin/reviews/comment/{comment_id}/moderate",
            data={"status": "approved", "csrf_token": token},
        )
        self.assertEqual(approve.status_code, 302)
        public = self.other_customer.get(f"/products/{self.product_id}")
        self.assertIn("Quản trị viên Mộc Market".encode(), public.data)
        self.assertIn("Sản phẩm làm từ gốm thủ công.".encode(), public.data)

        hide = self.admin.post(
            f"/admin/reviews/comment/{comment_id}/moderate",
            data={"status": "hidden", "csrf_token": token},
        )
        self.assertEqual(hide.status_code, 302)
        self.assertNotIn("Câu hỏi về chất liệu".encode(), self.other_customer.get(f"/products/{self.product_id}").data)

    def test_admin_review_and_comment_mutations_require_csrf(self):
        self._login_users()
        self._submit_review(self.customer)
        self._submit_comment(self.customer)
        with self.app.app_context():
            review = Review.query.one()
            comment = ProductComment.query.one()
        self.assertEqual(
            self.admin.post(
                f"/admin/reviews/review/{review.id}/moderate",
                data={"status": "approved"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.admin.post(
                f"/admin/reviews/comments/{comment.id}/reply",
                data={"reply": "Trả lời"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.customer.post(
                f"/products/{self.product_id}/reviews",
                data={"rating": "5", "comment": "No CSRF"},
            ).status_code,
            400,
        )

    def test_review_and_comment_content_is_escaped_and_bounded(self):
        self._login_users()
        self._submit_review(self.customer, "<script>alert(1)</script>")
        self._submit_comment(self.customer, "<img src=x onerror=alert(1)>")
        with self.app.app_context():
            review = Review.query.one()
            review.status = "approved"
            comment = ProductComment.query.one()
            comment.status = "approved"
            db.session.commit()
        page = self.other_customer.get(f"/products/{self.product_id}")
        self.assertNotIn(b"<script>alert(1)</script>", page.data)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", page.data)
        self.assertNotIn(b"<img src=x onerror=alert(1)>", page.data)
        self.assertIn(b"&lt;img src=x onerror=alert(1)&gt;", page.data)

        too_long_review = self._submit_review(self.customer, "x" * 2001)
        self.assertEqual(too_long_review.status_code, 302)
        with self.app.app_context():
            review = Review.query.one()
            self.assertEqual(len(review.comment), len("<script>alert(1)</script>"))

    def test_hidden_and_deleted_products_do_not_accept_new_content_or_show_forms(self):
        self._login_users()
        for product_id in (self.hidden_product_id, self.deleted_product_id):
            with self.subTest(product_id=product_id):
                self.assertEqual(
                    self.customer.get(f"/products/{product_id}").status_code,
                    404,
                )
                token = self._csrf(self.customer)
                response = self.customer.post(
                    f"/products/{product_id}/comments",
                    data={"content": "Không được gửi", "kind": "comment", "csrf_token": token},
                )
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.customer.get(f"/products/{self.product_id}").status_code, 200)

    def test_admin_list_uses_admin_layout_filters_and_pagination(self):
        self._login_users()
        self._submit_review(self.customer)
        self._submit_comment(self.customer)
        response = self.admin.get("/admin/reviews?status=pending")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"admin-sidebar", response.data)
        self.assertIn("Đánh giá &amp; Bình luận".encode(), response.data)
        self.assertIn("Chờ duyệt".encode(), response.data)
        self.assertIn(b"product-feedback", self.customer.get(f"/products/{self.product_id}").data)

    def test_simple_submission_limit_blocks_excessive_new_posts(self):
        self._login_users()
        for index in range(5):
            response = self._submit_comment(self.customer, content=f"Câu hỏi {index}")
            self.assertEqual(response.status_code, 302)
        response = self._submit_comment(self.customer, content="Câu hỏi vượt giới hạn")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(ProductComment.query.filter_by(user_id=self.customer_id).count(), 5)
        page = self.customer.get(f"/products/{self.product_id}")
        self.assertIn("Vui lòng thử lại sau ít phút".encode(), page.data)


if __name__ == "__main__":
    import unittest

    unittest.main()
