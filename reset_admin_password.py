from getpass import getpass
import sys

from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.models import User, db


def main():
    app = create_app()

    with app.app_context():
        email = input("Email tài khoản Admin: ").strip().lower()
        admin = User.query.filter_by(email=email, role="admin").first()
        if admin is None:
            print("Không tìm thấy tài khoản Admin với email đó.")
            return 1

        password = getpass("Mật khẩu mới (ít nhất 12 ký tự): ")
        confirmation = getpass("Nhập lại mật khẩu mới: ")
        if len(password) < 12:
            print("Mật khẩu phải có ít nhất 12 ký tự.")
            return 1
        if password != confirmation:
            print("Hai mật khẩu không khớp.")
            return 1

        admin.password_hash = generate_password_hash(password)
        db.session.commit()
        print(f"Đã đổi mật khẩu cho tài khoản Admin {email}. Mật khẩu không được hiển thị.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
