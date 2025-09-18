TEMPLATE_NO_ORDER = (
    "Để em kiểm tra chương trình của cửa hàng xem có freeship cho mình không nha chị/anh."
)

TEMPLATE_FREESHIP = (
    "Dạ đơn hàng của mình đang được hưởng ưu đãi freeship đó ạ. Em gửi hàng ngay nha chị/anh."
)

TEMPLATE_FIRST_TIME = (
    "Dạ chị/anh ơi, hiện bên em chưa chạy chương trình miễn ship. Nhưng đang có chương trình giảm giá sâu cực kỳ ưu đãi chỉ trong hôm nay, mình đặt ngay kẻo lỡ nhé ạ!"
)

TEMPLATE_ASK_FREE = (
    "Dạ mong chị/anh thông cảm giúp em nha. Phí ship của các đơn hàng khá cao, bên em đã hỗ trợ một phần ship và giá sản phẩm tốt nhất có thể rồi ạ. Nhờ mình hỗ trợ phần ship này giúp em nhé, em cảm ơn nhiều!"
)

TEMPLATE_ESCALATE_FREESHIP_NEW = (
    "Dạ phí ship toàn quốc nhà em là khoảng 35k ạ. Vì đây là đơn đầu, bên em xin hỗ trợ miễn phí ship đơn này cho chị/anh nhé. Nếu tiện mình cân thêm sản phẩm nào thì ủng hộ em với nha."
)

TEMPLATE_ESCALATE_FREESHIP_LOYAL = (
    "Dạ phí ship toàn quốc nhà em là khoảng 35k ạ. Vì chị/anh là khách thân, bên em xin hỗ trợ miễn phí ship đơn này cho mình nhé. Nếu tiện mình cân thêm sản phẩm nào thì ủng hộ em với nha."
)

def render_fee_amount(fee: int) -> str:
    return f"Dạ phí ship đơn hiện tại của mình là {fee:,}đ ạ."


