from functools import wraps
from datetime import datetime
import logging
import os
import re
import unicodedata
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash
from backend.csrf import validate_csrf_token
from backend.models import (
    Category,
    Order,
    OrderItem,
    Product,
    ProductComment,
    ProductImage,
    Review,
    User,
    db,
)
from backend.order_status import ORDER_STATUS_LABELS, ORDER_STATUS_TRANSITIONS
from backend.services.order_transitions import (
    OrderTransitionError,
    transition_order,
)
from backend.services.product_images import (
    InvalidProductImage,
    MAX_PRODUCT_IMAGES,
    is_managed_upload,
    prepare_image,
    remove_managed_upload,
    write_pending_image,
)

admin_bp = Blueprint("admin", __name__)
logger = logging.getLogger(__name__)
ADMIN_REVIEW_PAGE_SIZE = 25
ADMIN_CONTENT_MAX_LENGTH = 2000

def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("admin.login"))

        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            return redirect(url_for("admin.login"))
        if user.role != "admin":
            abort(403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            validate_csrf_token()

        return view(*args, **kwargs)

    return wrapped_view


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        validate_csrf_token()

    user_id = session.get("user_id")
    current_user = db.session.get(User, user_id) if user_id is not None else None
    if user_id is not None and current_user is None:
        session.pop("user_id", None)
    if current_user is not None:
        if current_user.role == "admin":
            return redirect(url_for("admin.dashboard"))
        flash("Không thể truy cập khu vực quản trị.")
        return render_template("admin/login.html"), 403

    if request.method == "POST":
        user = User.query.filter_by(
            email=request.form.get("email", "").strip().lower()
        ).first()
        if (
            user is not None
            and user.role == "admin"
            and check_password_hash(user.password_hash, request.form.get("password", ""))
        ):
            session["user_id"] = user.id
            flash("Đăng nhập thành công.")
            return redirect(url_for("admin.dashboard"))
        flash("Email hoặc mật khẩu chưa đúng.")

    return render_template("admin/login.html")


@admin_bp.get("/logout")
@admin_required
def logout():
    session.clear()
    flash("Bạn đã đăng xuất.")
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
@admin_required
def dashboard():
    status_counts = {
        status: count
        for status, count in (
            db.session.query(Order.status, func.count(Order.id))
            .filter(Order.status.in_(ORDER_STATUS_LABELS))
            .group_by(Order.status)
            .all()
        )
    }
    status_counts = {
        status: status_counts.get(status, 0) for status in ORDER_STATUS_LABELS
    }
    total_orders = Order.query.count()
    completed_revenue = (
        db.session.query(func.coalesce(func.sum(Order.total_amount), 0))
        .filter(Order.status == "completed")
        .scalar()
    )
    stock_alerts = (
        Product.query.filter(
            Product.deleted_at.is_(None),
            or_(
                Product.stock == 0,
                and_(Product.is_active.is_(True), Product.stock.between(1, 5)),
            )
        )
        .order_by(Product.stock.asc(), Product.id.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "admin/dashboard.html",
        product_count=Product.query.filter(Product.deleted_at.is_(None)).count(),
        trashed_product_count=Product.query.filter(
            Product.deleted_at.is_not(None)
        ).count(),
        active_product_count=Product.query.filter(
            Product.is_active.is_(True), Product.deleted_at.is_(None)
        ).count(),
        low_stock_count=Product.query.filter(
            Product.is_active.is_(True),
            Product.deleted_at.is_(None),
            Product.stock.between(1, 5),
        ).count(),
        out_of_stock_count=Product.query.filter(
            Product.stock == 0, Product.deleted_at.is_(None)
        ).count(),
        order_count=total_orders,
        pending_order_count=status_counts["pending"],
        shipping_order_count=status_counts["shipping"],
        completed_order_count=status_counts["completed"],
        completed_revenue=completed_revenue,
        status_counts=status_counts,
        status_labels=ORDER_STATUS_LABELS,
        max_status_count=max(status_counts.values(), default=0),
        recent_orders=Order.query.order_by(Order.id.desc()).limit(8).all(),
        stock_alerts=stock_alerts,
    )


@admin_bp.get("/products")
@admin_required
def products():
    keyword = request.args.get("q", "").strip()
    selected_category = request.args.get("category", "").strip()
    selected_view = request.args.get("view", "all")
    if selected_view not in {"all", "active", "hidden", "trash"}:
        abort(404)
    query = Product.query
    if selected_view == "trash":
        query = query.filter(Product.deleted_at.is_not(None))
    else:
        query = query.filter(Product.deleted_at.is_(None))
        if selected_view == "active":
            query = query.filter(Product.is_active.is_(True))
        elif selected_view == "hidden":
            query = query.filter(Product.is_active.is_(False))
    if keyword:
        query = query.filter(Product.name.ilike(f"%{keyword}%"))
    if selected_category == "uncategorized":
        query = query.filter(Product.category_id.is_(None))
    elif selected_category:
        if not selected_category.isdecimal():
            abort(404)
        category = db.session.get(Category, int(selected_category))
        if category is None:
            abort(404)
        query = query.filter(Product.category_id == category.id)
    products = query.order_by(Product.id.desc()).all()
    return render_template(
        "admin/products.html",
        products=products,
        categories=Category.query.order_by(Category.name.asc()).all(),
        keyword=keyword,
        selected_category=selected_category,
        selected_view=selected_view,
    )


def _product_form_values(product=None):
    if product is None:
        return {
            "name": "",
            "description": "",
            "price": "",
            "old_price": "",
            "image_url": "",
            "stock": "",
            "is_active": True,
            "category_id": "",
        }
    return {
        "name": product.name,
        "description": product.description or "",
        "price": str(product.price),
        "old_price": "" if product.old_price is None else str(product.old_price),
        "image_url": product.image_url or "",
        "stock": str(product.stock),
        "is_active": product.is_active,
        "category_id": "" if product.category_id is None else str(product.category_id),
    }


def _admin_product_images(product):
    images = [
        {
            "id": image.id,
            "image_path": image.image_path,
            "sort_order": image.sort_order,
            "is_primary": image.is_primary,
            "record": image,
        }
        for image in product.images
    ]
    if product.image_url and not any(
        image["image_path"] == product.image_url for image in images
    ):
        images.append(
            {
                "id": "legacy",
                "image_path": product.image_url,
                "sort_order": max(
                    (image["sort_order"] for image in images), default=-1
                )
                + 1,
                "is_primary": not images,
                "record": None,
            }
        )
    return images


def _product_form_context(product, values, errors):
    return {
        "page_title": "Sửa sản phẩm" if product else "Thêm sản phẩm",
        "product_values": values,
        "categories": Category.query.order_by(Category.name.asc()).all(),
        "product_images": _admin_product_images(product) if product else [],
        "errors": errors,
    }


def _plan_product_gallery(product, values):
    current_images = _admin_product_images(product) if product else []
    current_by_key = {str(image["id"]): image for image in current_images}
    old_url = product.image_url or "" if product else ""
    new_url = values["image_url"].strip()
    errors = []

    deleted_keys = request.form.getlist("delete_image_ids")
    if len(set(deleted_keys)) != len(deleted_keys) or any(
        key not in current_by_key for key in deleted_keys
    ):
        errors.append("Danh sách ảnh cần xóa không hợp lệ.")
    deleted = set(deleted_keys)
    if old_url and not new_url:
        deleted.update(
            key
            for key, image in current_by_key.items()
            if image["image_path"] == old_url and not is_managed_upload(old_url)
        )

    kept = [image for key, image in current_by_key.items() if key not in deleted]
    uploads = [file for file in request.files.getlist("images") if file.filename]
    if len(uploads) > MAX_PRODUCT_IMAGES:
        errors.append("Mỗi sản phẩm chỉ được tải tối đa 5 ảnh trong một lần.")

    prepared_uploads = []
    for upload in uploads[:MAX_PRODUCT_IMAGES]:
        try:
            extension, content = prepare_image(upload)
            prepared_uploads.append({"extension": extension, "content": content})
        except InvalidProductImage as error:
            errors.append(f"{upload.filename}: {error}")

    legacy_deleted = bool(old_url) and any(
        image["image_path"] == old_url
        for key, image in current_by_key.items()
        if key in deleted
    )
    legacy_new = bool(new_url) and not (
        new_url == old_url and legacy_deleted
    ) and not any(
        image["image_path"] in {new_url, old_url} for image in kept
    )
    total_images = len(kept) + len(prepared_uploads) + int(legacy_new)
    if total_images > MAX_PRODUCT_IMAGES:
        errors.append("Mỗi sản phẩm chỉ được có tối đa 5 ảnh, tính cả ảnh đang có.")

    ordered_entries = []
    used_positions = set()

    def add_ordered_entry(key, default_position, entry):
        raw_position = request.form.get(f"image_order_{key}", "")
        try:
            position = int(raw_position) if raw_position else default_position
        except ValueError:
            position = 0
        if position < 1 or position > MAX_PRODUCT_IMAGES:
            errors.append("Thứ tự mỗi ảnh phải là một số từ 1 đến 5.")
        elif position in used_positions:
            errors.append("Thứ tự các ảnh không được trùng nhau.")
        else:
            used_positions.add(position)
        entry["position"] = position
        ordered_entries.append(entry)

    for image in kept:
        position = image["sort_order"] + 1
        add_ordered_entry(
            str(image["id"]),
            position,
            {
                "key": str(image["id"]),
                "record": image["record"],
                "image_path": image["image_path"],
            },
        )

    upload_entries = []
    for index, prepared in enumerate(prepared_uploads):
        next_position = next(
            (position for position in range(1, MAX_PRODUCT_IMAGES + 1)
             if position not in used_positions),
            MAX_PRODUCT_IMAGES + 1,
        )
        entry = {
            "key": f"new:{index}",
            "prepared": prepared,
        }
        add_ordered_entry(f"new_{index}", next_position, entry)
        upload_entries.append(entry)

    legacy_entry = None
    if legacy_new:
        legacy_entry = {
            "key": "legacy-new",
            "record": None,
            "image_path": new_url,
        }
        legacy_position = next(
            (position for position in range(1, MAX_PRODUCT_IMAGES + 1)
             if position not in used_positions),
            MAX_PRODUCT_IMAGES + 1,
        )
        if legacy_position > MAX_PRODUCT_IMAGES:
            errors.append("Không còn vị trí trống cho ảnh URL dự phòng.")
        legacy_entry["position"] = legacy_position
        ordered_entries.append(legacy_entry)

    primary_choice = request.form.get("primary_image", "")
    valid_primary_keys = {entry["key"] for entry in ordered_entries}
    if primary_choice and primary_choice not in valid_primary_keys:
        if primary_choice in deleted:
            primary_choice = ""
        else:
            errors.append("Ảnh chính đã chọn không hợp lệ.")
    if not primary_choice:
        primary_choice = next(
            (
                entry["key"]
                for entry in ordered_entries
                if entry.get("record") is not None
                and entry["record"].is_primary
            ),
            ordered_entries[0]["key"] if ordered_entries else "",
        )
    clear_legacy_url = legacy_deleted and new_url == old_url
    return {
        "entries": ordered_entries,
        "uploads": upload_entries,
        "deleted": [
            image["record"]
            for key, image in current_by_key.items()
            if key in deleted and image["record"] is not None
        ],
        "deleted_paths": [
            image["image_path"]
            for key, image in current_by_key.items()
            if key in deleted
        ],
        "primary_choice": primary_choice,
        "clear_legacy_url": clear_legacy_url,
        "errors": errors,
    }


def _save_product_gallery(product, values, plan):
    temporary_files = []
    committed_files = []
    old_image_url = product.image_url or ""
    try:
        for entry in plan["uploads"]:
            temporary, destination, relative_path = write_pending_image(
                entry["prepared"]["extension"], entry["prepared"]["content"]
            )
            temporary_files.append(temporary)
            entry["destination"] = destination
            entry["relative_path"] = relative_path
            entry["temporary_path"] = temporary

        product.image_url = "" if plan["clear_legacy_url"] else values["image_url"].strip()
        for image in plan["deleted"]:
            db.session.delete(image)

        for entry in plan["entries"]:
            primary = entry["key"] == plan["primary_choice"]
            if entry["key"].startswith("new:"):
                new_upload = next(
                    upload for upload in plan["uploads"] if upload["key"] == entry["key"]
                )
                image_path = new_upload["relative_path"]
                db.session.add(
                    ProductImage(
                        product=product,
                        image_path=image_path,
                        sort_order=entry["position"] - 1,
                        is_primary=primary,
                    )
                )
            elif entry["key"] == "legacy-new":
                db.session.add(
                    ProductImage(
                        product=product,
                        image_path=values["image_url"].strip(),
                        sort_order=entry["position"] - 1,
                        is_primary=primary,
                    )
                )
            else:
                image = entry["record"]
                if image is None:
                    image = ProductImage(
                        product=product,
                        image_path=values["image_url"].strip(),
                    )
                    db.session.add(image)
                elif (
                    values["image_url"].strip()
                    and image.image_path == old_image_url
                    and not is_managed_upload(image.image_path)
                ):
                    image.image_path = values["image_url"].strip()
                image.sort_order = entry["position"] - 1
                image.is_primary = primary

        db.session.flush()
        for temporary, destination, _ in (
            (entry["temporary_path"], entry["destination"], entry["relative_path"])
            for entry in plan["uploads"]
        ):
            os.replace(temporary, destination)
            temporary_files.remove(temporary)
            committed_files.append(destination)
        db.session.commit()
    except (OSError, SQLAlchemyError):
        db.session.rollback()
        for path in temporary_files + committed_files:
            path.unlink(missing_ok=True)
        raise

    for image_path in plan["deleted_paths"]:
        if not is_managed_upload(image_path):
            continue
        if (
            ProductImage.query.filter_by(image_path=image_path).first() is not None
            or Product.query.filter_by(image_url=image_path).first() is not None
        ):
            continue
        try:
            remove_managed_upload(image_path)
        except OSError:
            logger.exception("Could not remove deleted product image %s", image_path)
            flash(
                "Sản phẩm đã lưu nhưng có một tệp ảnh cũ không thể dọn khỏi ổ đĩa."
            )


def _get_product_or_404(product_id):
    product = Product.query.filter(
        Product.id == product_id, Product.deleted_at.is_(None)
    ).first()
    if product is None:
        abort(404)
    return product


def _safe_image_url(value):
    if not value:
        return True
    if len(value) > 500 or any(ord(char) < 32 for char in value) or "\\" in value:
        return False

    try:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            return (
                parsed.scheme.lower() == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.port is None
                and not parsed.fragment
            )
    except ValueError:
        return False

    if parsed.query or parsed.fragment or value.startswith("//"):
        return False
    path = parsed.path.lstrip("/")
    if path.startswith("static/images/"):
        path = path[len("static/images/") :]
    elif path.startswith("images/"):
        path = path[len("images/") :]
    else:
        return False
    parts = path.split("/")
    return all(
        part not in {"", ".", ".."}
        and re.fullmatch(r"[\w .()-]+", part, flags=re.UNICODE) is not None
        for part in parts
    )


def _validate_product_form():
    values = {
        "name": request.form.get("name", ""),
        "description": request.form.get("description", ""),
        "price": request.form.get("price", "").strip(),
        "old_price": request.form.get("old_price", "").strip(),
        "image_url": request.form.get("image_url", ""),
        "stock": request.form.get("stock", "").strip(),
        "is_active": request.form.get("is_active") == "1",
        "category_id": request.form.get("category_id", "").strip(),
    }
    errors = []
    normalized_name = values["name"].strip()

    if not normalized_name:
        errors.append("Tên sản phẩm không được để trống.")
    elif len(normalized_name) > 150:
        errors.append("Tên sản phẩm không được dài quá 150 ký tự.")

    integers = {}
    for field, label, required in (
        ("price", "Giá bán", True),
        ("old_price", "Giá cũ", False),
        ("stock", "Tồn kho", True),
    ):
        raw_value = values[field]
        if not raw_value and not required:
            integers[field] = None
            continue
        if not re.fullmatch(r"\d+", raw_value):
            errors.append(f"{label} phải là số nguyên không âm.")
            continue
        if len(raw_value) > 10:
            errors.append(f"{label} vượt quá giới hạn lưu trữ.")
            continue
        number = int(raw_value)
        if number > 2_147_483_647:
            errors.append(f"{label} vượt quá giới hạn lưu trữ.")
            continue
        integers[field] = number

    if not _safe_image_url(values["image_url"].strip()):
        errors.append("Ảnh chỉ được dùng URL HTTPS hoặc đường dẫn trong /static/images/.")

    category_id = None
    raw_category_id = values["category_id"]
    if not raw_category_id or not raw_category_id.isdecimal():
        errors.append("Vui lòng chọn một danh mục hợp lệ.")
    else:
        category_id = int(raw_category_id)
        if db.session.get(Category, category_id) is None:
            errors.append("Danh mục đã chọn không tồn tại.")

    integers["category_id"] = category_id
    return values, integers, errors


def _render_product_form(product, values, errors, status=200):
    return (
        render_template(
            "admin/product_form.html",
            page_title="Sửa sản phẩm" if product else "Thêm sản phẩm",
            product_values=values,
            categories=Category.query.order_by(Category.name.asc()).all(),
            product_images=_admin_product_images(product) if product else [],
            errors=errors,
        ),
        status,
    )


def _update_product_fields(product, values, numbers):
    product.name = values["name"].strip()
    product.description = values["description"].strip()
    product.price = numbers["price"]
    product.old_price = numbers["old_price"]
    product.stock = numbers["stock"]
    product.is_active = values["is_active"]
    product.category_id = numbers["category_id"]


def _category_slug(name):
    normalized = name.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFKD", normalized)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


def _validate_category_form(category=None):
    name = request.form.get("name", "").strip()
    errors = []
    if not name:
        errors.append("Tên danh mục không được để trống.")
    elif len(name) > 100:
        errors.append("Tên danh mục không được dài quá 100 ký tự.")

    slug = _category_slug(name)
    if name and not slug:
        errors.append("Tên danh mục cần có ký tự chữ hoặc số.")
    if name and len(name) <= 100:
        duplicate_name = Category.query.filter(
            func.lower(Category.name) == name.lower()
        )
        duplicate_slug = Category.query.filter_by(slug=slug)
        if category is not None:
            duplicate_name = duplicate_name.filter(Category.id != category.id)
            duplicate_slug = duplicate_slug.filter(Category.id != category.id)
        if duplicate_name.first() is not None:
            errors.append("Tên danh mục đã tồn tại.")
        if duplicate_slug.first() is not None:
            errors.append("Tên này tạo ra mã danh mục đã được sử dụng.")
    return name, slug, errors


@admin_bp.get("/categories")
@admin_required
def categories():
    return render_template(
        "admin/categories.html",
        categories=Category.query.order_by(Category.name.asc()).all(),
        category_values={"name": ""},
        category_being_edited=None,
        errors=[],
    )


@admin_bp.post("/categories")
@admin_required
def create_category():
    name, slug, errors = _validate_category_form()
    if errors:
        return render_template(
            "admin/categories.html",
            categories=Category.query.order_by(Category.name.asc()).all(),
            category_values={"name": name},
            category_being_edited=None,
            errors=errors,
        ), 400

    db.session.add(Category(name=name, slug=slug))
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Không thể lưu danh mục do lỗi cơ sở dữ liệu. Vui lòng thử lại.")
        return render_template(
            "admin/categories.html",
            categories=Category.query.order_by(Category.name.asc()).all(),
            category_values={"name": name},
            category_being_edited=None,
            errors=[],
        ), 500
    flash("Đã thêm danh mục.")
    return redirect(url_for("admin.categories"))


@admin_bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_category(category_id):
    category = db.session.get(Category, category_id)
    if category is None:
        abort(404)
    if request.method == "POST":
        name, slug, errors = _validate_category_form(category)
        if errors:
            return render_template(
                "admin/category_form.html",
                category=category,
                category_values={"name": name},
                errors=errors,
            ), 400
        category.name = name
        category.slug = slug
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Không thể cập nhật danh mục do lỗi cơ sở dữ liệu. Vui lòng thử lại.")
            return render_template(
                "admin/category_form.html",
                category=category,
                category_values={"name": name},
                errors=[],
            ), 500
        flash("Đã cập nhật danh mục.")
        return redirect(url_for("admin.categories"))

    return render_template(
        "admin/category_form.html",
        category=category,
        category_values={"name": category.name},
        errors=[],
    )


@admin_bp.route("/products/new", methods=["GET", "POST"])
@admin_required
def create_product():
    values = _product_form_values()
    if request.method == "POST":
        values, numbers, errors = _validate_product_form()
        plan = _plan_product_gallery(None, values)
        errors.extend(plan["errors"])
        if errors:
            return _render_product_form(None, values, errors, 400)

        product = Product(
            name=values["name"].strip(),
            description=values["description"].strip(),
            price=numbers["price"],
            old_price=numbers["old_price"],
            image_url="",
            stock=numbers["stock"],
            is_active=values["is_active"],
            category_id=numbers["category_id"],
        )
        db.session.add(product)
        try:
            _save_product_gallery(product, values, plan)
        except (OSError, SQLAlchemyError):
            db.session.rollback()
            logger.exception("Could not save product and its image gallery")
            flash("Không thể lưu sản phẩm hoặc ảnh do lỗi lưu trữ. Vui lòng thử lại.")
            return _render_product_form(None, values, [], 500)
        flash("Đã thêm sản phẩm.")
        return redirect(url_for("admin.products"))

    return _render_product_form(None, values, [])


@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    product = _get_product_or_404(product_id)
    values = _product_form_values(product)
    if request.method == "POST":
        values, numbers, errors = _validate_product_form()
        plan = _plan_product_gallery(product, values)
        errors.extend(plan["errors"])
        if errors:
            return _render_product_form(product, values, errors, 400)

        _update_product_fields(product, values, numbers)
        try:
            _save_product_gallery(product, values, plan)
        except (OSError, SQLAlchemyError):
            db.session.rollback()
            logger.exception(
                "Could not update product %s and its image gallery", product.id
            )
            flash("Không thể cập nhật sản phẩm hoặc ảnh do lỗi lưu trữ. Vui lòng thử lại.")
            return _render_product_form(product, values, [], 500)
        flash("Đã cập nhật sản phẩm.")
        return redirect(url_for("admin.products"))

    return _render_product_form(product, values, [])


@admin_bp.post("/products/<int:product_id>/toggle")
@admin_required
def toggle_product(product_id):
    product = _get_product_or_404(product_id)
    product.is_active = not product.is_active
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Không thể cập nhật trạng thái sản phẩm do lỗi cơ sở dữ liệu.")
    else:
        flash("Sản phẩm đã được hiện." if product.is_active else "Sản phẩm đã được ẩn.")
    return redirect(url_for("admin.products"))


@admin_bp.post("/products/<int:product_id>/delete")
@admin_required
def delete_product(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    if product.deleted_at is None:
        product.deleted_at = datetime.now()
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not move product %s to trash", product_id)
            flash("Không thể chuyển sản phẩm vào thùng rác do lỗi cơ sở dữ liệu.")
            return redirect(url_for("admin.products"))
        flash("Đã chuyển sản phẩm vào thùng rác.")
    else:
        flash("Sản phẩm đã có trong thùng rác.")
    return redirect(url_for("admin.products", view="trash"))


@admin_bp.post("/products/<int:product_id>/restore")
@admin_required
def restore_product(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    if product.deleted_at is not None:
        product.deleted_at = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not restore product %s from trash", product_id)
            flash("Không thể khôi phục sản phẩm do lỗi cơ sở dữ liệu.")
            return redirect(url_for("admin.products", view="trash"))
        flash("Đã khôi phục sản phẩm.")
    else:
        flash("Sản phẩm hiện không nằm trong thùng rác.")
    return redirect(url_for("admin.products"))


@admin_bp.get("/orders")
@admin_required
def orders():
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
    )
    if selected_status:
        query = query.filter(Order.status == selected_status)

    order_rows = query.order_by(Order.id.desc()).all()
    orders = []
    for order, item_count in order_rows:
        buyer = db.session.get(User, order.user_id) if order.user_id is not None else None
        orders.append(
            {
                "order": order,
                "buyer": buyer,
                "item_count": item_count,
                "status_label": ORDER_STATUS_LABELS.get(order.status, order.status),
                "next_statuses": ORDER_STATUS_TRANSITIONS.get(order.status, ()),
            }
        )
    return render_template(
        "admin/orders.html",
        orders=orders,
        selected_status=selected_status,
        status_labels=ORDER_STATUS_LABELS,
    )


@admin_bp.get("/orders/<int:order_id>")
@admin_required
def order_detail(order_id):
    order = db.session.get(Order, order_id)
    if order is None:
        abort(404)

    buyer = db.session.get(User, order.user_id) if order.user_id is not None else None
    order_items = []
    for item in OrderItem.query.filter_by(order_id=order.id).order_by(OrderItem.id).all():
        product = db.session.get(Product, item.product_id)
        order_items.append(
            {
                "item": item,
                "product": product,
                "line_total": item.quantity * item.unit_price,
            }
        )

    return render_template(
        "admin/order_detail.html",
        order=order,
        buyer=buyer,
        order_items=order_items,
        status_label=ORDER_STATUS_LABELS.get(order.status, order.status),
        status_labels=ORDER_STATUS_LABELS,
        next_statuses=ORDER_STATUS_TRANSITIONS.get(order.status, ()),
    )


@admin_bp.post("/orders/<int:order_id>/status")
@admin_required
def update_order_status(order_id):
    new_status = request.form.get("status", "").strip()
    if new_status not in ORDER_STATUS_LABELS:
        flash("Trạng thái đơn hàng không hợp lệ.")
        if db.session.get(Order, order_id) is None:
            abort(404)
        return redirect(url_for("admin.order_detail", order_id=order_id))

    try:
        result, _ = transition_order(order_id, new_status)
    except OrderTransitionError as error:
        db.session.rollback()
        flash(str(error))
        return redirect(url_for("admin.order_detail", order_id=order_id))
    except SQLAlchemyError:
        db.session.rollback()
        flash("Không thể cập nhật đơn hàng do lỗi cơ sở dữ liệu. Vui lòng thử lại.")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    if result == "not_found":
        abort(404)
    if result != "updated":
        flash("Không thể chuyển đơn hàng từ trạng thái hiện tại sang trạng thái đã chọn.")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    flash(f"Đã cập nhật trạng thái đơn hàng #{order_id}: {ORDER_STATUS_LABELS[new_status]}.")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.get("/reviews")
@admin_required
def reviews():
    selected_status = request.args.get("status", "").strip()
    if selected_status not in {"pending", "approved", "hidden"}:
        selected_status = ""
    try:
        page = max(1, int(request.args.get("page", "1")))
    except (TypeError, ValueError):
        page = 1

    content_rows = union_all(
        select(
            Review.id.label("id"),
            literal("review").label("content_type"),
            Review.product_id.label("product_id"),
            Review.user_id.label("user_id"),
            literal("review").label("kind"),
            Review.rating.label("rating"),
            Review.comment.label("content"),
            Review.status.label("status"),
            Review.created_at.label("created_at"),
            literal(None).label("admin_reply"),
        ),
        select(
            ProductComment.id.label("id"),
            literal("comment").label("content_type"),
            ProductComment.product_id.label("product_id"),
            ProductComment.user_id.label("user_id"),
            ProductComment.kind.label("kind"),
            literal(None).label("rating"),
            ProductComment.content.label("content"),
            ProductComment.status.label("status"),
            ProductComment.created_at.label("created_at"),
            ProductComment.admin_reply.label("admin_reply"),
        ),
    ).subquery()
    query = (
        select(
            content_rows.c.id,
            content_rows.c.content_type,
            content_rows.c.product_id,
            Product.name.label("product_name"),
            User.full_name.label("sender_name"),
            content_rows.c.kind,
            content_rows.c.rating,
            content_rows.c.content,
            content_rows.c.status,
            content_rows.c.created_at,
            content_rows.c.admin_reply,
        )
        .select_from(content_rows)
        .join(Product, Product.id == content_rows.c.product_id)
        .outerjoin(User, User.id == content_rows.c.user_id)
    )
    count_query = select(func.count()).select_from(content_rows)
    if selected_status:
        query = query.where(content_rows.c.status == selected_status)
        count_query = count_query.where(content_rows.c.status == selected_status)
    total = db.session.execute(count_query).scalar_one()
    pages = max(1, (total + ADMIN_REVIEW_PAGE_SIZE - 1) // ADMIN_REVIEW_PAGE_SIZE)
    page = min(page, pages)
    entries = db.session.execute(
        query.order_by(content_rows.c.created_at.desc(), content_rows.c.id.desc())
        .offset((page - 1) * ADMIN_REVIEW_PAGE_SIZE)
        .limit(ADMIN_REVIEW_PAGE_SIZE)
    ).all()
    return render_template(
        "admin/reviews.html",
        entries=entries,
        selected_status=selected_status,
        page=page,
        pages=pages,
        total=total,
    )


@admin_bp.post("/reviews/<content_type>/<int:content_id>/moderate")
@admin_required
def moderate_review_content(content_type, content_id):
    new_status = request.form.get("status", "").strip()
    if new_status not in {"approved", "hidden"}:
        abort(400, description="Trạng thái kiểm duyệt không hợp lệ.")
    if content_type == "review":
        entry = db.session.get(Review, content_id)
    elif content_type == "comment":
        entry = db.session.get(ProductComment, content_id)
    else:
        abort(404)
    if entry is None:
        abort(404)
    entry.status = new_status
    entry.updated_at = datetime.now()
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "Could not moderate %s content %s", content_type, content_id
        )
        flash("Không thể cập nhật trạng thái nội dung. Vui lòng thử lại.")
    else:
        flash("Nội dung đã được duyệt." if new_status == "approved" else "Nội dung đã được ẩn.")
    return redirect(url_for("admin.reviews", status=request.args.get("status", "")))


@admin_bp.post("/reviews/comments/<int:comment_id>/reply")
@admin_required
def reply_to_product_comment(comment_id):
    comment = db.session.get(ProductComment, comment_id)
    if comment is None:
        abort(404)
    reply = request.form.get("reply", "").strip()
    if not reply or len(reply) > ADMIN_CONTENT_MAX_LENGTH:
        flash("Nội dung trả lời không được để trống và tối đa 2.000 ký tự.")
        return redirect(url_for("admin.reviews", status=request.args.get("status", "")))

    admin_id = session.get("user_id")
    admin_user = db.session.get(User, admin_id) if admin_id is not None else None
    if admin_user is None or admin_user.role != "admin":
        abort(403)
    comment.admin_reply = reply
    comment.replied_by_user_id = admin_user.id
    comment.replied_at = datetime.now()
    comment.updated_at = datetime.now()
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not save Admin reply for product comment %s", comment_id)
        flash("Không thể lưu câu trả lời. Vui lòng thử lại.")
    else:
        flash("Đã lưu câu trả lời của Admin.")
    return redirect(url_for("admin.reviews", status=request.args.get("status", "")))
