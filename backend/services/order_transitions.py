from sqlalchemy import update

from backend.models import Order, OrderItem, Product, db
from backend.order_status import ORDER_STATUS_TRANSITIONS


class OrderTransitionError(RuntimeError):
    pass


def transition_order(
    order_id, new_status, owner_id=None, allowed_previous_statuses=None
):
    try:
        connection = db.session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        order_query = Order.query.filter_by(id=order_id)
        if owner_id is not None:
            order_query = order_query.filter_by(user_id=owner_id)
        order = order_query.first()
        if order is None:
            db.session.rollback()
            return "not_found", None

        previous_status = order.status
        if (
            allowed_previous_statuses is not None
            and previous_status not in allowed_previous_statuses
        ) or new_status not in ORDER_STATUS_TRANSITIONS.get(previous_status, ()):
            db.session.rollback()
            return "invalid_transition", previous_status

        update_query = db.session.query(Order).filter_by(
            id=order_id,
            status=previous_status,
        )
        if owner_id is not None:
            update_query = update_query.filter_by(user_id=owner_id)
        if update_query.update(
            {Order.status: new_status}, synchronize_session=False
        ) != 1:
            db.session.rollback()
            return "conflict", previous_status

        if new_status == "cancelled":
            items = OrderItem.query.filter_by(order_id=order_id).all()
            for item in items:
                if item.quantity <= 0:
                    raise OrderTransitionError(
                        f"Số lượng sản phẩm #{item.product_id} trong đơn hàng không hợp lệ."
                    )
                restored = db.session.query(Product).filter_by(
                    id=item.product_id
                ).update(
                    {Product.stock: Product.stock + item.quantity},
                    synchronize_session=False,
                )
                if restored != 1:
                    raise OrderTransitionError(
                        f"Không tìm thấy sản phẩm #{item.product_id} của đơn hàng."
                    )

        db.session.commit()
        return "updated", previous_status
    except Exception:
        db.session.rollback()
        raise
