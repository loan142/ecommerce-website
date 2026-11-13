-- Reference schema only. The starter currently creates tables through SQLAlchemy.
-- Keep this file aligned with backend/models/__init__.py if your group chooses SQL migrations.
CREATE TABLE category (
  id INTEGER PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  slug VARCHAR(120) NOT NULL UNIQUE
);

CREATE TABLE product (
  id INTEGER PRIMARY KEY,
  name VARCHAR(150) NOT NULL,
  description TEXT,
  price INTEGER NOT NULL DEFAULT 0,
  old_price INTEGER,
  image_url VARCHAR(500),
  stock INTEGER NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  category_id INTEGER REFERENCES category(id),
  deleted_at DATETIME
);

CREATE INDEX ix_product_category_id ON product (category_id);

-- Review data is extended by migrations/add_product_reviews.py.
CREATE TABLE review (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES product(id),
  user_id INTEGER REFERENCES user(id),
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment TEXT,
  status VARCHAR(20) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'hidden')),
  purchase_verified BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME,
  updated_at DATETIME
);

CREATE UNIQUE INDEX uq_review_product_customer
  ON review (product_id, user_id)
  WHERE user_id IS NOT NULL;

CREATE TABLE product_comment (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES product(id),
  user_id INTEGER NOT NULL REFERENCES user(id),
  kind VARCHAR(20) NOT NULL DEFAULT 'comment'
    CHECK (kind IN ('comment', 'question')),
  content TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'hidden')),
  admin_reply TEXT,
  replied_by_user_id INTEGER REFERENCES user(id),
  replied_at DATETIME,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE INDEX ix_product_comment_product_id ON product_comment (product_id);
CREATE INDEX ix_product_comment_user_id ON product_comment (user_id);
CREATE INDEX ix_product_comment_status ON product_comment (status);
