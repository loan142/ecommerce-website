import hmac
import secrets

from flask import abort, request, session


def generate_csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token():
    expected = session.get("csrf_token")
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if (
        not isinstance(expected, str)
        or not expected.isascii()
        or not isinstance(submitted, str)
        or not submitted.isascii()
        or not hmac.compare_digest(expected, submitted)
    ):
        abort(400, description="Mã bảo vệ biểu mẫu không hợp lệ hoặc đã hết hạn.")
