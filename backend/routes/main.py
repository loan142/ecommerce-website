from flask import Blueprint, render_template
from backend.models import Product

main_bp = Blueprint("main", __name__)

@main_bp.get("/")
def index():
    products = Product.query.filter(
        Product.is_active.is_(True), Product.deleted_at.is_(None)
    ).limit(8).all()
    return render_template("index.html", products=products)

@main_bp.get("/policies")
def policies():
    return render_template("policies.html")
