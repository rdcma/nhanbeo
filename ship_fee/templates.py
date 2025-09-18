import random


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
    "Dạ mong c thông cảm giúp e nha, phí ship của nhãn hàng khá cao, bên e đã hỗ trợ mình 1 phần ship và ưu đãi về giá sản phẩm thấp nhất có thể cho mình rùi ạ. Nhờ mình hỗ trợ phần ship này giúp e nha, e cảm ơn c nhiều."
)

# Ask freeship first-time specific reply
TEMPLATE_ASK_FREE_FIRST_TIME = (
    "Dạ chị ơi hiện nhà e chưa có chạy chương trình miễn ship nhưng đang có chương trình giảm giá sâu cực kỳ ưu đãi chỉ trong hôm nay, mình đặt ngay kẻo lỡ nhé ạ!"
)

TEMPLATE_ESCALATE_FREESHIP_NEW = (
    "Dạ phí ship toàn quốc nhà em là khoảng 35k ạ. Vì đây là đơn đầu, bên em xin hỗ trợ miễn phí ship đơn này cho chị/anh nhé. Nếu tiện mình cân thêm sản phẩm nào thì ủng hộ em với nha."
)

TEMPLATE_ESCALATE_FREESHIP_LOYAL = (
    "Dạ phí ship toàn quốc nhà em là khoảng 35k ạ. Vì chị/anh là khách thân, bên em xin hỗ trợ miễn phí ship đơn này cho mình nhé. Nếu tiện mình cân thêm sản phẩm nào thì ủng hộ em với nha."
)

def render_fee_amount(fee: int) -> str:
    variants = [
        f"Dạ phí ship đơn hiện tại của mình là {fee:,}đ ạ.",
        f"Dạ em kiểm tra đơn này, phí ship là {fee:,}đ ạ.",
        f"Em báo mình phí ship hiện tại: {fee:,}đ ạ.",
        f"Dạ hiện phí vận chuyển cho đơn của mình là {fee:,}đ ạ.",
        f"Em vừa tra phí ship là {fee:,}đ ạ.",
        f"Em xác nhận phí ship đơn này là {fee:,}đ ạ.",
        f"Dạ phí vận chuyển hiện tại là {fee:,}đ ạ.",
    ]
    return random.choice(variants)


# Complaint priority sentence (must be used first)
TEMPLATE_FEE_COMPLAINT_PRIORITY = (
    "Dạ em hiểu ạ, phí ship hiện tại là do bên vận chuyển quy định chung. Nhưng chị yên tâm, hàng bên em chất lượng chuẩn xưởng, chị nhận được sẽ thấy xứng đáng ạ."
)


def render_fee_complaint() -> str:
    return TEMPLATE_FEE_COMPLAINT_PRIORITY


def render_cancel_threat_save() -> str:
    variants = [
        "Dạ em xin lỗi vì bất tiện ạ. Em hỗ trợ ưu đãi phí ship để mình yên tâm nha, mình cho em địa chỉ để em áp mức tốt nhất ạ?",
        "Đừng vội ạ, em hỗ trợ giảm phí ship cho đơn này để mình trải nghiệm trước nha?",
    ]
    return random.choice(variants)


TEMPLATE_TAG_AGENT = (
    "Dạ để em nhờ nhân viên bên em hỗ trợ trực tiếp phí ship cho mình nhanh nhất ạ."
)


