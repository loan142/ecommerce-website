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

### Nâng cấp database để dùng danh mục
Trước khi cập nhật ứng dụng, hãy sao lưu `instance/ecommerce.db` rồi chạy
migration dưới đây bằng đường dẫn backup của bạn. Script xác minh backup, giữ
nguyên sản phẩm/đơn hàng, để sản phẩm cũ ở trạng thái chưa phân loại và có thể
chạy lại an toàn:
```powershell
.\.venv\Scripts\python.exe migrations\add_product_categories.py --backup "backups\before-task08-categories-20261111-0855\instance\ecommerce.db"
```
Sau khi migration thành công, khởi động lại ứng dụng bằng `.\.venv\Scripts\python.exe run.py`.

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
- Các trang admin yêu cầu đăng nhập bằng tài khoản có role `admin`.

### Đổi mật khẩu Admin trên máy cục bộ
Chạy lệnh sau trong Terminal tại thư mục dự án. Nhập email Admin và mật khẩu
mới (ít nhất 12 ký tự) hai lần; mật khẩu được nhập ẩn và lưu dưới dạng băm.
```powershell
.\.venv\Scripts\python.exe reset_admin_password.py
```

## Phạm vi starter
Đã có trang Admin quản lý sản phẩm (thêm, sửa, ẩn/hiện), quản lý đơn hàng,
Dashboard và CSRF protection cho các thao tác POST. Checkout lưu thông tin
người nhận, giá tại thời điểm đặt và cập nhật tồn kho trong transaction.
COD là thanh toán khi nhận hàng; website không xử lý thanh toán trực tuyến.

Admin có giao diện quản trị riêng với Dashboard, sản phẩm, danh mục và đơn hàng.
Sản phẩm được chuyển vào thùng rác bằng xóa mềm; ảnh và lịch sử đơn hàng được giữ
nguyên, và sản phẩm có thể khôi phục về trạng thái hiển thị cũ.

Admin quản lý danh mục tại **Quản trị → Danh mục**; danh mục không thể xóa để
tránh mất liên kết sản phẩm. Sản phẩm cũ chưa được phân loại được đánh dấu trong
Admin và không tự động gán; hãy lọc **Chưa phân loại** rồi chọn danh mục đúng khi
sửa sản phẩm. Trang **Sản phẩm** cho phép kết hợp tìm kiếm, danh mục và sắp xếp.

### Cập nhật schema đơn hàng SQLite
Ứng dụng dùng `instance/ecommerce.db`. Khi nâng cấp cơ sở dữ liệu cũ, hãy sao
lưu database trước rồi chạy migration (đường dẫn backup phải trỏ tới bản sao
SQLite hợp lệ; migration kiểm tra số bản ghi và có thể chạy lại an toàn):
```powershell
.\.venv\Scripts\python.exe migrations\add_order_shipping_fields.py --backup "backups\before-checkout-20261106-1312\database\ecommerce.db"
```
Các đơn cũ giữ nguyên ID và sản phẩm; những thông tin giao hàng và ngày tạo
chưa từng được lưu sẽ để trống, không được tự điền.

### Cập nhật schema xóa mềm sản phẩm
Sao lưu `instance/ecommerce.db` trước, sau đó chạy migration với đường dẫn tới
bản sao mới tạo. Migration chỉ thêm `product.deleted_at`, kiểm tra dữ liệu so với
backup và có thể chạy lại an toàn:
```powershell
.\.venv\Scripts\python.exe migrations\add_product_soft_delete.py --backup "backups\before-admin-separation-soft-delete-YYYYMMDD-HHMMSS\ecommerce.db"
```
