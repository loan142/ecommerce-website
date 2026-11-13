from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from backend.models import Order, OrderItem, Product, ProductImage, User, db
from backend.order_status import ORDER_STATUS_LABELS
from backend.services.order_transitions import (
    OrderTransitionError,
    transition_order,
)

customer_orders_bp = Blueprint("customer_orders", __name__)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("auth.login"))

        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            return redirect(url_for("auth.login"))

        return view(user_id, *args, **kwargs)

    return wrapped_view


@customer_orders_bp.get("")
@login_required
def my_orders(user_id):
    selected_status = request.args.get("status", "").strip()
    if selected_status not in ORDER_STATUS_LABELS:
        selected_status = ""

    item_counts = (
        db.session.query(
            OrderItem.order_id.label("order_id"),
            func.sum(OrderItem.quantity).label("item_count"),
        )
        .group_by(OrderItem.order_id)
        .subquery()
    )
    query = (
        db.session.query(Order, func.coalesce(item_counts.c.item_count, 0))
        .outerjoin(item_counts, Order.id == item_counts.c.order_id)
        .filter(Order.user_id == user_id)
    )
    if selected_status:
        query = query.filter(Order.status == selected_status)

    orders = query.order_by(
        Order.created_at.is_(None), Order.created_at.desc(), Order.id.desc()
    ).all()
    return render_template(
        "my_orders.html",
        orders=orders,
        selected_status=selected_status,
        status_labels=ORDER_STATUS_LABELS,
    )


@customer_orders_bp.post("/<int:order_id>/cancel")
@login_required
def cancel_my_order(user_id, order_id):
    try:
        result, previous_status = transition_order(
            order_id,
            "cancelled",
            owner_id=user_id,
            allowed_previous_statuses=("pending",),
        )
    except OrderTransitionError as error:
        flash(str(error))
        return redirect(url_for("customer_orders.my_order_detail", order_id=order_id))
    except SQLAlchemyError:
        flash("Không thể hủy đơn hàng do lỗi cơ sở dữ liệu. Vui lòng thử lại.")
        return redirect(url_for("customer_orders.my_order_detail", order_id=order_id))

    if result == "not_found":
        abort(404)
    if result == "invalid_transition":
        if previous_status == "confirmed":
            flash("Đơn hàng đã được Admin xác nhận nên bạn không thể hủy.")
        else:
            flash("Chỉ đơn hàng đang chờ xác nhận mới có thể hủy.")
        return redirect(url_for("customer_orders.my_order_detail", order_id=order_id))
    if result == "conflict":
        flash("Đơn hàng vừa được cập nhật. Vui lòng tải lại để kiểm tra trạng thái.")
        return redirect(url_for("customer_orders.my_order_detail", order_id=order_id))

    flash(f"Đơn hàng #{order_id} đã được hủy. Tồn kho sản phẩm đã được hoàn lại.")
    return redirect(url_for("customer_orders.my_order_detail", order_id=order_id))


@customer_orders_bp.get("/<int:order_id>")
@login_required
def my_order_detail(user_id, order_id):
    order = Order.query.filter_by(id=order_id, user_id=user_id).first_or_404()
    order_items = []
    items = (
        db.session.query(OrderItem, Product)
        .outerjoin(Product, Product.id == OrderItem.product_id)
        .filter(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
        .all()
    )
    for item, product in items:
        primary_image = None
        if product is not None:
            primary_image = (
                ProductImage.query.filter_by(
                    product_id=product.id, is_primary=True
                )
                .order_by(ProductImage.sort_order, ProductImage.id)
                .first()
            )
            if primary_image is None:
                primary_image = (
                    ProductImage.query.filter_by(product_id=product.id)
                    .order_by(ProductImage.sort_order, ProductImage.id)
                    .first()
                )
        order_items.append(
            {
                "item": item,
                "product": product,
                "image": primary_image,
                "line_total": item.quantity * item.unit_price,
            }
        )

    return render_template(
        "my_order_detail.html",
        order=order,
        order_items=order_items,
        status_labels=ORDER_STATUS_LABELS,
    )
