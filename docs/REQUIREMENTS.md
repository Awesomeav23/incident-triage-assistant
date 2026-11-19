# Requirements

## 1. Problem

Engineering teams lose hours to noisy application logs and failed CI runs. The same
failures recur (connection-pool exhaustion, expired certs, lockfile drift, OOM kills),
but the knowledge of *how they were fixed last time* lives in old tickets, chat threads
and people's heads. On-call engineers start every investigation from zero.

## 2. Goal

Given a raw stack trace or failed CI/CD log, return in seconds:

1. a plain-English **root-cause summary**,
2. a **severity** rating,
3. a concrete **suggested fix**, and
4. the **past resolved incidents** that informed the answer,

by searching a vector database of historical incidents and passing the closest matches
to Claude (retrieval-augmented generation).

## 3. Users

| User | How they use it |
|---|---|
| On-call engineer | Pastes a log into the web UI (or `POST /triage`), reads the summary and next steps |
| CI/CD pipeline | Uploads a failed job's log to S3 → triaged automatically |
| Team lead / SRE | Adds resolved incidents (`POST /index-incident`), confirms or corrects triage results, watches metrics |

## 4. Functional requirements

Status checked line by line on 2026-11-19 against the code and tests. ✅ met · ⚠️ met with a documented limitation.

| ID | Requirement | Phase | Status |
|---|---|---|---|
| FR-1 | `POST /index-incident` stores a resolved incident (title, service, error type, log, root cause, fix, severity, tags) and its embedding | 1 | ✅ `/index-incident`; `tests/test_triage.py`, `test_rag.py` |
| FR-2 | Logs are normalised before embedding: timestamps, UUIDs, IPs, hex addresses and hashes are replaced with placeholders, so repeats of one bug embed close together | 1 | ✅ `normalize_log`; `test_triage.py` |
| FR-3 | Retrieve the top-k (default 3, max 10) most similar past incidents, each with a similarity score | 1 | ✅ `top_k` 1–10 (422 outside); `test_triage.py`, `test_rag.py` |
| FR-4 | Hybrid retrieval: vector similarity combined with Postgres full-text keyword search | 2 | ✅ hybrid RRF, hit@1 0.936 vs 0.894 vector (`results/retrieval_2026-11-17.json`) |
| FR-5 | If none of the top-k matches reaches the similarity threshold, triage reports "no close match" instead of passing them to the LLM as if relevant; if one does, all top-k are shown and the LLM judges relevance (changed in Phase 5, ADR-004b) | 2 | ✅ `test_rag.py` (threshold + full top-k); no-match logs rejected 4/6 |
| FR-6 | `POST /triage` returns root-cause summary, severity (5 levels + 1–5 score), severity reason, suggested fix, up to 5 next steps, cited incident IDs, confidence (0–1), the similar incidents, and per-step latency | 3 | ✅ all fields; ≤ 5 next steps enforced; `test_triage.py` |
| FR-7 | Claude's output is schema-validated (structured outputs); malformed or refused responses return HTTP 502 with a clear message; missing or invalid credentials return 503 | 3 | ✅ 502 / 503 paths; `test_triage.py` |
| FR-8 | Claude may only cite incidents it was shown; any other IDs are dropped | 3 | ✅ citation guard; `test_triage.py` |
| FR-9 | Obvious secrets in logs (API keys, bearer tokens, passwords in URLs/env dumps, AWS keys) are redacted before the log is sent to the LLM or stored | 3 | ✅ 12 secret shapes; `test_redact.py`, `test_history.py` |
| FR-10 | `POST /feedback` lets an engineer mark a triage correct or supply the real root cause/fix; confirmed results can be indexed as new incidents | 6 | ✅ `/feedback`; `test_history.py` |
| FR-11 | Every triage run is stored (log fingerprint, result, latency, model, feedback) | 6 | ✅ `triage_runs`; `test_history.py` (real SQL) |
| FR-12 | Duplicate suppression: a log whose normalised fingerprint was triaged within a configurable window returns the cached result plus a duplicate count, without calling the LLM | 6 | ✅ 60-min window; replay 97.6% calls avoided |
| FR-13 | `GET /stats` reports incidents indexed, triages run, duplicates suppressed, feedback accuracy | 6 | ✅ `/stats`; `test_history.py` |
| FR-14 | Uploading a log to S3 `incoming/` triggers a Lambda that calls `/triage` and writes the result to `triaged/<key>.json` | 7 | ⚠️ on Moto: handler + S3 writes tested; invoked by a script, not a real S3 notification (ADR-009) |
| FR-15 | Triage metrics (latency, severity, matches, duplicates) are published to CloudWatch; an alarm fires on critical-severity triages | 7 | ⚠️ metrics published + alarm defined on Moto; Moto doesn't evaluate alarms, so firing isn't shown |
| FR-16 | `GET /health` reports DB connectivity and incident count | 1 | ✅ `{"db": "up"}` or 503 `{"db": "down"}`; `test_triage.py` |
| FR-17 | Web UI triage page: paste a log (or pick a sample), see severity, root cause, fix, next steps, confidence, matched incidents with similarity, and latency breakdown | 4 | ✅ `ResultCard`, `TriagePage` tests |
| FR-18 | Web UI incidents page: search/filter past incidents, view details, add a resolved incident via a form | 4 | ✅ Incidents page, add-incident form tests |
| FR-19 | `GET /incidents` (paginated, text + severity filter), `GET /incidents/{id}`, `GET /samples` | 4 | ✅ `test_triage.py`, `test_rag.py` |
| FR-20 | Web UI shows loading, error (422/502 message) and "no close match" states clearly | 4 | ✅ loading / error / no-match states tested |
| FR-21 | Web UI feedback buttons, duplicate badge, and stats page | 6 | ✅ `Feedback.test.tsx`, `StatsPage.test.tsx` |
| FR-22 | Demo mode (default): triage works with no paid API calls by reusing the closest past incident's diagnosis; every demo answer is labelled as not AI-generated. Real Claude triage only with `LLM_MODE=claude` | 4 | ✅ default; `test_demo.py`; demo banner in UI |

## 5. Non-functional requirements

| ID | Requirement | Target | Status |
|---|---|---|---|
| NFR-1 | Retrieval latency (embed + search), warm | p95 < 200 ms | ✅ embed p95 29 ms, search p95 8 ms (`results/bench_2026-11-19.json`) |
| NFR-2 | End-to-end triage latency, warm | p50 < 2 s target, **measured not assumed** (dominated by the LLM call; see DECISIONS ADR-005) | ⚠️ demo p50 25 ms ✅; with Claude ~7–15 s per call (observed, not benchmarked) ❌ — reported in README |
| NFR-3 | Runs fully locally with no cloud bill: Docker Postgres, local embeddings, AWS emulator | required | ✅ Docker Postgres, local embeddings, Moto |
| NFR-4 | Test suite runs without an Anthropic API key (LLM stubbed); live tests are opt-in | required | ✅ 141 tests without a key; live test opt-in |
| NFR-5 | Log input is untrusted: prompt-injection text inside a log must not change the system's instructions | required | ✅ tag neutralisation + prompt rule; `test_triage.py` |
| NFR-6 | Max log size 50,000 chars; longer input rejected with 422 | required | ✅ 422 above 50,000; Lambda keeps the tail |
| NFR-7 | Metrics publishing never fails a triage request | required | ✅ `test_lambda.py` (API + Lambda metrics failures swallowed) |
| NFR-8 | Every number in the README comes from a script in this repo that anyone can re-run | required | ✅ every README number links to `results/`; Claude latency labelled as observed |
| NFR-9 | Web UI usable at phone width and in light and dark mode | required | ✅ checked at 390 px and in dark mode (triage/incidents Phase 4, Stats page Phase 6 + final review) |
| NFR-10 | Nothing in the default setup, tests or CI spends money: paid calls need `LLM_MODE=claude` (app) or `RUN_LIVE=1` (tests) | required | ✅ demo default; CI has no secrets; eval needs `--yes` |

## 6. Success metrics

| Metric | Target | Measured by | Result |
|---|---|---|---|
| Historical incidents indexed | ≥ 200 patterns | `GET /health` / seed output | ✅ 235 |
| Retrieval hit@3 on held-out logs | ≥ 90% | `scripts/eval_retrieval.py` | ✅ 97.9% |
| Retrieval hit@1 | ≥ 80% | `scripts/eval_retrieval.py` | ✅ 93.6% |
| Severity accuracy (exact / within ±1) | ≥ 70% / ≥ 95% | `scripts/eval_triage.py` | ✅ demo 95.7% / 97.9%; Claude not measured (cost) |
| Correct past incident cited | ≥ 85% recall | `scripts/eval_triage.py` | ✅ demo 93.6%; Claude not measured (cost) |
| Triage latency p50 / p95 | < 2 s / reported | `scripts/bench.py` | ✅ demo 25 / 39 ms; ❌ with Claude (~7–15 s observed) |
| LLM calls avoided by dedup on a noisy replay | reported | Phase 6 replay script | ✅ 97.6% (488 of 500) |

Targets are starting points. If one is missed, the README reports the real number and why.

## 7. Out of scope

- Authentication, multi-tenancy, per-team data isolation
- Streaming ingestion (Kafka/Kinesis) — S3 batch upload only
- Automatically applying fixes or opening PRs
- A production deploy to real AWS (optional extra after Phase 8)
- User accounts / login on the web UI

## 8. Constraints and assumptions

- Python 3.12 in Docker; 3.13 for the local venv (3.15 lacks wheels for the ONNX runtime)
- Ports: Postgres **5438**, Moto **4567**, API **8030**, web UI **5181** (others are taken by existing projects)
- `ANTHROPIC_API_KEY` lives in the gitignored `.env`; it is only used with `LLM_MODE=claude` (app) or `RUN_LIVE=1` (tests). Demo mode, tests and CI never need it
- The historical dataset starts with 20 hand-written incidents. The 200+ set includes synthetic variants, labelled as such
