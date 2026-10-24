from flask import Flask
from backend.models import db

def create_app():
    # HTML templates live in ../frontend; static files are served from frontend/.
    app = Flask(
        __name__,
        template_folder="../frontend",
        static_folder="../frontend",
        static_url_path="/static",
    )
    app.config["SECRET_KEY"] = "dev-only-change-me"  # Override via environment in real use.
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///ecommerce.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    from backend.routes.main import main_bp
    from backend.routes.products import products_bp
    from backend.routes.auth import auth_bp
    from backend.routes.cart import cart_bp
    from backend.routes.contact import contact_bp
    from backend.routes.admin import admin_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(products_bp, url_prefix="/products")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(cart_bp, url_prefix="/cart")
    app.register_blueprint(contact_bp, url_prefix="/contact")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    with app.app_context():
        from backend import models
        db.create_all()
    return app
