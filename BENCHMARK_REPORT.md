# Benchmark & Observability Report — Day 14

**Họ tên:** Nguyễn Văn Minh — 2A202603771
**Ngày chạy:** 2026-11-26 (các mốc giờ trong báo cáo ghi theo UTC, `Z`)
**Môi trường:** MacBook Apple M3 Pro (11 CPU, 18 GB RAM), Docker Desktop 29.6.2 (11 CPU / 7.75 GB cấp cho VM), Python 3.12. Bước 1 chạy agent trực tiếp bằng `python app.py`; Bước 2 chạy stack Docker Compose (mỗi agent giới hạn `cpus: 0.5`).

Nguồn số liệu: `reports/*.json` (output của `load_test.py`, `run_eval.py`; 3 file eval chạy từ thư mục `develop/` với `--dataset eval/golden_dataset.jsonl`), `benchmark-results/scale_{1,3}_stats.csv` (Locust headless), Prometheus API `/api/v1/alerts` + PromQL, Jaeger API v3.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500 ms** (load test) · alert khi > 1 s trong 2 phút | Ở tải bình thường p95 đo được ~220 ms (mock LLM 120 ms + jitter + retrieval), 500 ms chừa biên ~2× cho LLM thật dao động. Đặt theo p95 vì average che tail latency (mục 2.2). Ngưỡng alert 1 s rộng hơn để không báo động giả |
| Error rate `/ask` | < **2 %** (load test) · alert khi > 5 % trong 1 phút | Mock có ~1 % lỗi upstream "tự nhiên" (giống provider trả 429/503). 2 % chừa biên cho lỗi đó; 5 % là mức chắc chắn có sự cố |
| Accuracy trên golden dataset | **100 %** (23/23) và **không regression** so với baseline | Dataset nhỏ, chấm theo keyword, đã đạt 23/23 sau khi sửa KB → mọi câu tụt là regression thật cần chặn merge |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency $c --out reports/load_v2_c$c.json` (SLO mặc định của script: p95 ≤ 500 ms, error ≤ 2 %).

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.25 | 191.6 | 220.6 | 1696.2 | 0.67 % (2× 503) | ✅ PASS |
| 10 | 34.86 | 193.6 | 221.8 | 1706.8 | 2.67 % (8× 503) | ❌ FAIL (error rate) |
| 50 | 130.98 | 191.7 | 247.0 | 1688.6 | 1.33 % (4× 503) | ✅ PASS |
| 100 | 72.45 | 273.0 | 2171.5 | 2967.3 | 1.33 % (4× 503) | ❌ FAIL (p95) |

**Nhận xét:** RPS **không tăng tuyến tính** suốt dải. Từ 1 → 10 gần tuyến tính (4.25 → 34.86, ≈ 8.2× khi concurrency tăng 10×) vì mỗi request chủ yếu là `await asyncio.sleep` của mock LLM — event loop xử lý song song nhiều request đang chờ I/O, p50 gần như không đổi (~192 ms). Ở 50, RPS 130.98 chỉ bằng ~50 % mức lý tưởng (50 / 0.19 s ≈ 263 req/s) và p95 bắt đầu nhích lên (247 ms) — đã có hàng đợi. Ở 100, RPS **giảm** còn 72.45, p50 273 ms, p95 2171.5 ms. Giải thích khả dĩ: agent chạy **một process Python**, phần CPU (parse JSON, metrics, tracing, log) của 100 request đồng thời xếp hàng trên event loop nên request chờ trước khi được xử lý; với chỉ 300 request (3 "lượt" 100 request), mỗi lượt bị kéo dài bởi request chậm nhất nên throughput tụt. Kết luận: một instance bão hoà khoảng 50–100 request đồng thời; muốn hơn phải scale ngang. Lỗi 503 rải rác ở mọi mức là lỗi upstream mô phỏng (`MOCK_ERROR_RATE=0.01`), không phụ thuộc tải — riêng lần c=10 rơi vào 8/300 nên vượt SLO 2 %, cho thấy với mẫu nhỏ error rate dao động mạnh.

### 2.2 Tail latency

Lệnh: restart v2 với `MOCK_SLOW_RATE=0 | 0.03 | 0.1`, mỗi lần `--requests 300 --concurrency 20 --out reports/load_v2_slow_<rate>.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 189.5 | 216.8 | 220.4 |
| 0.03 (mặc định) | 239.8 | 221.7 | 1693.9 |
| 0.1 | 320.3 | 1688.8 | 1713.5 |

(p50 lần lượt 190.0 / 188.8 / 192.0 ms; max 233.4 / 1725.2 / 1726.2 ms.)

**Nhận xét:** Chỉ số nhạy nhất là **percentile đuôi**: với 3 % request chậm, avg chỉ tăng 50 ms (189.5 → 239.8) nhưng **p99 nhảy từ 220 → 1694 ms** (gấp ~7.7 lần); với 10 % request chậm (> 5 %), **p95 cũng nhảy lên 1689 ms** trong khi avg mới tăng 131 ms so với 0. p50 gần như không đổi ở cả 3 mức. Vì vậy SLO **không nên đặt theo average**: avg 320 ms trông vẫn "ổn" trong khi cứ 10 user có 1 người chờ ~1.7 s. Percentile nói trực tiếp "X % user được phục vụ nhanh hơn ngưỡng", nên p95/p99 mới phản ánh trải nghiệm của nhóm user bị chậm.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.35 % (1/23) | 91.3 % (21/23) | **100 % (23/23)** |
| Avg tokens / câu | 57.6 | 108.7 | 117.2 |
| Tổng cost (USD) | 0.000411 | 0.000756 | 0.000821 |
| Cost / câu đúng (USD) | 0.00041055 | 0.00003599 | 0.0000357 |
| Latency p50 / p95 (ms) | 168.4 / 186.8 | 191.5 / 215.2 | 198.0 / 221.1 |
| Quality gate | **FAIL** (accuracy < 80 %) | **PASS** (≥ 80 %, 0 regression so với v1) | **PASS** (`--min-accuracy 1.0`, 0 regression so với v2) |

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Không có tài liệu nào khớp nên v2 trả lời chung chung. Thêm entry KB với trigger `jwt`, `api key`, `json web token`, nội dung: API key là chuỗi bí mật cố định, không tự hết hạn; JWT là **token** tự chứa thông tin user kèm thời điểm **hết hạn** (exp) và **chữ ký** số, server chỉ verify chữ ký |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry KB với trigger `hpa`, `kubernetes`, `autoscaler`, `autoscaling`: HPA tăng/giảm pod theo **CPU** utilization, **memory** usage hoặc **custom metrics** (req/s, queue length qua Prometheus Adapter) |

Trigger được chọn hẹp (không dùng từ rộng như `token`, `scale`) để không "cướp" câu khác — retrieval chọn top-1 theo số keyword trùng. Lệnh cuối `python eval/run_eval.py --url http://localhost:8002 --dataset eval/golden_dataset.jsonl --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json` → `Accuracy 100.0% (23/23)`, `Câu được cải thiện: 2`, `Câu bị regression: 0`, `Quality gate ... ✅ PASS`, exit code 0. Phần KB đã sửa được chép sang `production/utils/mock_llm.py`; eval qua Nginx của stack production cũng đạt 23/23.

**Quality gate trong CI:** `run_eval.py` trả **exit code 1** khi accuracy < `--min-accuracy` hoặc có regression so với `--baseline`. Thử chạy v1 với `--baseline reports/eval_v2.json` → in `Câu bị regression : 20`, `Quality gate ... ❌ FAIL`, **exit=1**. Job `.github/workflows/eval-gate.yml` khởi động agent v2 rồi chạy `run_eval.py --min-accuracy 1.0 --baseline reports/eval_v2_fixed.json`; bước nào trả exit ≠ 0 thì job fail, và khi bật *Require status checks to pass* cho job `eval-gate` trong branch protection của `main` thì PR không merge được. Job đặt `MOCK_ERROR_RATE=0` vì gate đo **chất lượng câu trả lời**, không để 1 % lỗi upstream ngẫu nhiên của mock làm CI fail chập chờn.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:** v2 thêm bước retrieval nên prompt dài hơn → **~2× token mỗi câu** (57.6 → 108.7) và **~1.8× tổng cost** (0.000411 → 0.000756), latency p95 tăng ~28 ms (186.8 → 215.2). Đổi lại accuracy tăng từ 4.35 % lên 91.3 %, nên **cost trên mỗi câu đúng rẻ hơn ~11.4 lần** (0.00041055 → 0.00003599 USD). Đó là lý do so sánh bằng "cost / câu đúng" chứ không phải "cost / câu": v1 rẻ trên mỗi request nhưng gần như mọi câu trả lời đều vô dụng. Sau khi sửa KB, cost / câu đúng còn giảm nhẹ nữa (0.0000357) dù tokens tăng nhẹ.

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build` (Nginx + agent + Prometheus + Grafana + Jaeger + Locust). Ghi chú: `production/Dockerfile` có sẵn bị lỗi `ModuleNotFoundError: No module named 'uvicorn'` (user `agent` có HOME=`/app` nên Python không thấy `/home/agent/.local`), đã sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải — [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png): Traffic 26.7 req/s, Error rate 1.36 %, Latency p95 292 ms, Instances up 1 (đang chạy scale_1), panel "Requests/s theo instance" thấy giai đoạn 3 instance (scale_3, ~20 req/s mỗi instance, các đường chồng lên nhau) rồi về 1 instance. Đợt tải đầu tiên trên panel (~30 req/s mỗi instance) là lần chạy scale_1 bị sai số instance đã bỏ (mục 5).
- [x] Một trace chậm trong Jaeger — [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`) — [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với alert đang **firing** — [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png): **3 alert cùng FIRING** — `AgentHighErrorRate` (A), `AgentInstanceDown` (D), `LLMCostBurnRateHigh` (còn từ benchmark 100 user)

**Exercise 7.4 — đọc dashboard:**
- **avg ↔ p95/p99:** trên panel Latency, p50/avg nằm ~200–250 ms còn p99 nằm ~1.7–1.9 s suốt thời gian có tải. Khoảng cách đó chính là tail latency do ~3 % request chậm của mock; avg bị kéo lên ít vì 97 % request nhanh.
- **Input token > output token:** panel Tokens/s cho input cao hơn output (đo bằng PromQL: v2 ≈ **79.5 input / 42.3 output** token mỗi LLM call). Prompt = system prompt + **tài liệu retrieval** + câu hỏi, còn câu trả lời chỉ là một đoạn ngắn → input luôn dài hơn; với giá output đắt hơn 4× thì vẫn cần theo dõi cả hai.
- **`/metrics` bị chặn ở Nginx:** `curl http://localhost:8080/metrics` → **404**. `/metrics` lộ thông tin nội bộ (số request, cost, version, instance) và không cần cho user; Prometheus scrape thẳng từng agent trong network nội bộ (DNS service discovery), không đi qua Nginx — đi qua LB thì mỗi lần scrape trúng một instance ngẫu nhiên, số liệu sẽ nhảy lung tung.

### 4.2 Thí nghiệm "phá hệ thống"

Tải nền: Locust headless 20 user chạy suốt thí nghiệm (`--no-deps`), 2 agent. Thời điểm đổi cấu hình ghi bằng `date -u`; thời điểm firing lấy từ script theo dõi `/api/v1/alerts` của Prometheus (poll 3 s). Bằng chứng gốc: [`reports/chaos/chaos_timeline.txt`](reports/chaos/chaos_timeline.txt) (mốc đổi cấu hình + số PromQL), [`reports/chaos/alert_state_changes.log`](reports/chaos/alert_state_changes.log) (mọi lần alert đổi trạng thái, kèm `activeAt`), [`reports/chaos/trace_log_lines.jsonl`](reports/chaos/trace_log_lines.jsonl) (log của 2 trace ở mục 4.3), [`reports/chaos/promtool_test_rules.txt`](reports/chaos/promtool_test_rules.txt).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` (08:33:27Z) | `AgentHighErrorRate` — pending 08:33:42Z, **firing 08:34:44Z** | **77 s** (≈ 15 s cho rate 1m vượt 5 % + `for: 1m`) | Error rate (đỏ), Requests/s theo status code (503 tăng), LLM calls theo kết quả (error) |
| B | `MOCK_SLOW_RATE=0.3` (08:37:20Z) | `AgentHighLatencyP95` — pending 08:37:37Z, **firing 08:39:39Z** | **139 s** (`for: 2m`) | Latency p50/p95/p99: PromQL lúc 08:39:50Z cho **avg 0.593 s, p50 0.216 s, p95 1.906 s, p99 1.981 s** — p95 gấp ~9 lần mức ~0.2 s lúc bình thường, còn p50 gần như không đổi |
| C | `LLM_MODEL=mock-llm-large` (08:39:52Z) | `LLMCostBurnRateHigh` — pending 08:40:17Z, **firing 08:42:18Z** | **146 s** (`for: 2m`) | Cost burn rate (USD/giờ): **$1.24/h** (model small, rate 5m, đo 08:39:50Z) → **$23.27/h** (model large, rate 1m, đo sau khi firing) — cùng tải 20 user, tăng ~19× (hai số dùng cửa sổ rate khác nhau nên chỉ là ước lượng; bảng giá large đắt ~16× trên mỗi token); LLM latency p95 0.495 s |
| D | `docker pause k5-day14-observability-agent-1` (08:33:34Z, chồng lên A) | `AgentInstanceDown` (instance 172.24.0.8:8000) — pending 08:33:42Z, **firing 08:34:14Z** | **40 s** (scrape fail + `for: 30s`) | Instances up giảm 2 → 1. Nginx log ghi **7 lỗi 504** trong lúc pause, nhưng metric `http_requests_total` của agent chỉ có status 200/503, **không có 504** |
| E | `AGENT_VERSION=v1` (08:42:28Z) | **Không có** — `/api/v1/alerts` trả `[]` lúc 08:47:21Z (alert cost còn sót của C tự resolve lúc 08:47:19Z) | — | Bảng "Phiên bản đang chạy" đổi sang v1 (×2); Retrieval hit ratio mất (v2: 10.2 hit/s → v1: không có retrieval); token mỗi call giảm (79.5/42.3 → 36.4/19.9 input/output). Error rate 1 %, p95 0.198 s — mọi thứ "xanh" |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 là sự cố **chất lượng**: agent vẫn trả 200 nhanh, rẻ hơn, nên mọi metric hạ tầng (error rate, latency, instance up) đều đẹp hơn — trong khi accuracy rơi từ 100 % xuống 4.35 % (mục 3). Cách phát hiện:
1. **Chặn trước khi deploy:** quality gate eval trong CI/CD (job `eval-gate`) — v1 với baseline v2 trả exit 1, không cho merge/deploy.
2. **Giám sát chất lượng khi chạy:** chạy eval định kỳ (synthetic/canary eval trên golden dataset) với production và alert khi accuracy giảm; alert theo **proxy metric** như retrieval hit ratio tụt về 0, token/request giảm đột ngột, hoặc `agent_info{version}` đổi ngoài lịch deploy.
3. **Tín hiệu từ user:** feedback 👍/👎, tỉ lệ hỏi lại, LLM-as-judge chấm mẫu ngẫu nhiên các câu trả lời thật.

Thí nghiệm D cho thấy thêm một điểm mù: lỗi **504 xảy ra ở Nginx** (agent bị pause, Nginx chờ hết `proxy_read_timeout 10s`) — request chưa bao giờ được agent xử lý xong nên metric của agent không đếm, panel Error rate của agent vẫn xanh. Cần giám sát ở tầng load balancer (metric/log Nginx) hoặc synthetic check từ bên ngoài.

### 4.3 Debug bằng trace

Trace chậm nhất trong khoảng chạy benchmark (lấy từ Jaeger API, sắp theo duration):

- `trace_id`: `3c32f4c2d30895d91cec6ecfc35c805b`
- Tổng thời gian: **2213.6 ms** (`POST /ask`, HTTP 200)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (2184.3 ms, 98.7 %)**, `agent.retrieve_context` chỉ 25.6 ms. Attributes: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=81`, `gen_ai.usage.output_tokens=46` → nút thắt là LLM (request "slow" của mock), không phải code của agent.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 3c32f4c2d30895d91cec6ecfc35c805b`):

```json
{"ts": "2026-11-26T08:24:47.911515+00:00", "level": "INFO", "service": "observable-agent", "instance": "417d3e73d58b", "event": "llm_call", "trace_id": "3c32f4c2d30895d91cec6ecfc35c805b", "span_id": "1332c4fc350d6ecf", "request_id": "9c08c8034cdb", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 81, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1638.1}
{"ts": "2026-11-26T08:24:47.913140+00:00", "level": "INFO", "service": "observable-agent", "instance": "417d3e73d58b", "event": "http_request", "trace_id": "3c32f4c2d30895d91cec6ecfc35c805b", "span_id": "1332c4fc350d6ecf", "request_id": "9c08c8034cdb", "method": "POST", "route": "/ask", "status": 200, "ms": 2212.6}
```

Log ghi `llm_ms: 1638.1` — đây là latency mà mock **định ra** (`result.latency_s`), còn span `chat` đo thời gian thật 2184.3 ms. Chênh ~550 ms nhiều khả năng là thời gian chờ event loop/CPU của container (giới hạn `cpus: 0.5`) lúc 100 user — trace đo thời gian thật nên thấy được phần chờ này. (Trace này thuộc lần chạy 100 user đầu tiên — lần scale_1 bị sai số instance ở mục 5; trace vẫn là request thật.)

Trace lỗi: `trace_id` `197f218fd013dc5c9afb5a2613be6eeb`, `POST /ask` **503** (862.4 ms), span `agent.run` và `chat mock-llm-small` có status error và event **`exception`** (`LLMError: Upstream LLM error: 503 Service Unavailable (mock)`). Log cùng trace_id:

```json
{"ts": "2026-11-26T08:27:25.071463+00:00", "level": "INFO", "service": "observable-agent", "instance": "417d3e73d58b", "event": "llm_error", "trace_id": "197f218fd013dc5c9afb5a2613be6eeb", "span_id": "d580ed19348c4b4a", "request_id": "21ce3b4ae093", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

Alert rules cũng được test bằng promtool: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh (CP8): `docker compose -p k5-day14-observability run --rm --no-deps locust ... --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_N`. Số liệu từ dòng `Aggregated` (cột `/ask` trong ngoặc) của `benchmark-results/scale_N_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 65.67 (`/ask` 59.39) | 190 | 230 | 1700 | 90 / 7840 (1.15 %), toàn bộ là 503 upstream |
| 3 | 66.06 (`/ask` 60.04) | 190 | 220 (`/ask` 230) | 1700 | 70 / 7881 (0.89 %), toàn bộ là 503 upstream |

**Lưu ý phương pháp:** lần đầu chạy `docker compose run` **không có `--no-deps`**, Compose tự đưa service agent về `replicas: 2` khai báo trong compose nên "scale_1" thực tế chạy 2 instance (Prometheus `count(up{job="agent"}==1)` = 2). Kết quả đó đã bỏ; cả hai lần trong bảng chạy với `--no-deps` và đã kiểm tra bằng Prometheus: suốt lần scale_1 có đúng **1** instance up, scale_3 có đúng **3**.

**Giải thích kết quả:** Scale 1 → 3 instance **không cải thiện** RPS (65.67 → 66.06) hay p95 (230 → 220/230 ms). Nút thắt không nằm ở agent:
1. **Bản thân bài test là closed-loop:** 100 user, mỗi user gửi xong lại `wait_time = between(0.5, 2)` (trung bình 1.25 s) → trần lý thuyết ≈ 100 / (1.25 + 0.22) ≈ 68 req/s, đúng bằng số đo. Muốn thấy lợi ích scale phải tăng số user hoặc giảm wait time.
2. **Agent chưa bão hoà:** ở 1 instance, CPU agent tối đa chỉ **0.20 core** (PromQL `rate(process_cpu_seconds_total)`) trên giới hạn 0.5; latency chủ yếu là thời gian chờ mock LLM (`await asyncio.sleep`), thêm instance không làm LLM nhanh hơn — panel LLM latency p95 giữ ~200 ms, p99 ~1.7 s ở cả hai lần (tail do `MOCK_SLOW_RATE`, không do tải).
3. Scale vẫn có giá trị về **độ sẵn sàng** (thí nghiệm D: instance còn lại vẫn phục vụ khi một instance treo, dù vẫn có 7 lỗi 504 trước khi Nginx tránh được instance treo) và khi tải thật vượt sức một instance (như concurrency 100 ở mục 2.1).

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối:** 3 % request chậm làm p99 tăng 7.7× trong khi avg chỉ tăng 27 % — SLO và alert phải dùng percentile.
  2. **Infra xanh ≠ agent đúng:** đổi sang v1 làm accuracy rơi từ 100 % xuống 4 % mà không alert nào kêu; chất lượng phải được đo bằng eval (CI gate + eval định kỳ). Tương tự, lỗi 504 ở Nginx không hiện trên metric agent — mỗi tầng chỉ thấy những gì nó xử lý.
  3. **Đo phải kiểm chứng cách đo:** `docker compose run` âm thầm scale agent về 2 làm sai benchmark scale_1; closed-loop load test có trần RPS riêng. Trace/log có chung `trace_id` giúp đi từ "p99 cao" tới đúng request, đúng span (LLM chiếm 98.7 %).
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** eval định kỳ trên production + alert khi accuracy < SLO; alert retrieval hit ratio giảm mạnh; theo dõi feedback 👍/👎.
  - **Tầng biên:** metric/alert từ Nginx/LB (tỉ lệ 5xx/504, upstream response time) và synthetic check từ bên ngoài (blackbox exporter).
  - **SLO dạng burn-rate:** alert error budget burn rate nhiều cửa sổ (5m/1h) thay vì ngưỡng tĩnh, để vừa nhanh vừa ít báo động giả.
  - **Chi phí:** budget theo ngày/tháng (cost guard 402 như Part 6) + alert khi dùng 80 % ngân sách; cost/câu đúng theo version.
  - **Saturation:** CPU/memory container, độ dài hàng đợi/in-flight, rate limit 429 từ provider LLM, Redis latency.
