import re
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from backend.models import Product, db, Order, OrderItem

cart_bp = Blueprint("cart", __name__)

def get_items():
    cart = session.get("cart", {})
    items, total = [], 0
    for product_id, quantity in cart.items():
        product = db.session.get(Product, int(product_id))
        if product and quantity > 0:
            items.append(
                {
                    "product": product,
                    "quantity": quantity,
                    "deleted": product.deleted_at is not None,
                }
            )
            if product.deleted_at is None:
                total += product.price * quantity
    return items, total

@cart_bp.get("/")
def view_cart():
    items, total = get_items()
    return render_template(
        "cart.html",
        items=items,
        total=total,
        has_deleted_items=any(item["deleted"] for item in items),
    )

@cart_bp.post("/add/<int:product_id>")
def add_to_cart(product_id):
    product = Product.query.filter(
        Product.id == product_id,
        Product.is_active.is_(True),
        Product.deleted_at.is_(None),
    ).first_or_404()
    try:
        quantity = max(1, int(request.form.get("quantity", 1)))
    except ValueError:
        quantity = 1
    if product.stock < quantity:
        flash("Số lượng vượt quá tồn kho.")
    else:
        cart = session.get("cart", {})
        cart[str(product.id)] = min(product.stock, int(cart.get(str(product.id), 0)) + quantity)
        session["cart"] = cart
        flash("Đã thêm sản phẩm vào giỏ hàng.")
    return redirect(url_for("cart.view_cart"))

@cart_bp.post("/update")
def update_cart():
    cart = session.get("cart", {})
    remove_id = request.form.get("remove_id")
    if remove_id:
        cart.pop(str(remove_id), None)
    else:
        for key in list(cart):
            try:
                qty = int(request.form.get(f"qty_{key}", cart[key]))
                if qty <= 0:
                    cart.pop(key, None)
                else:
                    product = db.session.get(Product, int(key))
                    cart[key] = min(qty, product.stock) if product else 0
            except ValueError:
                pass
    session["cart"] = cart
    return redirect(url_for("cart.view_cart"))

@cart_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    if request.method == "GET":
        items, cart_error = _get_checkout_items()
        if not items and not cart_error:
            flash("Giỏ hàng đang trống.")
            return redirect(url_for("cart.view_cart"))
        if not session.get("checkout_token"):
            session["checkout_token"] = secrets.token_urlsafe(32)
        session.pop("order_confirmation_id", None)
        return _render_checkout(
            items, _checkout_form_values(), [cart_error] if cart_error else []
        )

    values, errors = _validate_checkout_form()
    token = request.form.get("checkout_token", "").strip()
    if not token:
        token = session.get("checkout_token", "")
    if not token:
        token = secrets.token_urlsafe(32)
        session["checkout_token"] = token
    if errors:
        items, _ = _get_checkout_items()
        return _render_checkout(items, values, errors, 400)
    if not token:
        items, _ = _get_checkout_items()
        return _render_checkout(
            items,
            values,
            ["Phiên đặt hàng đã hết hạn. Vui lòng tải lại trang."],
            400,
        )

    try:
        connection = db.session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        existing_order = Order.query.filter_by(checkout_token=token).first()
        if existing_order is not None:
            db.session.rollback()
            return _complete_checkout(existing_order)

        if token != session.get("checkout_token"):
            db.session.rollback()
            items, _ = _get_checkout_items()
            return _render_checkout(
                items,
                values,
                ["Phiên đặt hàng đã hết hạn. Vui lòng tải lại trang."],
                400,
            )

        items, cart_error = _get_checkout_items()
        if cart_error or not items:
            db.session.rollback()
            return _render_checkout(
                items, values, [cart_error or "Giỏ hàng đang trống."], 409
            )

        order = Order(
            user_id=session.get("user_id"),
            status="pending",
            total_amount=sum(
                item["product"].price * item["quantity"] for item in items
            ),
            recipient_name=values["recipient_name"],
            recipient_phone=values["recipient_phone"],
            shipping_address=values["shipping_address"],
            note=values["note"] or None,
            payment_method=values["payment_method"],
            checkout_token=token,
        )
        db.session.add(order)
        db.session.flush()

        for item in sorted(items, key=lambda checkout_item: checkout_item["product"].id):
            result = db.session.execute(
                update(Product)
                .where(
                    Product.id == item["product"].id,
                    Product.is_active.is_(True),
                    Product.deleted_at.is_(None),
                    Product.stock >= item["quantity"],
                )
                .values(stock=Product.stock - item["quantity"])
            )
            if result.rowcount != 1:
                raise _StockUnavailable

            db.session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=item["product"].id,
                    quantity=item["quantity"],
                    unit_price=item["product"].price,
                )
            )
        db.session.commit()
    except _StockUnavailable:
        db.session.rollback()
        items, _ = _get_checkout_items()
        return _render_checkout(
            items,
            values,
            [
                "Sản phẩm vừa thay đổi tồn kho hoặc không còn mở bán. "
                "Vui lòng kiểm tra lại giỏ hàng."
            ],
            409,
        )
    except IntegrityError:
        db.session.rollback()
        existing_order = Order.query.filter_by(checkout_token=token).first()
        if existing_order is not None:
            return _complete_checkout(existing_order)
        items, _ = _get_checkout_items()
        return _render_checkout(
            items,
            values,
            ["Không thể tạo đơn hàng do dữ liệu thay đổi. Vui lòng thử lại."],
            409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        return _render_checkout(
            [],
            values,
            [
                "Có lỗi khi lưu đơn hàng. Giỏ hàng của bạn vẫn được giữ lại; "
                "vui lòng thử lại."
            ],
            500,
        )

    return _complete_checkout(order)


@cart_bp.get("/checkout/success/<int:order_id>")
def checkout_success(order_id):
    if session.get("order_confirmation_id") != order_id:
        return redirect(url_for("cart.view_cart"))
    order = db.session.get(Order, order_id)
    if order is None:
        return redirect(url_for("cart.view_cart"))
    return render_template("checkout.html", order=order)


def _checkout_form_values():
    return {
        "recipient_name": "",
        "recipient_phone": "",
        "shipping_address": "",
        "note": "",
        "payment_method": "cod",
    }


def _validate_checkout_form():
    values = {
        "recipient_name": request.form.get(
            "recipient_name", request.form.get("full_name", "")
        ).strip(),
        "recipient_phone": request.form.get(
            "recipient_phone", request.form.get("phone", "")
        ).strip(),
        "shipping_address": request.form.get(
            "shipping_address", request.form.get("address", "")
        ).strip(),
        "note": request.form.get("note", "").strip(),
        "payment_method": request.form.get("payment_method", "").strip(),
    }
    errors = []

    if not values["recipient_name"]:
        errors.append("Vui lòng nhập họ tên người nhận.")
    elif len(values["recipient_name"]) > 120:
        errors.append("Họ tên người nhận không được vượt quá 120 ký tự.")
    elif any(ord(character) < 32 for character in values["recipient_name"]):
        errors.append("Họ tên người nhận chứa ký tự không hợp lệ.")

    phone = values["recipient_phone"]
    normalized_phone = re.sub(r"[ ()-]", "", phone)
    if not phone:
        errors.append("Vui lòng nhập số điện thoại người nhận.")
    elif len(phone) > 30 or re.fullmatch(r"\+?\d{9,15}", normalized_phone) is None:
        errors.append("Số điện thoại không hợp lệ. Vui lòng kiểm tra lại.")

    address = values["shipping_address"]
    if not address:
        errors.append("Vui lòng nhập địa chỉ giao hàng.")
    elif len(address) > 500:
        errors.append("Địa chỉ giao hàng không được vượt quá 500 ký tự.")
    elif any(ord(character) < 32 and character not in "\t\n\r" for character in address):
        errors.append("Địa chỉ giao hàng chứa ký tự không hợp lệ.")

    if len(values["note"]) > 1000:
        errors.append("Ghi chú không được vượt quá 1000 ký tự.")
    if values["payment_method"] != "cod":
        errors.append("Phương thức thanh toán hiện chỉ hỗ trợ COD.")

    return values, errors


def _get_checkout_items():
    cart = session.get("cart", {})
    if not isinstance(cart, dict) or not cart:
        return [], None

    items = []
    for product_id, raw_quantity in cart.items():
        if not str(product_id).isdigit() or isinstance(raw_quantity, bool):
            return [], "Giỏ hàng có thông tin không hợp lệ. Vui lòng cập nhật lại."
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            return [], "Giỏ hàng có thông tin không hợp lệ. Vui lòng cập nhật lại."
        if quantity <= 0:
            return [], "Số lượng sản phẩm phải lớn hơn 0."

        product = Product.query.filter_by(
            id=int(product_id), is_active=True, deleted_at=None
        ).first()
        if product is None:
            return [], "Một sản phẩm trong giỏ không còn được mở bán."
        if product.stock == 0:
            return [], f"Sản phẩm “{product.name}” hiện đã hết hàng."
        if product.stock < quantity:
            return [], f"Sản phẩm “{product.name}” không đủ tồn kho cho số lượng đã chọn."
        items.append({"product": product, "quantity": quantity})

    return items, None


def _render_checkout(items, values, errors, status=200):
    total = sum(item["product"].price * item["quantity"] for item in items)
    if not session.get("checkout_token"):
        session["checkout_token"] = secrets.token_urlsafe(32)
    return (
        render_template(
            "checkout.html",
            items=items,
            total=total,
            values=values,
            errors=errors,
            checkout_token=session["checkout_token"],
        ),
        status,
    )


def _complete_checkout(order):
    if order.user_id != session.get("user_id"):
        items, _ = _get_checkout_items()
        return _render_checkout(
            items,
            _checkout_form_values(),
            ["Phiên đặt hàng không hợp lệ. Vui lòng tải lại trang."],
            status=400,
        )
    session["cart"] = {}
    session.pop("checkout_token", None)
    session["order_confirmation_id"] = order.id
    return redirect(url_for("cart.checkout_success", order_id=order.id))


class _StockUnavailable(Exception):
    pass
