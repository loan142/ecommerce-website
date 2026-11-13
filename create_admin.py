from getpass import getpass
import sys

from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import User, db


def main():
    app = create_app()

    with app.app_context():
        email = input("Email admin: ").strip().lower()
        email_name, separator, email_domain = email.partition("@")
        if not separator or not email_name or not email_domain:
            print("Email không hợp lệ.")
            return 1

        if User.query.filter_by(email=email).first():
            print("Email đã tồn tại; không thay đổi tài khoản hiện có.")
            return 1

        full_name = input("Họ tên admin: ").strip()
        if not full_name:
            print("Họ tên không được để trống.")
            return 1

        password = getpass("Mật khẩu admin (ít nhất 12 ký tự): ")
        confirmation = getpass("Nhập lại mật khẩu: ")
        if len(password) < 12:
            print("Mật khẩu phải có ít nhất 12 ký tự.")
            return 1
        if password != confirmation:
            print("Hai mật khẩu không khớp.")
            return 1

        db.session.add(
            User(
                full_name=full_name,
                email=email,
                password_hash=generate_password_hash(password),
                role="admin",
            )
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            print("Không thể tạo tài khoản: email đã tồn tại.")
            return 1

        print(f"Đã tạo tài khoản Admin cho {email}. Mật khẩu không được hiển thị.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
