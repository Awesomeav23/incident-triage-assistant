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

## Phase 5 — Triage quality eval ⬜
**Goal:** a measured accuracy number for Claude's answers. See [EVAL_PLAN.md](EVAL_PLAN.md) §3.

**Tasks**
- [ ] Ground truth per eval query: expected incident, severity, key root-cause facts
- [ ] `scripts/eval_triage.py` — severity exact / ±1, citation precision/recall, rubric-graded root cause (LLM judge), latency, tokens/cost per triage
- [ ] Results → `results/triage_eval_<model>_<date>.json` + summary table
- [ ] One prompt-tuning pass, re-run, record before/after
- [ ] Optional: same eval on a second model via `CLAUDE_MODEL` to show the quality/latency trade-off

**Acceptance criteria:** a baseline table committed; README draft cites it.
**Needs:** `ANTHROPIC_API_KEY` **and the user's explicit go-ahead to spend** (~$3–4 per Opus run). The script, ground truth and rubric are built and tested with fakes first; the paid run waits for approval. Until then the README marks these numbers as pending.

---

## Phase 6 — Feedback loop + noise reduction ⬜
**Covers:** FR-10 to FR-13.

**Tasks**
- [ ] Tables `triage_runs`, `feedback` (ARCHITECTURE §5)
- [ ] Fingerprint = SHA-256 of the normalised, redacted log; cached hit within `DEDUP_WINDOW_MIN` → return the stored result + `duplicate_of`, `duplicate_count`
- [ ] `POST /feedback`; when `index: true`, create an incident from the confirmed/corrected triage
- [ ] `GET /stats`
- [ ] Frontend: feedback buttons on the result card (👍 correct / ✏️ correct it → form), duplicate badge ("seen 37 times"), Stats page
- [ ] `scripts/replay_noise.py` — replay a burst (e.g. 500 logs from 12 distinct failures with varying timestamps/IDs) → LLM calls made vs avoided
- [ ] Tests for dedup window, feedback → indexing

**Acceptance criteria:** the replay reports calls avoided; a corrected triage is retrievable on the next similar log.

---

## Phase 7 — Cloud workflow ⬜
**Covers:** FR-14, FR-15, NFR-7.

**Tasks**
- [ ] `Dockerfile` (python:3.12-slim, embedding model baked in); `api` service in compose on 8030
- [ ] `frontend/Dockerfile` (multi-stage: Node build → nginx); `web` service on 5181 proxying `/api` to `api`
- [ ] Moto service on 4567
- [ ] `lambda_fn/handler.py` — S3 event → read object → POST `/triage` → `triaged/` or `failed/`; stdlib `urllib` for HTTP
- [ ] `scripts/simulate_s3_ingest.py` — create bucket, upload sample logs, invoke handler with S3-shaped events, print results
- [ ] CloudWatch: metrics from API + Lambda; `put_metric_alarm` on `SeverityScore >= 5`
- [ ] `tests/test_lambda.py` with Moto (`mock_aws`)

**Acceptance criteria:** uploading 3 logs yields 3 result files in `triaged/`; metrics listed via `list_metrics` on Moto.

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
