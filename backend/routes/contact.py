from flask import Blueprint, render_template, request, redirect, url_for, flash

contact_bp = Blueprint("contact", __name__)

@contact_bp.route("/", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        # TODO: persist feedback to database or send it to a configured service.
        flash("Đã nhận biểu mẫu mẫu. Nhóm cần triển khai lưu feedback.")
        return redirect(url_for("contact.contact"))
    return render_template("contact.html")
