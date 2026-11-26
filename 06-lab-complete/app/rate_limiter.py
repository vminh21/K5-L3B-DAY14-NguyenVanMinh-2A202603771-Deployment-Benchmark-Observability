"""
Rate limiting (tiêu chí 7) — sliding window log trên Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis nên mọi worker / instance dùng chung và vẫn còn sau khi restart
"""
import hashlib
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    # Không lưu API key gốc trong Redis — chỉ lưu hash
    return "rl:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)   # bỏ request ngoài cửa sổ
        pipe.zcard(bucket)                                         # đếm request trong 60s
        _, count = pipe.execute()
        if count >= limit:
            oldest = r.zrange(bucket, 0, 0, withscores=True)
            retry = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {limit} requests / {WINDOW_SECONDS}s",
                headers={"Retry-After": str(max(1, retry))},
            )
        pipe = r.pipeline()
        pipe.zadd(bucket, {f"{now}-{uuid.uuid4().hex[:8]}": now})
        pipe.expire(bucket, WINDOW_SECONDS + 1)
        pipe.execute()
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Rate limiter unavailable (Redis)")
