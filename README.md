# E-commerce Website (Flask)

Bộ khung dự án thương mại điện tử theo cấu trúc `frontend/`, `backend/`, `database/`.

## Yêu cầu
- Python 3.10+
- Git
- VS Code

## Chạy trên Windows
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```
Mở http://127.0.0.1:5000.

Nếu PowerShell không cho kích hoạt môi trường ảo, có thể chạy:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

## Cấu trúc
- `frontend/`: HTML/Jinja templates, CSS, JS, images.
- `backend/routes/`: Flask Blueprints cho từng module.
- `backend/services/`: logic nghiệp vụ tái sử dụng.
- `backend/models/`: SQLAlchemy models.
- `backend/app.py`: tạo và cấu hình Flask app.
- `database/schema.sql`: schema tham khảo; hiện app tạo bảng qua SQLAlchemy.
- `run.py`: điểm khởi chạy.

## Git workflow
1. `main` giữ phiên bản ổn định; không push trực tiếp.
2. Cập nhật code mới:
   ```bash
   git switch main
   git pull origin main
   ```
3. Tạo branch riêng:
   ```bash
   git switch -c feature/product-search
   ```
4. Commit và push:
   ```bash
   git add .
   git commit -m "Implement product search"
   git push -u origin feature/product-search
   ```
5. Tạo Pull Request trên GitHub, review, chạy thử rồi mới merge.

## Quy ước nhóm
- Không tự ý đổi tên route/model/trường database dùng chung nếu chưa báo nhóm.
- Dùng tiền VND dạng số nguyên, ví dụ `150000`.
- Không commit `.env`, mật khẩu, database cục bộ hoặc khóa API.
- Mỗi PR nên nhỏ, có mô tả và kết quả kiểm thử.
- Các route admin hiện là giao diện mẫu, chưa có bảo vệ quyền truy cập.

## Phạm vi starter
Đã có giao diện nền và route mẫu cho trang chủ, sản phẩm, chi tiết sản phẩm, đăng ký/đăng nhập, giỏ hàng, checkout demo, liên hệ, chính sách và admin. Cần bổ sung kiểm thử, CSRF protection, xác thực/phân quyền admin, xử lý thanh toán an toàn, validation và migration trước khi triển khai thật.
