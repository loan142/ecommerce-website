from flask import Blueprint, render_template
from backend.models import Product, Order

admin_bp = Blueprint("admin", __name__)

@admin_bp.get("/")
def dashboard():
    # IMPORTANT: Add authentication and admin-role authorization before real use.
    return render_template("admin/dashboard.html")

@admin_bp.get("/products")
def products():
    return render_template("admin/products.html", products=Product.query.all())

@admin_bp.get("/orders")
def orders():
    return render_template("admin/orders.html", orders=Order.query.order_by(Order.id.desc()).all())
