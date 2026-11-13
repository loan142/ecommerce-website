ORDER_STATUS_LABELS = {
    "pending": "Chờ xác nhận",
    "confirmed": "Đã xác nhận",
    "shipping": "Đang giao",
    "completed": "Hoàn thành",
    "cancelled": "Đã hủy",
}

ORDER_STATUS_TRANSITIONS = {
    "pending": ("confirmed", "cancelled"),
    "confirmed": ("shipping", "cancelled"),
    "shipping": ("completed",),
    "completed": (),
    "cancelled": (),
}
