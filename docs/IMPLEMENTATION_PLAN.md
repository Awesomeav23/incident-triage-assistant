# Implementation plan

Eight phases, about one day each. Every phase ends with: results shown → commit → push → review before the next phase.
Requirement IDs (FR/NFR) refer to [REQUIREMENTS.md](REQUIREMENTS.md); ADRs to [DECISIONS.md](DECISIONS.md).

Status: ✅ done · 🔄 in progress · ⬜ not started

---

## Phase 0 — Docs ✅
- [x] Requirements, architecture, decisions, implementation plan, eval plan
- [x] GitHub repo created (`Awesomeav23/incident-triage-assistant`)

---

## Phase 1 — Core search ✅
**Goal:** store incidents and find the closest match to a new log. **Covers:** FR-1, FR-2, FR-3, FR-16, NFR-1.

**Tasks**
- [x] `app/config.py`, `app/models.py`, `app/rag.py`, `app/main.py`
- [x] `data/sample_logs.json` (20 incidents), `data/eval_queries.json` (15 held-out logs)
- [x] `docker-compose.yml` — pgvector/pg16 on 5438 + `scripts/init_test_db.sql`
- [x] `requirements.txt` (pinned), `.gitignore`, `.env.example`, `.venv` on Python 3.13
- [x] `scripts/seed.py` — `--reset`, loads JSON via the store + embedder, prints count and time
- [x] `scripts/eval_retrieval.py` — hit@1, hit@3, MRR, embed/search p50/p95 → `results/retrieval.json`
- [x] Smoke test: `uvicorn` + `curl /health` and `/index-incident`

**Acceptance criteria**
- `docker compose up -d db` → healthy; seed loads 20 incidents
- Eval prints hit@1 / hit@3 / MRR and latency for the 14 matchable queries
- Retrieval p95 < 200 ms warm

**Risks:** fastembed/onnxruntime wheels on Python 3.13 → fall back to running inside Docker. *(Resolved: installs fine on 3.13.)*

**Result (2026-11-16):** 20 indexed, 15 queries → hit@1 1.0, hit@3 1.0, MRR 1.0; embed p95 24.8 ms, search p95 3.4 ms; mean top similarity 0.92 (matchable) vs 0.68 (no match). Too easy at 20 very different incidents; Phase 2 adds near-neighbour families to make the test meaningful.

---

## Phase 2 — Bigger dataset + hybrid search ✅
**Goal:** 200+ incident patterns and measurably better retrieval. **Covers:** FR-4, FR-5.

**Tasks**
- [x] `family` label on every incident; 27 new hand-written families in `data/families_extra.json`, deliberately close to existing ones (pool exhaustion vs too-many-clients vs statement timeout; OOMKilled vs Java heap vs Node heap vs Celery SIGKILL vs Lambda OOM; KMS deny vs bucket-policy deny; nil map vs nil pointer vs concurrent map writes …)
- [x] `scripts/expand_dataset.py` — 47 families × 5 (1 base + 4 synthetic recurrences: different service, file paths, line numbers, hosts, versions, values) → `data/incidents_expanded.json` (235 incidents, 188 flagged `synthetic: true`); deterministic seed
- [x] Eval set → `data/eval_set.json`: 53 held-out logs (47 matchable, one or more per family; 6 no-match). Held-out logs are written differently from the indexed ones (often another language or client library); a leakage check asserts none is identical to an indexed log
- [x] Schema: `family`, `synthetic`, `search_text` (normalised tokens) + generated `search_tsv` + GIN index
- [x] `IncidentStore.search(mode=vector|keyword|hybrid, min_similarity=…)` with RRF (ADR-004)
- [x] `MIN_SIMILARITY` = 0.73; `SEARCH_MODE` = hybrid; `/triage` returns `no_close_match` and `search_mode`
- [x] Eval compares all modes, lists misses, and reports the threshold curve → `results/retrieval_<date>.json`

**Result (2026-11-16)** — 235 indexed, 53 queries:

| Mode | hit@1 | hit@3 | MRR | search p95 |
|---|---|---|---|---|
| vector | 0.894 | 0.936 | 0.915 | 3.7 ms |
| keyword | 0.872 | 0.936 | 0.904 | 5.0 ms |
| **hybrid** | **0.936** | **0.957** | **0.950** | 4.7 ms |

- Embed p95 21.7 ms → retrieval total well under the 200 ms target.
- Misses (hybrid): Redis timeout logged by a Java client (rank 6), missing config in a Node app (rank 2), missing Node base image (not in top 10). All three are cross-language holdouts.
- No-match cutoff 0.73 keeps 100% of correct matches and rejects 4/6 no-match logs. The similarity ranges overlap (no-match 0.67–0.78, correct ≥ 0.73), so similarity alone can't fully separate them; Claude's "only cite genuine matches" rule is the second check, measured in Phase 5. Tuned on the eval set itself with only 6 no-match logs, so it's a starting point.

---

## Phase 3 — Claude triage + tests ✅
**Goal:** log in → root cause, severity, fix, next steps out. **Covers:** FR-6 to FR-9, NFR-4 to NFR-6.

**Tasks**
- [x] `app/llm.py` — prompt, structured output (`beta.messages.parse` → `LLMTriage`), refusal fallback, error mapping; client created lazily
- [x] `app/redact.py` — PEM keys, URL credentials, Bearer/Basic, JWTs, AWS/Anthropic/GitHub/Slack/Stripe/`sk-` keys, `password=`/`token:`-style pairs → `[REDACTED]`
- [x] Redaction wired into `/triage` (before embed, search and LLM; count returned as `redacted_secrets`) and `/index-incident` (before storing)
- [x] Prompt hardening: `<new_log>` / `<past_incidents>` tags inside a log are escaped so a log can't close the tag and inject fake incidents; explicit "no past incident" message when retrieval finds nothing
- [x] Errors: no/invalid/misconfigured credentials → **503** with a readable message; other Claude failures → 502
- [x] `tests/test_triage.py` (fakes via dependency overrides): schema, severity mapping, citation guard, no-close-match path, 502/503, 422 bounds, redaction wiring, prompt tags + injection, normalisation
- [x] `tests/test_redact.py` — 12 secret shapes masked, ordinary text untouched, zero false positives across all 235 dataset logs
- [x] `tests/test_rag.py` — real pgvector + embeddings on `triage_test`: every mode finds its incident, exact-code keyword match, cosine bounds, threshold filter, tsquery safety, idempotent schema
- [x] `tests/test_live.py` (`-m live`) — skipped without `ANTHROPIC_API_KEY`
- [x] Live test passed (2026-11-16): valid structured output, cited the matching Redis-failover incident, severity high, 5 concrete next steps. One call took ~9 s at effort `low` with Opus 5.5, well over the 2 s target; Phase 5 measures latency properly and compares models.

**Result (2026-11-16):** 51 passed, 1 skipped (live) in ~2 s, no API key or network needed.

**Bugs found by the tests and fixed**
- `normalize_log` cut long logs at 4,000 chars *after* keeping 20 head lines, so long first lines pushed the tail ("Caused by", innermost frame) out entirely. Now head and tail get separate budgets. A first attempt also capped every line at 300 chars; the retrieval eval caught that it cut "Hostname mismatch" off the end of a TLS error (hybrid hit@1 0.936 → 0.915), so the line cap now applies only to over-budget logs. Eval is back to 0.936.
- No API key → bare HTTP 500 from inside the SDK. Now a 503 that names `ANTHROPIC_API_KEY`.

---

## Phase 4 — Frontend ✅
**Goal:** anyone can paste a log in a browser and read the triage. **Covers:** FR-17 to FR-20. See ADR-012.

**Stack:** React 18 + Vite 5 + TypeScript + Tailwind 3 (exact versions pinned) in `frontend/`. Dev server on **5181**; `/api/*` proxied to the API on `127.0.0.1:8030` (same origin, so no CORS).

**Backend additions**
- [x] `GET /incidents?q=&severity=&limit=&offset=&include_synthetic=` — paginated; `q` is a case-insensitive match on title/service/error type/family with LIKE wildcards escaped
- [x] `GET /incidents/{id}` (404 when missing), `GET /samples` (7 held-out logs from `data/samples.json`, never indexed)
- [x] `/triage` returns **one incident per failure family** (`distinct_families`, ADR-013)

**Pages**
- [x] **Triage** (`/`): sample chips, log box with 50k counter, matches selector, ⌘/Ctrl+Enter, live elapsed timer; result card with severity badge, confidence meter, no-match banner, redaction notice, root cause, numbered fix steps, next steps, inline `code`; similar incidents with similarity bar, "Cited by Claude" and expandable root cause/fix; latency breakdown
- [x] **Incidents** (`/incidents`): debounced search, severity filter, "Hide synthetic copies" (on by default), pagination, detail drawer (Esc closes), add-incident form with field validation
- [x] Header with health indicator; light/dark (follows the OS); no horizontal scroll at 390 px

**Tests:** 19 Vitest/RTL tests (result card for every severity, citations, no-match, redaction, expand; triage flow, disabled state, API error text; add-incident validation + payload; error-message mapping). Backend: 60 passed, 1 live skipped. `npm run typecheck` and `npm run build` clean (60 KB gzipped JS).

**Screenshots** (`docs/screenshots/`, captured by `scripts/screenshots.py` with Playwright driving the installed Chrome, real Claude calls).

**Found while reviewing the screenshots, and fixed**
- The 3 similar incidents were often recurrences of one failure. Collapsing to one per family gives 3 different causes and raises hybrid **hit@3 from 0.957 to 0.979** (MRR 0.950 → 0.957; hit@1 unchanged at 0.936).
- Suggested fixes came back as one paragraph with inline "1) 2) 3)". The prompt now asks for one step per line with code in backticks, and the UI keeps line breaks.
- On phones, incident titles were squeezed beside the similarity bar; the bar and "cited" tag now wrap below.
- The incidents list was mostly synthetic copies; now hidden by default.
- Vite's proxy failed with ECONNREFUSED: Node resolves `localhost` to IPv6 `::1` while uvicorn listens on IPv4. The proxy targets `127.0.0.1`.

**Demo mode (added 2026-11-17, ADR-014):** `LLM_MODE=demo` is the default, so the UI, tests and CI never call Claude. Verified end to end: `/health` reports `llm_mode: demo`, a triage takes ~40 ms with 0 calls to Anthropic, and the UI labels the answer as not AI-generated (`docs/screenshots/triage-demo.png`). Backend 72 passed + 1 opt-in live test; frontend 21 passed.

**Observed, for Phase 5:** Claude took **6.7–15 s** per triage (Opus 5.5, effort low) against the 2 s target. In one run Claude didn't cite the top-ranked OOMKilled incident for a different OOM log, and did in another — citation consistency is a Phase 5 metric.

**Later phases add to the UI:** Phase 6 → feedback buttons (👍 / ✏️ correct it) and a Stats page; Phase 7 → the frontend in Docker (nginx serving the build, proxying `/api`); Phase 8 → frontend build + tests in CI.

---

## Phase 5 — Triage quality eval ✅ (free scope)
**Goal:** a measured accuracy number for Claude's answers. See [EVAL_PLAN.md](EVAL_PLAN.md) §3.

**Tasks**
- [x] Ground truth: `data/triage_truth.json`, per failure family: expected severity (= the base incident's) and 2 key root-cause facts, each with keywords for the free judge
- [x] `scripts/eval_triage.py` runs every eval log through the real `/triage` route (redaction, hybrid search, threshold, citation guard); only the triager and judge change
  - metrics: severity exact / ±1, citation recall / precision, retrieval ceiling, recall when shown, no-match honesty, root-cause score, fix actionable, confidence when right vs wrong, LLM/total latency p50/p95, tokens + actual cost
  - judges: `keyword` (free, deterministic, 0 or 2 per fact) and `claude` (rubric 0/1/2 per fact + actionable, structured output)
  - spending guard: any paid component prints an estimate and exits unless `--yes`; `--estimate-only`, `--limit N` for smoke runs, `--model` / `--judge-model` / `--effort`
  - `ClaudeTriager.last_usage` records input/output tokens per call
- [x] `tests/test_eval_triage.py` — 20 tests: answer key covers every query and matches the index's severities, keywords credit each family's real diagnosis, scoring, aggregation, cost estimate, guard refuses without `--yes` / without a key
- [x] Free demo baseline → `results/triage_eval_demo_keyword_2026-11-18.json`
- [x] Cutoff fix (below): the right incident is now shown for 46/47 logs (was 44/47)

**Decided 2026-11-18: stay free.** The project doesn't spend money, so the Claude-side numbers are **not measured (demo mode)**. These steps are ready but optional, only if credits are added later:
- [ ] Paid baseline: `python -m scripts.eval_triage --triager claude --judge claude --yes` (~$2.21; `--limit 5` smoke ~$0.22)
- [ ] Hand-check ~10 judge verdicts; one prompt-tuning pass re-run with `--tag tuned`
- [ ] Same eval with `--model claude-sonnet-5-5` for the quality/latency trade-off

**Free demo baseline (2026-11-18, keyword judge, 53 queries, after the cutoff fix)**

| Metric | Demo | Target |
|---|---|---|
| Severity exact / ±1 | 0.957 / 0.979 | ≥ 0.70 / ≥ 0.95 |
| Citation recall | 0.936 | ≥ 0.85 |
| Retrieval ceiling (right family shown at all) | 0.979 (was 0.936) | |
| Citation precision | 0.957 | |
| No-match honesty | 0.667 (4/6) | |
| Root-cause score | 0.936 | ≥ 0.80 |
| Total latency p50 | 19 ms | < 2 s |

How to read it: demo mode copies the closest past incident, so it's right exactly when the #1 result is right, and its root-cause score is partly circular (the answer key was written from those same past incidents). It's the floor a real model would have to beat, and a check that the scoring works: right answers score 1.0, wrong ones 0.

**Cutoff fix (found by this eval).** The 0.73 cutoff used to filter each result separately. Two correct matches just under it were dropped (`alembic-multiple-heads` at 0.729, `k8s-crashloop-missing-env` at 0.708) while wrong ones at 0.730–0.732 stayed. Now the cutoff only decides whether *anything* is close: if one of the top 3 clears it, all top 3 are shown; if none does, only deeper results that clear it (usually none → "no close match"). "No close match" decisions are unchanged (no-match honesty still 4/6).
- Retrieval ceiling 0.936 → **0.979**: what a real model gets to see.
- Demo recall stays 0.936: `alembic` is now right, but `redis-slow-command-timeout` is now wrong, because a weaker pg-pool incident (0.715) that the old filter hid ranks #1 above the right one (0.757). Demo can only copy #1; a model sees all three.
- Remaining gaps: `docker-base-image-missing` is a real search miss (not in the top 10); no-match honesty 4/6 needs a model to judge relevance.

**Cost estimates** (chars/4 tokens, ×1.3 margin, list prices): full run ~$2.21 (Opus triage + Opus judge), ~$1.90 with a Sonnet judge, ~$1.59 triage-only with the keyword judge, ~$0.22 for `--limit 5`.

**Acceptance criteria (free scope):** eval built and tested, demo baseline committed, Claude numbers clearly marked "not measured (demo mode)". A paid run needs `ANTHROPIC_API_KEY` **and the user's explicit go-ahead to spend.**

---

## Phase 6 — Feedback loop + noise reduction ✅
**Covers:** FR-10 to FR-13. See ADR-010.

**Backend**
- [x] Tables `triage_runs`, `feedback` (one feedback per run, `UNIQUE`); created by `PgRunStore` in `app/history.py`, sharing the incident store's connection pool
- [x] Fingerprint = SHA-256 of the **whole** redacted log with timestamps/UUIDs/IPs/addresses/hashes masked (not the embedding's head+tail cut, so long logs differing in the middle don't collide)
- [x] `/triage`: same fingerprint + same model within `DEDUP_WINDOW_MIN` (60; 0 = off) → stored result returned with `duplicate_of`, `duplicate_count`; no embed, search or model call. Every response carries `run_id`
- [x] Runs marked **wrong** are never reused: the next repeat gets a fresh answer
- [x] `POST /feedback`: 👍 / correction (root cause, fix, severity); `index: true` adds the log as a new incident (confirmed → joins the cited incident's family; corrected → new, needs root cause + fix). 404 unknown run, 409 second feedback, 422 correction without root cause/fix
- [x] `GET /stats`: requests, model runs, duplicates answered from history, feedback accuracy, incidents added from feedback, severity counts, recent runs
- [x] `scripts/eval_triage.py` uses `NullRunStore`: evals never reuse or record runs

**Frontend**
- [x] Result card: "Seen N times" banner on reused results; "Was this right?" 👍 Correct / ✏️ Correct it (form with root cause, fix, severity, "add to the incident library", optional title/service)
- [x] Stats page (`/stats`): 4 tiles, severity bars, recent triages with feedback marks; no horizontal scroll at 390 px

**Replay — `scripts/replay_noise.py`** (demo mode, free; deletes its own runs unless `--keep`)

| Logs | Distinct failures | Model calls | Avoided | Reduction |
|---|---|---|---|---|
| 500 | 12 | 12 | 488 | **97.6%** |

Each log is one of 12 real eval failures with fresh timestamps, request IDs, IPs and addresses, skewed so a few failures dominate (like a crash loop). This is the best case for exact-fingerprint dedup: logs that differ in other ways (pod names, line numbers) aren't merged here and go through retrieval instead. If Claude were on, the 488 avoided calls would be ~73 min of model time and ~$15 (estimate: 9 s and $0.03 per call from Phase 3, not measured here) → `results/replay_noise_2026-11-19.json`.

**Tests:** backend +24 (fingerprint, guesses, dedup, window, wrong-not-reused, secrets masked before fingerprinting, feedback paths, stats, real SQL on `triage_test` incl. window expiry) → 117 passed, 1 live skipped. Frontend +11 → 32 passed. Typecheck + build clean. Checked in Chrome: repeat triage shows "Seen 2 times", 👍 saves, Stats updates (`docs/screenshots/duplicate.png`, `stats.png`).

**Found while building:** the first `/triage` change made the existing API tests write runs into the real database (the run store wasn't overridden). Caught by a failing test, the 2 rows removed, and `fakes` now overrides the run store so tests can't touch it.

---

## Phase 7 — Cloud workflow ✅
**Covers:** FR-14, FR-15, NFR-7. See ADR-009, ADR-015, ADR-016. Everything runs on the local AWS emulator (Moto): free, no AWS account.

**Tasks**
- [x] `lambda_fn/handler.py` — S3 `ObjectCreated` → read `incoming/<path>` → POST `/triage` → `triaged/<path>.json` (source, time, truncated flag, full result) or `failed/<path>.json` (error + HTTP status). boto3 + stdlib only, so it deploys as one file
  - URL-encoded keys decoded; bad bytes replaced; logs over 50k chars keep the **tail** (where errors are) and are marked `truncated`; empty logs fail without calling the API
  - retries once on unreachable / 502–504; never on 4xx
  - ignores anything outside `incoming/`, so its own writes can't re-trigger it
  - metrics `IngestSucceeded`, `IngestFailed`, `IngestDuplicate`; a metrics failure never fails the invocation
- [x] API metrics add `DuplicateSuppressed`; reused results publish metrics too (LLM latency 0)
- [x] `scripts/simulate_s3_ingest.py` — creates the bucket + alarm `critical-severity-triage` (`SeverityScore` max ≥ 5, idempotent), uploads sample logs to `incoming/ci/<timestamp>/`, calls the handler with a real-shaped S3 event per upload, lists `triaged/` / `failed/` and the CloudWatch metrics
- [x] Moto in docker-compose (`motoserver/moto:5.1.20`, port 4567)
- [x] `Dockerfile` (python:3.12-slim, bge-small baked in, non-root user; `.dockerignore` keeps `.env`, tests, docs out — checked: no `.env` in the image)
- [x] `frontend/Dockerfile` (Node 20.18.1 build → nginx 1.27.3) + `nginx.conf` (`/api/` → `api:8030`, SPA fallback)
- [x] Compose `app` profile: `api` (demo mode by default, metrics → Moto with dummy credentials) and `web`; ports overridable with `API_PORT` / `WEB_PORT`
- [x] `requirements-dev.txt` (pytest, moto) split from the runtime `requirements.txt`
- [x] `tests/test_lambda.py` on Moto's in-memory AWS (`mock_aws`) — 19 tests: key mapping, tail truncation, triaged/failed writes, retry rules, empty log, own outputs ignored, non-create events ignored, ingest + API metrics, metrics failures swallowed, setup idempotent. Plus 1 in `test_history.py` (reused result publishes `DuplicateSuppressed`)

**Result (2026-11-19, demo mode, free)**
- Local API + Moto: 4 uploads (1 repeated) → 4 files in `triaged/`, 0 in `failed/`; the repeat was answered from history
- Containers (`docker compose --profile app up`, on 8031/5182): 7 sample uploads → 7 in `triaged/`, 0 failed; web UI triage and `/stats` deep link work through nginx
- CloudWatch on Moto: `TriageLatencyMs`, `LLMLatencyMs`, `SeverityScore`, `MatchedIncidents`, `DuplicateSuppressed`, `IngestSucceeded`, `IngestDuplicate`; alarm defined
- Tests: backend 137 passed, 1 live skipped; frontend 32

**Limits (honest):** Moto stores the alarm but doesn't evaluate it, and doesn't run the Lambda from an S3 notification, so the script invokes the handler directly (ADR-009). A real-AWS deploy (S3 notification filtered on `incoming/`, function URL or VPC access to the API) is optional and not done: it would need an AWS account.

**Acceptance criteria:** ✅ uploading 3+ logs yields the same number of result files in `triaged/`; metrics listed via `list_metrics` on Moto.

---

## Phase 8 — CI + publish ⬜

**Tasks**
- [ ] `.github/workflows/ci.yml` — ruff, pytest, pgvector service container, seed + retrieval eval; fail if hit@3 < threshold
- [ ] CI job for the frontend: `npm ci`, typecheck, `npm test`, `npm run build`
- [ ] `scripts/bench.py` — N warm triages → p50/p95 for embed / search / LLM / total → `results/bench.json`
- [ ] README: screenshots, what it does, architecture diagram, quick start, API examples, measured results table, limitations
- [ ] Final review of docs vs what was built; update ROADMAP to ✅

**Acceptance criteria:** CI green on GitHub; every README number traceable to a file in `results/`.

---

## Optional extras (after Phase 8)
- Real AWS deploy (S3 notification → Lambda, API on ECS/App Runner)
- `LLM_PROVIDER=bedrock` switch
