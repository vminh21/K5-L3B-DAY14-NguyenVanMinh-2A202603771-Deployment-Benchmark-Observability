# Day 14 Lab — Mission Answers

> Mọi output dưới đây là output thật của các lệnh đã chạy trên máy (macOS, Docker Desktop, Python 3.12 venv) ngày 2026-11-26.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns tìm được trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | Hardcode secret | `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` (dòng 17) | Push lên GitHub là lộ key; muốn đổi key phải sửa code và deploy lại |
| 2 | Hardcode connection string có mật khẩu | `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"` (dòng 18) | Lộ mật khẩu DB; `localhost` sai hoàn toàn khi chạy trong container/cloud |
| 3 | Log ra secret + dùng `print` thay vì logging | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` (dòng 34) | Secret nằm trong log của platform; `print` không có level, không có cấu trúc để lọc/tìm |
| 4 | Không có config management | `DEBUG = True`, `MAX_TOKENS = 500` (dòng 21–22) | Không đổi được giữa dev/staging/prod mà không sửa code |
| 5 | Không có health check | không có `/health` (chạy thử: `GET /health` → **404**) | Platform không biết agent còn sống để restart |
| 6 | Bind `localhost` | `host="localhost"` (dòng 51) | Trong container chỉ nhận kết nối từ chính container → bên ngoài không gọi được |
| 7 | Port cố định | `port=8000` (dòng 52) | Railway/Render cấp port qua biến `PORT`, app không nghe đúng port sẽ fail health check |
| 8 | `reload=True` khi chạy | dòng 53 | Chế độ dev: tốn tài nguyên, theo dõi file thay đổi, không dùng cho production |
| 9 | Không xử lý graceful shutdown | không có lifespan / SIGTERM | Tắt đột ngột cắt ngang request đang chạy |

### Exercise 1.2: Chạy bản develop

```text
$ curl -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
HTTP 200
$ curl http://localhost:8000/health
{"detail":"Not Found"}
HTTP 404
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}
HTTP 422
```

Log server in ra cả secret — đúng anti-pattern #3:

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
```

Nó chạy, nhưng không production-ready: không health check, lộ secret trong log, nhận `question` qua query string.

Bản production (`cp .env.example .env && python app.py`):

```text
GET /health → 200 {"status":"ok","uptime_seconds":3.8,"version":"1.0.0","environment":"development",...}
GET /ready  → 200 {"ready":true}
POST /ask (JSON) → 200 {"question":"Hello","answer":"...","model":"gpt-4o-mini"}
SIGTERM → "Shutting down / Waiting for application shutdown / Application shutdown complete / Finished server process"
```

### Exercise 1.3: Bảng so sánh

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`OPENAI_API_KEY`, `DEBUG`, port) | `config.py` đọc env (`os.getenv`), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy được ở mọi môi trường; secret không vào git; đổi cấu hình không cần build lại |
| Secret | Nằm trong code và bị `print` ra log | Đọc từ env, log chỉ ghi `question_length`, không ghi secret | Lộ key = mất tiền/bị lạm dụng; log thường được nhiều người/hệ thống đọc |
| Health check | Không có (404) | `/health` (liveness, kèm uptime) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform restart container chết; load balancer chỉ gửi traffic vào instance đã sẵn sàng |
| Logging | `print()` | JSON qua `logging` (`{"event":"agent_request",...}`) | Log aggregator (Loki, Datadog) parse/lọc được theo field; có level |
| Shutdown | Đột ngột | `lifespan` + handler SIGTERM, uvicorn chờ request in-flight | Không cắt ngang request của user khi deploy/scale down |
| Host/Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Chạy được trong container và trên platform tự cấp port |
| Reload/Debug | `reload=True` luôn bật | `reload=settings.debug` | Không chạy chế độ dev trên production |
| Input | Query param, không validate | JSON body, trả 422 rõ ràng khi thiếu `question` | API rõ ràng, lỗi dễ hiểu cho client |

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Python đầy đủ (Debian + build tools), rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`), mọi lệnh `COPY`/`CMD` sau đó tính từ đây.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo layer. Requirements ít đổi → layer `pip install` được dùng lại; sửa code chỉ build lại layer `COPY app.py` phía sau. Nếu `COPY . .` trước thì mỗi lần sửa code là cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi `docker run image <lệnh khác>`; `ENTRYPOINT` là chương trình cố định của container, tham số `docker run` được nối thêm vào sau. Hay dùng `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", ...]`.

### Exercise 2.2: Build và chạy (build từ gốc repo)

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d -p 8000:8000 my-agent:develop
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
HTTP 200
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":4.0,"container":true}
HTTP 200
```

### Exercise 2.3: Multi-stage build và kích thước image

- **Stage 1 (builder)** `python:3.11-slim` + `gcc`, `libpq-dev`, chạy `pip install --user -r requirements.txt` → toàn bộ package nằm trong `/root/.local`.
- **Stage 2 (runtime)** `python:3.11-slim` mới tinh, tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` + `main.py` + `utils/`, có `HEALTHCHECK`, `CMD uvicorn ... --workers 2`.
- **Vì sao nhỏ hơn:** image cuối không chứa gcc/header/apt cache/pip cache của stage builder, và base là `slim` thay vì bản full (~1 GB). Layer chứa package chỉ còn 41.2 MB (`docker history`).

Đo bằng `docker images`:

```text
REPOSITORY   TAG        IMAGE ID       SIZE
my-agent     advanced   920ea18cdd18   265MB
my-agent     develop    6fdb60187c8b   1.67GB
```

- Develop: **1.67 GB** (1670 MB)
- Production (advanced): **265 MB**
- Chênh lệch: (1670 − 265) / 1670 = **giảm 84.1 %**

(`docker image inspect -f '{{.Size}}'` cho kích thước nén: 415 378 595 B ≈ 396 MB vs 57 515 533 B ≈ 55 MB, cũng giảm ~86 %.)

### Exercise 2.4: Docker Compose stack (`02-docker/production`)

```text
                ┌──────────────────── network: internal (bridge) ─────────────────────┐
Client ──:80──▶ │ nginx (reverse proxy, rate limit 10r/s, security headers)           │
                │    │ proxy_pass http://agent_backend (agent:8000)                    │
                │    ▼                                                                 │
                │ agent (FastAPI, build multi-stage, healthcheck /health)              │
                │    ├──▶ redis:6379   (session cache / rate limit, healthcheck ping)  │
                │    └──▶ qdrant:6333  (vector DB cho RAG, healthcheck tcp)           │
                └─────────────────────────────────────────────────────────────────────┘
```

- Agent chỉ start khi `redis` và `qdrant` healthy (`depends_on: condition: service_healthy`); agent **không** expose port, mọi traffic đi qua Nginx.
- Các service gọi nhau bằng tên service qua DNS nội bộ của network `internal`.

```text
$ touch .env.local && docker compose -p k5-day14-docker up -d --build
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 15 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 15 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 25 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 25 seconds (healthy)   6379/tcp

$ curl http://localhost/health
{"status":"ok","uptime_seconds":14.7,"version":"2.0.0","timestamp":"2026-11-26T08:11:14.702100"}
HTTP 200
$ curl http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
HTTP 200
(response header: Server: nginx → request đã đi qua Nginx)
```

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway (nền tảng chính) — deploy agent Part 6 (`06-lab-complete/`)

- **URL:** https://agent-production-cf1245af.up.railway.app (chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md))
- **Cách làm:** `railway init` → `railway add --database redis` → tạo service `agent` với biến `AGENT_API_KEY` (secret), `REDIS_URL=${{Redis.REDIS_URL}}`, `ENVIRONMENT=production`… → `railway up` (build theo `railway.toml`: Dockerfile + healthcheck `/health`) → `railway domain`.
- **Tự kiểm CP6** (chạy lúc 2026-11-26 08:54:21 UTC; mình chạy lại và tự chụp terminal lúc 16:48 GMT+7 → `screenshots/test.png`, cùng kết quả):

```text
GET  $BASE/health                 → 200
GET  $BASE/ready                  → 200   ({"status":"ready","redis":"ok"} — Redis trên Railway)
POST $BASE/ask (không key)        → 401
POST $BASE/ask (X-API-Key đúng)   → 200
```

- `check_production_ready.py --public-url <URL>` → `✅ Public URL: GET .../health → 200`, `PASS 12/12 · 44/44 checks`.
- Screenshot: [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

### Exercise 3.2: So sánh `railway.toml` với `render.yaml` (`06-lab-complete/`)

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Cấu hình **một service** (build + deploy) | **Blueprint** mô tả cả hạ tầng: web service + Key Value (Redis) | Railway thêm Redis bằng plugin/`railway add` trên project; Render muốn tạo Redis "as code" thì phải khai báo service `keyvalue` trong cùng file |
| Build | `[build] builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `dockerfilePath`, `dockerContext`, `rootDir: 06-lab-complete` | Railway CLI `railway up` upload đúng thư mục hiện tại; Render build từ repo GitHub nên phải chỉ ra thư mục con chứa Dockerfile |
| Health check | `[deploy] healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` | Cùng ý nghĩa: platform chỉ chuyển traffic sang bản deploy mới khi `/health` 200 (zero-downtime) |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Render tự restart, không khai báo | Railway cho chỉnh policy restart, Render dùng mặc định |
| Port | Không khai báo — Railway inject `PORT`, Dockerfile nghe `${PORT:-8000}` | Không khai báo — Render inject `PORT` | Cả hai đều cấp port qua env, nên `CMD` phải đọc `PORT` |
| Biến môi trường / secret | **Không có trong file**; đặt bằng CLI (`railway add --service agent --variables ...` / `railway variables`) hoặc dashboard (`AGENT_API_KEY`, `REDIS_URL=${{Redis.REDIS_URL}}`) | `envVars` trong file: giá trị thường ghi thẳng (`ENVIRONMENT`, `RATE_LIMIT_PER_MINUTE`), secret dùng `sync: false` (nhập tay trên dashboard), `REDIS_URL` lấy bằng `fromService` (connectionString của keyvalue) | Blueprint là infrastructure-as-code nên khai được cả liên kết giữa service; secret vẫn không bao giờ ghi giá trị vào git |
| Plan | Chọn trên dashboard | `plan: free` | — |

### Exercise 3.3: Pipeline CI/CD Cloud Run (`03-cloud-deployment/production-cloud-run/`)

1. **Trigger:** push lên repo → Cloud Build chạy `cloudbuild.yaml` (máy `E2_MEDIUM`, timeout 20 phút, log ra Cloud Logging).
2. **test:** image `python:3.11-slim` cài requirements + pytest, chạy `pytest tests/` — test fail thì pipeline dừng, không build.
3. **build → push:** `docker build` gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest` (dùng `--cache-from :latest` để tận dụng layer cache), rồi `docker push --all-tags` lên Container Registry.
4. **deploy:** `gcloud run deploy ai-agent --image ...:$COMMIT_SHA` ở `asia-southeast1`, public (`--allow-unauthenticated`), 1–10 instance, 512 Mi / 1 CPU, timeout 60 s, `ENVIRONMENT=production`, secret `OPENAI_API_KEY` lấy từ **Secret Manager** (không nằm trong code/pipeline).
5. **`service.yaml`** mô tả cùng service theo kiểu Knative khai báo: `minScale 1` (tránh cold start), `maxScale 10`, `containerConcurrency 80`, requests/limits CPU–RAM, secret `AGENT_API_KEY`/`OPENAI_API_KEY` qua `secretKeyRef`, **livenessProbe `/health`** và **startupProbe `/ready`** — deploy bằng `gcloud run services replace service.yaml`. Deploy theo `$COMMIT_SHA` giúp rollback về đúng commit trước.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop`, chạy `AGENT_API_KEY=secret-key-123 python app.py`)

- **Key được check ở đâu?** Dependency `verify_api_key()` đọc header `X-API-Key` qua `APIKeyHeader(auto_error=False)`, gắn vào `/ask` bằng `Depends(verify_api_key)`.
- **Sai key thì sao?** Thiếu key → **401**; key sai → **403** (bản demo). Ở Part 6 mình trả **401 cho cả hai** theo hợp đồng của script chấm.
- **Rotate key:** key đọc từ env `AGENT_API_KEY` → đặt giá trị mới trên platform rồi restart/redeploy, không sửa code. Muốn không downtime thì cho phép tạm 2 key (cũ + mới) trong thời gian chuyển đổi rồi bỏ key cũ.

```text
### Không có key
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

### Sai key
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

### Có key
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`, tài khoản `student` / `demo123`)

Flow: `POST /auth/token` (username/password) → server ký JWT HS256 chứa `sub`, `role`, `iat`, `exp` (60 phút) → client gửi `Authorization: Bearer <token>` → `verify_token()` kiểm chữ ký + hạn, lấy ra user/role mà không cần tra DB.

```text
### POST /auth/token
HTTP/1.1 200 OK
{"access_token":"<TOKEN_REDACTED>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer <TOKEN_REDACTED>"}

### /ask không có token
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

### /ask có token
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```

### Exercise 4.3: Rate limiting

- **Algorithm:** sliding window log — mỗi user một `deque` timestamp, bỏ timestamp cũ hơn 60 s rồi đếm.
- **Limit:** user thường 10 request/phút (`rate_limiter_user`), admin 100 request/phút (`rate_limiter_admin`).
- **Bypass cho admin:** không bypass hẳn mà chọn limiter theo `role` trong JWT (`teacher` có role `admin` → limiter 100/phút).

Vòng lặp 20 request với token ở 4.2 (request "có token" ở trên đã tính là request thứ 1 trong cửa sổ):

```text
req  1 -> 200 ... "requests_remaining":8 ...
req  2 -> 200 ... "requests_remaining":7 ...
...
req  9 -> 200 ... "requests_remaining":0 ...
req 10 -> 429 {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
req 11..20 -> 429 (giống trên)

### Chi tiết response 429
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

### Exercise 4.4: Cost guard

Bản `04-api-gateway/production/cost_guard.py`: trước khi gọi LLM, `check_budget(user)` so chi phí trong ngày của user với `$1/ngày` → **402**, và tổng toàn hệ thống với `$10/ngày` → **503**; sau khi gọi, `record_usage()` cộng token × giá (gpt-4o-mini: $0.15/1M input, $0.60/1M output); cảnh báo ở 80 %. Nhược điểm: state nằm trong RAM → mất khi restart, mỗi instance đếm riêng.

Cách mình làm ở Part 6 (`06-lab-complete/app/cost_guard.py`), đưa state vào **Redis**:

- `check_budget()` (gọi **trước** LLM): `MGET cost:daily:YYYY-MM-DD cost:monthly:YYYY-MM`; đạt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` (mặc định $10/tháng) → **HTTP 402**. Redis lỗi → 503 rõ ràng chứ không âm thầm cho qua.
- `record_cost()` (gọi **sau** LLM): tính cost theo token rồi `INCRBYFLOAT` vào cả key ngày và key tháng trong một pipeline, `EXPIRE` 2 ngày / 32 ngày → tự "reset" khi sang ngày/tháng mới (key mới), không cần cron.
- Vì nằm trong Redis nên mọi worker/instance dùng chung một ngân sách, restart không mất số đã tiêu. Script chấm xác nhận: đặt budget 0.000001 USD → 402 ngay request đầu tiên (Redis của lần test đã có chi phí từ các request trước nên tổng đã vượt budget).
- Rate limit bảo vệ **tốc độ** (chống spam/DoS, 429); cost guard bảo vệ **tiền** (một user gửi chậm nhưng câu dài/model đắt vẫn có thể đốt hết budget) → cần cả hai.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

- `/health` (liveness): trả 200 khi process còn sống, kèm uptime và mức dùng RAM — **không** phụ thuộc dependency bên ngoài, vì nếu Redis chết mà liveness fail thì platform restart agent vô ích.
- `/ready` (readiness): 503 khi `_is_ready=False` (đang khởi động hoặc đang shutdown) → load balancer ngừng gửi traffic nhưng không restart.

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.7,...,"checks":{"memory":{"status":"ok","used_percent":81.4}}}   HTTP 200
$ curl http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}   HTTP 200
```

### Exercise 5.2: Graceful shutdown

App đếm `_in_flight_requests` bằng middleware; khi nhận SIGTERM uvicorn ngừng nhận kết nối mới, lifespan đặt `_is_ready=False` (readiness → 503) và chờ request đang chạy xong (tối đa 30 s) rồi mới thoát. Thử: gửi request rồi `kill -TERM` ngay lập tức (log đã bỏ bớt timestamp cho gọn):

```text
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
HTTP 200 (request in-flight lúc SIGTERM)
process exit code: 0

INFO:     Shutting down
INFO:     127.0.0.1:58172 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
INFO 🔄 Graceful shutdown initiated...
INFO ✅ Shutdown complete
INFO:     Finished server process [63679]
```

Request đang chạy vẫn trả 200, process thoát với exit code 0.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/history ở Redis (`session:{id}` + `SETEX` TTL 1 giờ, `append_to_history`) thay vì dict trong RAM. Lý do: chạy 3 instance sau load balancer thì mỗi instance có RAM riêng — request 2 rơi vào instance khác sẽ không thấy history của request 1; restart instance cũng mất state. State ở Redis thì instance nào cũng đọc được, instance trở thành "cattle" có thể thêm/bớt/tắt tuỳ ý.

### Exercise 5.4: Load balancing (`docker compose -p k5-day14-scaling up -d --build --scale agent=3`)

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 15 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 26 seconds (healthy)   6379/tcp
```

Nginx (cổng 8080) round-robin giữa 3 agent: 5 request liên tiếp ở 5.5 lần lượt được phục vụ bởi `instance-a16d89 → f295e8 → c2e8ae → a16d89 → f295e8`.

### Exercise 5.5: Test stateless (`python test_stateless.py`)

```text
(output rút gọn: bỏ dòng câu trả lời A: của từng request và nội dung từng message)
Session ID: 834c30fd-1a9d-49be-979b-8fa64af769fd
Request 1: [instance-a16d89]  Q: What is Docker?
Request 2: [instance-f295e8]  Q: Why do we need containers?
Request 3: [instance-c2e8ae]  Q: What is Kubernetes?
Request 4: [instance-a16d89]  Q: How does load balancing work?
Request 5: [instance-f295e8]  Q: What is Redis used for?
Total requests: 5
Instances used: {'instance-f295e8', 'instance-a16d89', 'instance-c2e8ae'}
✅ All requests served despite different instances!
--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu trong cùng một session đi qua cả 3 instance, nhưng history đọc lại vẫn đủ 10 message (5 user + 5 assistant) vì mọi instance ghi/đọc cùng key trong Redis, không giữ gì trong RAM.

Thêm một bước tự kiểm (script không tự làm): `docker stop k5-day14-scaling-agent-1` rồi gọi tiếp cùng session — 4/4 request vẫn **HTTP 200**, turn tiếp tục tăng (7 → 10), history vẫn đọc được từ Redis. Tắt một instance không làm mất conversation; Nginx chuyển traffic sang 2 instance còn lại.

---

## Part 6: Final Project — `06-lab-complete/`

`python check_production_ready.py` (chạy đầy đủ, có Docker, trong `06-lab-complete/`):

```text
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

Sau khi deploy, chạy lại với `--public-url https://agent-production-cf1245af.up.railway.app` → thêm `✅ Public URL: GET .../health → 200`, **PASS 12/12 · 44/44 checks** (xem `DEPLOYMENT.md`).

Một số chi tiết từ lần chạy đầu: image **56 MB**; rate limit trả 429 sau 5 request (`RATE_LIMIT_PER_MINUTE=5`), restart agent vẫn 429 (state ở Redis); Redis mất → `/ready` 503 còn `/health` 200; cost guard 402; `docker stop` thoát exit code 0 trong 0.5 s.

Xử lý lỗi (chạy bằng `docker compose up`):

```text
POST /ask không key                  → 401 {"detail":"Invalid or missing API key"}
POST /ask {"question":""}            → 422 {"error":"invalid_request","detail":"Body phải là JSON {\"question\": \"...\"} (1–2000 ký tự)","fields":["question"]}
POST /ask body không phải JSON        → 422 {"error":"invalid_request",...}
```

Lỗi không lường trước: exception handler chung trong `app/main.py` trả `500 {"error":"internal_error"}`, không trả stack trace, chỉ ghi log JSON có `exc_type` (đọc từ code; không có endpoint nào cố ý gây lỗi để test runtime).

---

## AI log

Theo RULES §1 — khai báo việc dùng AI. Công cụ: **Claude Code** (Anthropic).

**Cách làm việc:** mình là người giao việc, ra quyết định, kiểm tra và nghiệm thu; Claude Code là trợ lý thực hiện theo chỉ đạo (viết code, chạy lệnh, soạn bản nháp câu trả lời). Mọi kết quả chỉ được giữ lại sau khi mình kiểm tra.

| # | Phần | Mình giao việc / quyết định | Claude Code thực hiện | Mình kiểm tra & nghiệm thu |
|---|---|---|---|---|
| 1 | Toàn bài | Giao nhiệm vụ: đọc luật, rubric, checkpoint và hoàn thành lab; duyệt kế hoạch; chọn deploy Railway; đồng ý đổi tên repo cho đúng mẫu; quyết định không để AI tự commit | Đọc RULES/RUBRIC/CHECKPOINTS/script chấm, lập kế hoạch CP0–CP8, đổi tên repo bằng `gh repo rename` | Duyệt kế hoạch trước khi cho làm; xác nhận repo đã đúng tên mẫu, để Public |
| 2 | Part 6 code | Giao viết các TODO trong `06-lab-complete/` theo hợp đồng ở README | Viết `app/*.py` (config, auth, rate limit Redis, cost guard Redis, log JSON, lifespan), Dockerfile multi-stage, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Nghiệm thu dựa trên kết quả `check_production_ready.py` bản đầy đủ (PASS 12/12) và kết quả test 401/422/200 qua `docker compose`; đọc lại code để giải thích được |
| 3 | Part 1, 2, 4, 5 | Giao chạy các ví dụ có sẵn và soạn câu trả lời; tự `docker login` khi Docker Hub báo 429 | Chạy develop/production từng Part, thu output, soạn bản nháp câu trả lời và bảng so sánh | Mọi output dán vào bài là từ lệnh chạy trên máy mình; số MB lấy từ `docker images` |
| 4 | Part 7 Bước 1 | Giao chạy load test, eval, sửa `KNOWLEDGE_BASE` cho v2 đạt 100% | Chạy load test 4 mức + 3 mức slow rate, eval v1/v2, thêm 2 entry KB (JWT, HPA), viết job CI `eval-gate.yml` | Eval cuối 23/23, không regression; job CI chạy thử ở local exit 0 |
| 5 | Part 7 Dockerfile | Yêu cầu kiểm tra việc sửa file đề có phạm luật không; quyết định giữ bản sửa | Phát hiện stack crash `No module named 'uvicorn'`, thêm `ENV PYTHONUSERBASE=/home/agent/.local` vào `production/Dockerfile` | Đối chiếu RULES §3: file này không bị cấm sửa; stack chạy, `/ask` 200 qua Nginx; đã ghi rõ trong report |
| 6 | Benchmark scaling | Được báo lần chạy scale_1 đầu bị sai số instance; đồng ý bỏ kết quả đó và chạy lại | Phát hiện `docker compose run` tự đưa agent về `replicas: 2`, chạy lại với `--no-deps`, xác nhận số instance bằng Prometheus | Bỏ kết quả sai; chỉ giữ CSV đã xác nhận đúng 1 và 3 instance |
| 7 | Part 3 deploy | Tự tạo tài khoản và `railway login`; giao deploy agent Part 6 kèm Redis | Chạy `railway init/add/up/domain`, sinh API key ngẫu nhiên chỉ đặt trên Railway, viết `DEPLOYMENT.md` | Tự chạy lại 4 lệnh CP6 trên terminal của mình (200/200/401/200); `--public-url` → 44/44 checks |
| 8 | Ảnh chụp | Tự chụp 3 ảnh deploy; giao chụp 4 ảnh Part 7 khi stack đang chạy trên máy mình | Tạo tải và chụp tự động (headless Chrome) trang Grafana, Jaeger, Prometheus | Xem lại từng ảnh, đối chiếu số trên ảnh với report. Ảnh mình tự chụp: `dashboard.png` (Railway, tab Variables đã che giá trị), `test.png` (4 lệnh CP6), `running.png` (`railway status` + deployment SUCCESS) |
| 9 | Riêng tư trong report | Được báo `eval_*.json` chứa đường dẫn có họ tên/MSSV; **chọn chạy lại eval** với đường dẫn tương đối; yêu cầu giữ nguyên các file đề bị cấm sửa | Chạy lại 3 lần eval từ `develop/` với `--dataset eval/golden_dataset.jsonl` (v1, v2 với KB gốc; v2_fixed với KB đã sửa), cập nhật số mục 3 của report | Cả 3 file JSON là output gốc của script; v2 vẫn sai đúng `security-02`, `scaling-03`; v2_fixed 23/23; `git diff` xác nhận `check_production_ready.py` và `golden_dataset.jsonl` nguyên bản |
