# Deployment Information

## Public URL

https://agent-production-cf1245af.up.railway.app

## Platform

**Railway** — project `day14-production-agent`, environment `production`, region `iad`.

| Service | Nguồn | Ghi chú |
|---|---|---|
| `agent` | `06-lab-complete/` (agent Part 6), build bằng `Dockerfile` multi-stage theo `railway.toml` (`builder = "DOCKERFILE"`, `healthcheckPath = "/health"`) | Lắng nghe `$PORT` do Railway cấp (`CMD ... --port ${PORT:-8000}`) |
| `Redis` | Railway database (`railway add --database redis`) | Agent kết nối qua private network `redis.railway.internal:6379` |

Các bước đã chạy (trong `06-lab-complete/`):

```bash
railway login
railway init --name day14-production-agent
railway add --database redis
railway add --service agent --variables "AGENT_API_KEY=<secret>" --variables 'REDIS_URL=${{Redis.REDIS_URL}}' \
  --variables ENVIRONMENT=production --variables RATE_LIMIT_PER_MINUTE=10 \
  --variables DAILY_BUDGET_USD=1.0 --variables MONTHLY_BUDGET_USD=10.0 --variables LOG_LEVEL=INFO
railway up --service agent --ci      # build Dockerfile → Deploy complete (deployment 3900d766…, SUCCESS)
railway domain --service agent       # → https://agent-production-cf1245af.up.railway.app
```

## Test Commands

Tự kiểm CP6 (đặt `BASE` là URL public, `AGENT_API_KEY` là key đã đặt trên Railway — key không ghi trong repo, Lab Coach cần thì xin trực tiếp):

```bash
export BASE=https://agent-production-cf1245af.up.railway.app
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả thật: **200 · 200 · 401 · 200** (chạy lần đầu lúc 2026-11-26 08:54:21 UTC; chạy lại và tự chụp terminal lúc 16:48 GMT+7) — xem [screenshots/test.png](screenshots/test.png).

### Health Check

```bash
curl https://agent-production-cf1245af.up.railway.app/health
# {"status":"ok","environment":"production"}
curl https://agent-production-cf1245af.up.railway.app/ready
# {"status":"ready","redis":"ok"}
```

### API Test (with authentication)

```bash
curl -X POST https://agent-production-cf1245af.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","cost_usd":1.2e-05}
```

### Rate limit (10 req/phút, state trong Redis của Railway)

```text
for i in 1..12: POST /ask  →  200 200 200 200 200 200 200 200 429 429 429 429
(2 request trước đó cùng cửa sổ 60 s → request thứ 11 trong cửa sổ bị chặn)
HTTP/2 429
retry-after: 44
{"detail":"Rate limit exceeded: 10 requests / 60s"}
```

### Script chấm với URL public

`python check_production_ready.py --public-url https://agent-production-cf1245af.up.railway.app` (chạy đầy đủ, có Docker):

```text
     ✅ Public URL: GET https://agent-production-cf1245af.up.railway.app/health → 200
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables), **không** ghi trong code hay repo:

| Biến | Giá trị |
|---|---|
| `PORT` | Railway tự inject |
| `AGENT_API_KEY` | secret ngẫu nhiên (`secrets.token_urlsafe(24)`), chỉ lưu trên Railway |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` — reference variable tới service Redis (`redis://default:***@redis.railway.internal:6379`) |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `LOG_LEVEL` | `INFO` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png) — Railway project: service `agent` + `Redis` online, tab Variables (giá trị secret bị che)
- [Service running](screenshots/running.png) — `railway status` + `railway deployment list`: agent Online tại URL public, Redis Online, deployment SUCCESS
- [Test results](screenshots/test.png) — 4 lệnh tự kiểm CP6: 200 / 200 / 401 / 200
