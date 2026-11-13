import os
import secrets
from pathlib import Path

from flask import Flask, abort, redirect, request, session, url_for
from backend.csrf import generate_csrf_token, validate_csrf_token
from backend.models import User, db


def _load_secret_key(instance_path):
    secret_key_path = Path(instance_path) / "flask_secret_key"
    secret_key_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        secret_key = secret_key_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        secret_key = secrets.token_hex(32)
        try:
            with secret_key_path.open("x", encoding="ascii") as secret_key_file:
                secret_key_file.write(secret_key)
        except FileExistsError:
            secret_key = secret_key_path.read_text(encoding="ascii").strip()

    if not secret_key:
        raise RuntimeError(f"Flask secret key file is empty: {secret_key_path}")
    return secret_key


def create_app(config=None):
    # HTML templates live in ../frontend; static files are served from frontend/.
    app = Flask(
        __name__,
        template_folder="../frontend",
        static_folder="../frontend",
        static_url_path="/static",
    )
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI="sqlite:///ecommerce.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=26 * 1024 * 1024,
    )
    if config is not None:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _load_secret_key(app.instance_path)

    db.init_app(app)

    @app.before_request
    def protect_state_changing_requests():
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.blueprint != "admin"
            and request.path != "/admin"
            and not request.path.startswith("/admin/")
        ):
            validate_csrf_token()

    @app.before_request
    def protect_admin_area():
        is_admin_path = request.path == "/admin" or request.path.startswith("/admin/")
        if (
            not is_admin_path
            or request.endpoint == "static"
            or request.path in {"/admin/login", "/admin/login/"}
        ):
            return None

        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("admin.login"))

        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            return redirect(url_for("admin.login"))
        if user.role != "admin":
            abort(403)
        return None

    @app.context_processor
    def inject_logged_in_user():
        user_id = session.get("user_id")
        user = db.session.get(User, user_id) if user_id is not None else None
        if user_id is not None and user is None:
            session.clear()
        from backend.services.product_images import image_source

        return {
            "logged_in_user": user,
            "csrf_token": generate_csrf_token,
            "image_source": image_source,
        }

    from backend.routes.main import main_bp
    from backend.routes.products import products_bp
    from backend.routes.auth import auth_bp
    from backend.routes.cart import cart_bp
    from backend.routes.contact import contact_bp
    from backend.routes.admin import admin_bp
    from backend.routes.customer_orders import customer_orders_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(products_bp, url_prefix="/products")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(cart_bp, url_prefix="/cart")
    app.register_blueprint(contact_bp, url_prefix="/contact")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(customer_orders_bp, url_prefix="/my-orders")

    with app.app_context():
        from backend import models
        db.create_all()
    return app
