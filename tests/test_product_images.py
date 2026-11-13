from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest import TestCase

from PIL import Image
from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import Category, Product, ProductImage, User, db


class ProductImageTests(TestCase):
    def setUp(self):
        self.uploads = TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "MAX_CONTENT_LENGTH": 26 * 1024 * 1024,
            }
        )
        self.app.static_folder = self.uploads.name
        self.client = self.app.test_client()
        with self.app.app_context():
            self.category = Category(name="Gốm trang trí", slug="gom-trang-tri")
            db.session.add(self.category)
            db.session.flush()
            self.category_id = self.category.id
            db.session.add_all(
                [
                    User(
                        full_name="Admin",
                        email="image-admin@example.test",
                        password_hash=generate_password_hash("test-password"),
                        role="admin",
                    ),
                    User(
                        full_name="Customer",
                        email="image-customer@example.test",
                        password_hash=generate_password_hash("test-password"),
                        role="customer",
                    ),
                    Product(
                        name="Bình gốm",
                        description="Bình làm thủ công",
                        price=125000,
                        stock=4,
                        is_active=True,
                        category_id=self.category_id,
                    ),
                    Product(
                        name="Sản phẩm ẩn",
                        description="Không hiển thị công khai",
                        price=50000,
                        stock=0,
                        is_active=False,
                        category_id=self.category_id,
                    ),
                ]
            )
            db.session.commit()
            self.product_id = Product.query.filter_by(name="Bình gốm").one().id
            self.hidden_product_id = Product.query.filter_by(name="Sản phẩm ẩn").one().id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.uploads.cleanup()

    @staticmethod
    def _image_bytes(color):
        image = Image.new("RGB", (24, 24), color)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def _csrf_token(self):
        response = self.client.get("/auth/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _login(self, email="image-admin@example.test", path="/admin/login"):
        return self.client.post(
            path,
            data={
                "email": email,
                "password": "test-password",
                "csrf_token": self._csrf_token(),
            },
        )

    def _product_form(self, **overrides):
        values = {
            "name": "Bình gốm cập nhật",
            "description": "Gốm thủ công",
            "price": "125000",
            "old_price": "",
            "image_url": "",
            "stock": "4",
            "is_active": "1",
            "category_id": str(self.category_id),
            "csrf_token": self._csrf_token(),
        }
        values.update(overrides)
        return values

    def _upload(self, product_id, images, **form_values):
        data = self._product_form(**form_values)
        data["images"] = [
            (BytesIO(image), f"photo-{index}.png", "image/png")
            for index, image in enumerate(images)
        ]
        return self.client.post(
            f"/admin/products/{product_id}/edit",
            data=data,
            content_type="multipart/form-data",
        )

    def test_admin_can_upload_single_image_and_it_survives_static_retrieval(self):
        self._login()

        response = self._upload(
            self.product_id,
            [self._image_bytes("red")],
            primary_image="new:0",
            image_order_new_0="1",
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            image = ProductImage.query.filter_by(product_id=self.product_id).one()
            image_path = image.image_path
            self.assertTrue(image.is_primary)
            self.assertEqual(image.sort_order, 0)
        self.assertRegex(image_path, r"^uploads/products/[a-f0-9]{32}\.png$")
        served = self.client.get(f"/static/{image_path}")
        self.assertEqual(served.status_code, 200)
        with Image.open(BytesIO(served.data)) as decoded:
            self.assertEqual(decoded.format, "PNG")

    def test_admin_can_upload_five_images_and_choose_primary_and_order(self):
        self._login()
        images = [self._image_bytes((index * 30, 40, 50)) for index in range(5)]
        form = {
            "primary_image": "new:4",
            **{f"image_order_new_{index}": str(5 - index) for index in range(5)},
        }

        response = self._upload(self.product_id, images, **form)

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            saved = ProductImage.query.filter_by(product_id=self.product_id).all()
            self.assertEqual(len(saved), 5)
            primary = next(image for image in saved if image.is_primary)
            self.assertEqual(primary.sort_order, 0)
            self.assertEqual(
                [image.sort_order for image in sorted(saved, key=lambda item: item.sort_order)],
                list(range(5)),
            )

    def test_upload_more_than_five_or_invalid_content_is_rejected_without_files(self):
        self._login()
        too_many = self._upload(
            self.product_id,
            [self._image_bytes("blue") for _ in range(6)],
        )
        self.assertEqual(too_many.status_code, 400)
        invalid = self._upload(
            self.product_id,
            [b"not an image"],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(list(Path(self.uploads.name).rglob("*.*")), [])
        with self.app.app_context():
            self.assertEqual(ProductImage.query.filter_by(product_id=self.product_id).count(), 0)

    def test_upload_over_five_megabytes_is_rejected(self):
        self._login()
        too_large = b"x" * (5 * 1024 * 1024 + 1)

        response = self._upload(self.product_id, [too_large])

        self.assertEqual(response.status_code, 400)
        self.assertIn("5 MB".encode(), response.data)
        self.assertEqual(list(Path(self.uploads.name).rglob("*.*")), [])

    def test_edit_preserves_images_and_deleting_primary_selects_another(self):
        self._login()
        self.assertEqual(
            self._upload(
                self.product_id,
                [self._image_bytes("red"), self._image_bytes("green")],
                primary_image="new:0",
                image_order_new_0="1",
                image_order_new_1="2",
            ).status_code,
            302,
        )
        with self.app.app_context():
            images = ProductImage.query.filter_by(product_id=self.product_id).order_by(
                ProductImage.sort_order
            ).all()
            first_id = images[0].id
            second_id = images[1].id
            first_path = images[0].image_path

        response = self.client.post(
            f"/admin/products/{self.product_id}/edit",
            data=self._product_form(
                primary_image=str(first_id),
                image_order_2="2",
                delete_image_ids=str(first_id),
            ),
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            saved = ProductImage.query.filter_by(product_id=self.product_id).all()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].id, second_id)
            self.assertTrue(saved[0].is_primary)
            self.assertFalse((Path(self.uploads.name) / first_path).exists())

    def test_customer_and_missing_csrf_cannot_manage_images(self):
        self._login("image-customer@example.test", path="/auth/login")
        customer_response = self.client.post(
            f"/admin/products/{self.product_id}/edit",
            data=self._product_form(),
        )
        self.assertEqual(customer_response.status_code, 403)

        self.client.get("/auth/logout")
        self._login()
        missing_token_data = self._product_form()
        missing_token_data.pop("csrf_token")
        csrf_response = self.client.post(
            f"/admin/products/{self.product_id}/edit",
            data=missing_token_data,
        )
        self.assertEqual(csrf_response.status_code, 400)

    def test_public_gallery_uses_legacy_image_and_hidden_product_stays_private(self):
        with self.app.app_context():
            product = db.session.get(Product, self.product_id)
            product.image_url = "/static/images/legacy.jpg"
            db.session.commit()

        public_detail = self.client.get(f"/products/{self.product_id}")
        hidden_detail = self.client.get(f"/products/{self.hidden_product_id}")

        self.assertEqual(public_detail.status_code, 200)
        self.assertIn(b'/static/images/legacy.jpg', public_detail.data)
        self.assertEqual(hidden_detail.status_code, 404)
