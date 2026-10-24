from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from backend.models import Product, db, Order, OrderItem

cart_bp = Blueprint("cart", __name__)

def get_items():
    cart = session.get("cart", {})
    items, total = [], 0
    for product_id, quantity in cart.items():
        product = db.session.get(Product, int(product_id))
        if product and quantity > 0:
            items.append({"product": product, "quantity": quantity})
            total += product.price * quantity
    return items, total

@cart_bp.get("/")
def view_cart():
    items, total = get_items()
    return render_template("cart.html", items=items, total=total)

@cart_bp.post("/add/<int:product_id>")
def add_to_cart(product_id):
    product = Product.query.filter_by(id=product_id, is_active=True).first_or_404()
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
    items, total = get_items()
    if not items:
        flash("Giỏ hàng đang trống.")
        return redirect(url_for("cart.view_cart"))
    if request.method == "POST":
        # Demo order flow only; add login requirement, address/phone validation,
        # transaction-safe stock updates, CSRF protection and payment integration before production.
        order = Order(user_id=session.get("user_id"), total_amount=total, status="pending")
        db.session.add(order)
        db.session.flush()
        for item in items:
            db.session.add(OrderItem(order_id=order.id, product_id=item["product"].id,
                                     quantity=item["quantity"], unit_price=item["product"].price))
            item["product"].stock -= item["quantity"]
        db.session.commit()
        session["cart"] = {}
        flash(f"Đã tạo đơn hàng demo #{order.id}.")
        return redirect(url_for("main.index"))
    return render_template("checkout.html")
