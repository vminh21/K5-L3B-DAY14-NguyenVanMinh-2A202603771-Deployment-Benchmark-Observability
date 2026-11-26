"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • Client được tạo "lazy": redis-py chỉ mở kết nối khi có lệnh đầu tiên, nên app vẫn
    khởi động được khi Redis chưa sẵn sàng (không ping lúc import)
  • Timeout ngắn để /ready trả 503 nhanh thay vì treo khi Redis chết
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: True nếu PING thành công, mọi lỗi kết nối → False."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
