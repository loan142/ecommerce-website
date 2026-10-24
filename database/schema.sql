-- Reference schema only. The starter currently creates tables through SQLAlchemy.
-- Keep this file aligned with backend/models/__init__.py if your group chooses SQL migrations.
CREATE TABLE product (
  id INTEGER PRIMARY KEY,
  name VARCHAR(150) NOT NULL,
  description TEXT,
  price INTEGER NOT NULL DEFAULT 0,
  old_price INTEGER,
  image_url VARCHAR(500),
  stock INTEGER NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);
