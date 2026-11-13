from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.csrf import validate_csrf_token
from backend.models import Category, Order, OrderItem, Product, ProductComment, Review, User, db

products_bp = Blueprint("products", __name__)
REVIEW_CONTENT_MAX_LENGTH = 2000
COMMENT_CONTENT_MAX_LENGTH = 2000
MAX_SUBMISSIONS_PER_WINDOW = 5
SUBMISSION_WINDOW = timedelta(minutes=10)
PUBLIC_PAGE_SIZE = 8


def customer_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("auth.login"))

        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            return redirect(url_for("auth.login"))
        if user.role != "customer":
            abort(403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            validate_csrf_token()
        return view(user, *args, **kwargs)

    return wrapped_view


def _submission_limit_reached(user_id):
    cutoff = datetime.now() - SUBMISSION_WINDOW
    recent_reviews = Review.query.filter(
        Review.user_id == user_id,
        Review.created_at >= cutoff,
    ).count()
    recent_comments = ProductComment.query.filter(
        ProductComment.user_id == user_id,
        ProductComment.created_at >= cutoff,
    ).count()
    return recent_reviews + recent_comments >= MAX_SUBMISSIONS_PER_WINDOW


def _product_accepts_content(product_id):
    return Product.query.filter(
        Product.id == product_id,
        Product.is_active.is_(True),
        Product.deleted_at.is_(None),
    ).first()


def _customer_has_completed_purchase(user_id, product_id):
    return (
        db.session.query(OrderItem.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.user_id == user_id,
            Order.status == "completed",
            OrderItem.product_id == product_id,
        )
        .first()
        is not None
    )


def _validated_content(value, max_length):
    if not isinstance(value, str):
        return None
    content = value.strip()
    if not content or len(content) > max_length:
        return None
    return content


def _page_number(parameter):
    try:
        return max(1, int(request.args.get(parameter, "1")))
    except (TypeError, ValueError):
        return 1


@products_bp.get("/")
def list_products():
    keyword = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest")
    selected_category = request.args.get("category", "").strip()
    categories = Category.query.order_by(Category.name.asc()).all()
    query = Product.query.filter(
        Product.is_active.is_(True), Product.deleted_at.is_(None)
    )
    if keyword:
        query = query.filter(
            or_(
                Product.name.ilike(f"%{keyword}%"),
                Product.description.ilike(f"%{keyword}%"),
            )
        )
    if selected_category:
        category = Category.query.filter_by(slug=selected_category).first()
        if category is None:
            abort(404)
        query = query.filter(Product.category_id == category.id)

    if sort == "price_asc":
        query = query.order_by(Product.price.asc(), Product.id.asc())
    elif sort == "price_desc":
        query = query.order_by(Product.price.desc(), Product.id.asc())
    else:
        sort = "newest"
        query = query.order_by(Product.id.desc())

    products = query.all()
    return render_template(
        "products.html",
        products=products,
        categories=categories,
        keyword=keyword,
        sort=sort,
        selected_category=selected_category,
    )


@products_bp.get("/<int:product_id>")
def product_detail(product_id):
    product = Product.query.filter(
        Product.id == product_id,
        Product.is_active.is_(True),
        Product.deleted_at.is_(None),
    ).first_or_404()

    user_id = session.get("user_id")
    current_user = db.session.get(User, user_id) if user_id is not None else None
    if user_id is not None and current_user is None:
        session.clear()

    review_stats = {
        rating: count
        for rating, count in (
            db.session.query(Review.rating, func.count(Review.id))
            .filter(
                Review.product_id == product.id,
                Review.status == "approved",
                Review.rating.between(1, 5),
            )
            .group_by(Review.rating)
            .all()
        )
    }
    review_count = sum(review_stats.values())
    average_rating = (
        db.session.query(func.avg(Review.rating))
        .filter(
            Review.product_id == product.id,
            Review.status == "approved",
            Review.rating.between(1, 5),
        )
        .scalar()
    )
    own_review = None
    can_review = False
    if current_user is not None and current_user.role == "customer":
        own_review = Review.query.filter_by(
            product_id=product.id, user_id=current_user.id
        ).first()
        can_review = _customer_has_completed_purchase(current_user.id, product.id)

    review_page = _page_number("review_page")
    approved_reviews_query = (
        db.session.query(Review, User.full_name)
        .outerjoin(User, User.id == Review.user_id)
        .filter(Review.product_id == product.id, Review.status == "approved")
    )
    reviews = (
        approved_reviews_query.order_by(
            Review.created_at.desc(), Review.id.desc()
        )
        .offset((review_page - 1) * PUBLIC_PAGE_SIZE)
        .limit(PUBLIC_PAGE_SIZE)
        .all()
    )
    review_pages = max(
        1, (approved_reviews_query.count() + PUBLIC_PAGE_SIZE - 1) // PUBLIC_PAGE_SIZE
    )
    if review_page > review_pages:
        review_page = review_pages
        reviews = (
            approved_reviews_query.order_by(
                Review.created_at.desc(), Review.id.desc()
            )
            .offset((review_page - 1) * PUBLIC_PAGE_SIZE)
            .limit(PUBLIC_PAGE_SIZE)
            .all()
        )

    comments_query = (
        db.session.query(ProductComment, User.full_name)
        .join(User, User.id == ProductComment.user_id)
        .filter(ProductComment.product_id == product.id)
    )
    if current_user is None:
        comments_query = comments_query.filter(
            ProductComment.status == "approved"
        )
    else:
        comments_query = comments_query.filter(
            or_(
                ProductComment.status == "approved",
                ProductComment.user_id == current_user.id,
            )
        )
    comment_page = _page_number("comment_page")
    comments = (
        comments_query.order_by(
            ProductComment.created_at.desc(), ProductComment.id.desc()
        )
        .offset((comment_page - 1) * PUBLIC_PAGE_SIZE)
        .limit(PUBLIC_PAGE_SIZE)
        .all()
    )
    comment_pages = max(
        1, (comments_query.count() + PUBLIC_PAGE_SIZE - 1) // PUBLIC_PAGE_SIZE
    )
    if comment_page > comment_pages:
        comment_page = comment_pages
        comments = (
            comments_query.order_by(
                ProductComment.created_at.desc(), ProductComment.id.desc()
            )
            .offset((comment_page - 1) * PUBLIC_PAGE_SIZE)
            .limit(PUBLIC_PAGE_SIZE)
            .all()
        )

    return render_template(
        "product-detail.html",
        product=product,
        current_user=current_user,
        review_stats=review_stats,
        review_count=review_count,
        average_rating=average_rating,
        reviews=reviews,
        review_page=review_page,
        review_pages=review_pages,
        own_review=own_review,
        can_review=can_review,
        comments=comments,
        comment_page=comment_page,
        comment_pages=comment_pages,
    )


@products_bp.post("/<int:product_id>/reviews")
@customer_required
def submit_review(current_user, product_id):
    product = _product_accepts_content(product_id)
    if product is None:
        abort(404)
    if not _customer_has_completed_purchase(current_user.id, product.id):
        abort(403)

    try:
        rating = int(request.form.get("rating", ""))
    except (TypeError, ValueError):
        rating = 0
    content = _validated_content(
        request.form.get("comment"), REVIEW_CONTENT_MAX_LENGTH
    )
    if rating not in range(1, 6) or content is None:
        flash("Vui lòng chọn từ 1 đến 5 sao và nhập đánh giá tối đa 2.000 ký tự.")
        return redirect(url_for("products.product_detail", product_id=product.id))
    if _submission_limit_reached(current_user.id):
        flash("Bạn đã gửi nhiều nội dung gần đây. Vui lòng thử lại sau ít phút.")
        return redirect(url_for("products.product_detail", product_id=product.id))

    review = Review.query.filter_by(
        product_id=product.id, user_id=current_user.id
    ).first()
    now = datetime.now()
    if review is None:
        review = Review(
            product_id=product.id,
            user_id=current_user.id,
            rating=rating,
            comment=content,
            status="pending",
            purchase_verified=True,
            created_at=now,
            updated_at=now,
        )
        db.session.add(review)
    else:
        review.rating = rating
        review.comment = content
        review.status = "pending"
        review.updated_at = now

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Bạn đã gửi đánh giá cho sản phẩm này.")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            "Could not save product review for product %s", product.id
        )
        flash("Không thể lưu đánh giá lúc này. Vui lòng thử lại.")
    else:
        flash("Đánh giá đã được gửi và đang chờ Admin duyệt.")
    return redirect(url_for("products.product_detail", product_id=product.id))


@products_bp.post("/<int:product_id>/comments")
@customer_required
def submit_product_comment(current_user, product_id):
    product = _product_accepts_content(product_id)
    if product is None:
        abort(404)
    content = _validated_content(
        request.form.get("content"), COMMENT_CONTENT_MAX_LENGTH
    )
    kind = request.form.get("kind", "comment")
    if content is None or kind not in {"comment", "question"}:
        flash("Vui lòng nhập bình luận hoặc câu hỏi tối đa 2.000 ký tự.")
        return redirect(url_for("products.product_detail", product_id=product.id))
    if _submission_limit_reached(current_user.id):
        flash("Bạn đã gửi nhiều nội dung gần đây. Vui lòng thử lại sau ít phút.")
        return redirect(url_for("products.product_detail", product_id=product.id))

    now = datetime.now()
    comment = ProductComment(
        product_id=product.id,
        user_id=current_user.id,
        kind=kind,
        content=content,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.session.add(comment)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            "Could not save product comment for product %s", product.id
        )
        flash("Không thể lưu bình luận lúc này. Vui lòng thử lại.")
    else:
        flash("Bình luận đã được gửi và đang chờ Admin duyệt.")
    return redirect(url_for("products.product_detail", product_id=product.id))


@products_bp.post("/comments/<int:comment_id>/edit")
@customer_required
def edit_product_comment(current_user, comment_id):
    comment = db.session.get(ProductComment, comment_id)
    if comment is None:
        abort(404)
    if comment.user_id != current_user.id:
        abort(403)
    if _product_accepts_content(comment.product_id) is None:
        abort(404)

    content = _validated_content(
        request.form.get("content"), COMMENT_CONTENT_MAX_LENGTH
    )
    kind = request.form.get("kind", comment.kind)
    if content is None or kind not in {"comment", "question"}:
        flash("Vui lòng nhập bình luận hoặc câu hỏi tối đa 2.000 ký tự.")
    else:
        comment.content = content
        comment.kind = kind
        comment.status = "pending"
        comment.admin_reply = None
        comment.replied_by_user_id = None
        comment.replied_at = None
        comment.updated_at = datetime.now()
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                "Could not update product comment %s", comment_id
            )
            flash("Không thể cập nhật bình luận lúc này. Vui lòng thử lại.")
        else:
            flash("Bình luận đã cập nhật và đang chờ Admin duyệt lại.")
    return redirect(url_for("products.product_detail", product_id=comment.product_id))


@products_bp.post("/comments/<int:comment_id>/delete")
@customer_required
def hide_own_product_comment(current_user, comment_id):
    comment = db.session.get(ProductComment, comment_id)
    if comment is None:
        abort(404)
    if comment.user_id != current_user.id:
        abort(403)
    if _product_accepts_content(comment.product_id) is None:
        abort(404)
    comment.status = "hidden"
    comment.updated_at = datetime.now()
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Could not hide product comment %s", comment_id)
        flash("Không thể ẩn bình luận lúc này. Vui lòng thử lại.")
    else:
        flash("Bình luận đã được ẩn; dữ liệu vẫn được lưu.")
    return redirect(url_for("products.product_detail", product_id=comment.product_id))
