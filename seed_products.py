from backend.app import create_app
from backend.models import db, Product

app = create_app()

sample_products = [
    {
        "name": "Bình gốm men lam họa tiết sen",
        "description": "Bình gốm thủ công lấy cảm hứng từ hoa sen Việt Nam, phù hợp trang trí phòng khách.",
        "price": 350000,
        "old_price": 390000,
        "image_url": "https://images.unsplash.com/photo-1578749556568-bc2c40e68b61?auto=format&fit=crop&w=800&q=85",
        "stock": 10,
    },
    {
        "name": "Bộ tách trà gốm thủ công",
        "description": "Bộ tách trà mang vẻ đẹp mộc mạc, phù hợp dùng hằng ngày hoặc làm quà tặng.",
        "price": 280000,
        "old_price": None,
        "image_url": "https://images.unsplash.com/photo-1578749556568-bc2c40e68b61?auto=format&fit=crop&w=800&q=85",
        "stock": 15,
    },
    {
        "name": "Lọ hoa gốm đất nung mộc",
        "description": "Lọ hoa với sắc đất tự nhiên, phù hợp không gian sống theo phong cách hoài cổ.",
        "price": 220000,
        "old_price": 250000,
        "image_url": "https://images.unsplash.com/photo-1578749556568-bc2c40e68b61?auto=format&fit=crop&w=800&q=85",
        "stock": 8,
    },
]

with app.app_context():
    added = 0

    for item in sample_products:
        existing = Product.query.filter_by(name=item["name"]).first()

        if existing:
            print(f"Đã tồn tại, bỏ qua: {item['name']}")
            continue

        product = Product(**item, is_active=True)
        db.session.add(product)
        added += 1

    db.session.commit()
    print(f"Hoàn tất! Đã thêm {added} sản phẩm mới.")