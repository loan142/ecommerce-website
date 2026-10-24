from flask import Blueprint, render_template, request, abort
from sqlalchemy import or_
from backend.models import Product

products_bp = Blueprint("products", __name__)

@products_bp.get("/")
def list_products():
    keyword = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest")
    query = Product.query.filter_by(is_active=True)
    if keyword:
        query = query.filter(or_(Product.name.ilike(f"%{keyword}%"), Product.description.ilike(f"%{keyword}%")))
    if sort == "price_asc":
        query = query.order_by(Product.price.asc())
    elif sort == "price_desc":
        query = query.order_by(Product.price.desc())
    products = query.all()
    return render_template("products.html", products=products, keyword=keyword, sort=sort)

@products_bp.get("/<int:product_id>")
def product_detail(product_id):
    product = Product.query.filter_by(id=product_id, is_active=True).first_or_404()
    return render_template("product-detail.html", product=product)
