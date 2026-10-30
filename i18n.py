from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# Từ điển bản dịch thông báo lỗi / Response
TRANSLATIONS = {
    "vi": {
        "wallet_not_found": "Ví không hợp lệ hoặc không tồn tại",
        "insufficient_balance": "Số dư trong ví không đủ",
        "invalid_amount": "Số tiền giao dịch phải lớn hơn 0",
        "system_busy": "Hệ thống đang bận xử lý, vui lòng thử lại sau",
        "same_wallet_transfer": "Không thể chuyển tiền cho chính mình",
        "success": "Thành công"
    },
    "en": {
        "wallet_not_found": "Wallet is invalid or not found",
        "insufficient_balance": "Insufficient wallet balance",
        "invalid_amount": "Transaction amount must be greater than 0",
        "system_busy": "System busy, please try again later",
        "same_wallet_transfer": "Cannot transfer money to yourself",
        "success": "Success"
    }
}

class LanguageMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Tự động đọc ngôn ngữ từ Header 'Accept-Language' hoặc Query Parameter '?lang=en'
        lang = request.query_params.get("lang") or request.headers.get("Accept-Language", "vi")
        request.state.lang = "en" if "en" in lang.lower() else "vi"
        response = await call_next(request)
        return response

def get_text(key: str, lang: str = "vi") -> str:
    """Hàm lấy câu thông báo theo ngôn ngữ chỉ định"""
    return TRANSLATIONS.get(lang, TRANSLATIONS["vi"]).get(key, key)