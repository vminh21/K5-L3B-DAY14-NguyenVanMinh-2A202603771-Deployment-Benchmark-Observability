"""
Production AI Agent — Part 6 Final Project.

    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2

Endpoints:
    GET  /health  liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
    GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không
    POST /ask     X-API-Key → rate limit (429) → cost guard (402) → mock LLM
"""
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log của app là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"extra_fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown
# uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi chạy phần sau `yield` và thoát với exit code 0. Không tự đăng ký signal handler
# để không đè mất handler của uvicorn.
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              auth_configured=bool(settings.agent_api_key))
    yield
    log_event("shutdown", reason="SIGTERM / lifespan shutdown, in-flight requests drained")
    try:
        r.close()
    except Exception:
        pass


app = FastAPI(title="Production AI Agent", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_event("unhandled_error", logging.ERROR, request_id=request_id, path=request.url.path)
        response = JSONResponse(status_code=500, content={"error": "internal_error",
                                                          "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    log_event("request", request_id=request_id, method=request.method, path=request.url.path,
              status=response.status_code, ms=round((time.perf_counter() - start) * 1000, 1))
    return response


# ─────────────────────────────────────────────────────────
# Lỗi: trả JSON gọn, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    fields = [".".join(str(p) for p in e.get("loc", [])[1:]) or "body" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "invalid_request",
                                                  "detail": "Body phải là JSON {\"question\": \"...\"} "
                                                            "(1–2000 ký tự)",
                                                  "fields": fields})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, path=request.url.path, exc_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: chỉ cần process còn sống — KHÔNG kiểm Redis (Redis chết không nên restart agent)."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: Redis không kết nối được → 503 để load balancer ngừng gửi traffic."""
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "down"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)        # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                   # 402 khi hết DAILY/MONTHLY budget
    answer = llm_ask(body.question)
    cost = record_cost(estimate_tokens(body.question), estimate_tokens(answer))
    return {"question": body.question, "answer": answer, "cost_usd": round(cost, 8)}
