"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày và theo tháng (UTC)
  • Tổng chi phí đạt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402
"""
from datetime import datetime, timezone

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """~4 ký tự / token (ước lượng giống mock LLM)."""
    return max(1, len(text) // 4)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    daily_key, monthly_key = _keys()
    try:
        daily, monthly = r.mget(daily_key, monthly_key)
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Cost guard unavailable (Redis)")
    if float(daily or 0) >= settings.daily_budget_usd:
        raise HTTPException(status_code=402, detail="Daily budget exceeded")
    if float(monthly or 0) >= settings.monthly_budget_usd:
        raise HTTPException(status_code=402, detail="Monthly budget exceeded")


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    try:
        pipe = r.pipeline()
        pipe.incrbyfloat(daily_key, cost)
        pipe.expire(daily_key, 2 * 24 * 3600)
        pipe.incrbyfloat(monthly_key, cost)
        pipe.expire(monthly_key, 32 * 24 * 3600)
        pipe.execute()
    except redis.RedisError:
        pass  # câu trả lời đã có; không làm hỏng response chỉ vì không ghi được cost
    return cost
