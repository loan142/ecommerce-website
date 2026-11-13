from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask import session
from backend.models import db, User

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            if user.role != "customer":
                flash("Thông tin đăng nhập không hợp lệ cho cổng khách hàng.")
                return render_template("login.html")
            session["user_id"] = user.id
            flash("Đăng nhập thành công.")
            return redirect(url_for("main.index"))
        flash("Email hoặc mật khẩu chưa đúng.")
    return render_template("login.html")

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not full_name or len(password) < 8:
            flash("Vui lòng nhập họ tên và mật khẩu tối thiểu 8 ký tự.")
        elif User.query.filter_by(email=email).first():
            flash("Email đã được sử dụng.")
        else:
            user = User(full_name=full_name, email=email, password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            flash("Đăng ký thành công. Mời bạn đăng nhập.")
            return redirect(url_for("auth.login"))
    return render_template("register.html")

@auth_bp.get("/logout")
def logout():
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id is not None else None
    session.clear()
    flash("Bạn đã đăng xuất.")
    if user is not None and user.role == "admin":
        return redirect(url_for("admin.login"))
    return redirect(url_for("auth.login"))
