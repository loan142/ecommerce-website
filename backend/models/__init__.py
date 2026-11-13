from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    slug = db.Column(db.String(120), nullable=False, unique=True)

    products = db.relationship("Product", back_populates="category")


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Integer, nullable=False, default=0)
    old_price = db.Column(db.Integer)
    image_url = db.Column(db.String(500), default="")
    stock = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    category = db.relationship("Category", back_populates="products")
    images = db.relationship(
        "ProductImage",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.sort_order, ProductImage.id",
    )

    @property
    def primary_image(self):
        return next((image for image in self.images if image.is_primary), None) or (
            self.images[0] if self.images else None
        )


class ProductImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_path = db.Column(db.String(500), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)

    product = db.relationship("Product", back_populates="images")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="customer")

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    status = db.Column(db.String(30), nullable=False, default="pending")
    total_amount = db.Column(db.Integer, nullable=False, default=0)
    recipient_name = db.Column(db.String(120))
    recipient_phone = db.Column(db.String(30))
    shipping_address = db.Column(db.String(500))
    note = db.Column(db.Text)
    payment_method = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.now)
    checkout_token = db.Column(db.String(64))

    __table_args__ = (
        db.Index("uq_order_checkout_token", "checkout_token", unique=True),
    )

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Integer, nullable=False, default=0)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False, index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, default="")
    status = db.Column(db.String(20), nullable=False, default="pending")
    purchase_verified = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'hidden')", name="ck_review_status"
        ),
        db.Index(
            "uq_review_product_customer",
            "product_id",
            "user_id",
            unique=True,
            sqlite_where=db.text("user_id IS NOT NULL"),
        ),
    )


class ProductComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False, index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="comment")
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    admin_reply = db.Column(db.Text)
    replied_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    replied_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    __table_args__ = (
        db.CheckConstraint(
            "kind IN ('comment', 'question')", name="ck_product_comment_kind"
        ),
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'hidden')",
            name="ck_product_comment_status",
        ),
    )
