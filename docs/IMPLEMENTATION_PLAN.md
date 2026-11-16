# Implementation plan

Seven phases, about one day each. Every phase ends with: results shown → commit → push → review before the next phase.
Requirement IDs (FR/NFR) refer to [REQUIREMENTS.md](REQUIREMENTS.md); ADRs to [DECISIONS.md](DECISIONS.md).

Status: ✅ done · 🔄 in progress · ⬜ not started

---

## Phase 0 — Docs ✅
- [x] Requirements, architecture, decisions, implementation plan, eval plan
- [x] GitHub repo created (`Awesomeav23/incident-triage-assistant`)

---

## Phase 1 — Core search 🔄
**Goal:** store incidents and find the closest match to a new log. **Covers:** FR-1, FR-2, FR-3, FR-16, NFR-1.

**Tasks**
- [x] `app/config.py`, `app/models.py`, `app/rag.py`, `app/main.py` (written, not run)
- [x] `data/sample_logs.json` (20 incidents), `data/eval_queries.json` (15 held-out logs)
- [ ] `docker-compose.yml` — pgvector/pg16 on 5438 + `scripts/init_test_db.sql`
- [ ] `requirements.txt` (pinned), `.gitignore`, `.env.example`, `.venv` on Python 3.13
- [ ] `scripts/seed.py` — `--reset`, loads JSON via the store + embedder, prints count and time
- [ ] `scripts/eval_retrieval.py` — hit@1, hit@3, MRR, embed/search p50/p95 → `results/retrieval.json`
- [ ] Smoke test: `uvicorn` + `curl /health` and `/index-incident`

**Acceptance criteria**
- `docker compose up -d db` → healthy; seed loads 20 incidents
- Eval prints hit@1 / hit@3 / MRR and latency for the 14 matchable queries
- Retrieval p95 < 200 ms warm

**Risks:** fastembed/onnxruntime wheels on Python 3.13 → fall back to running inside Docker.

---

## Phase 2 — Bigger dataset + hybrid search ⬜
**Goal:** 200+ incident patterns and measurably better retrieval. **Covers:** FR-4, FR-5.

**Tasks**
- [ ] `scripts/expand_dataset.py` — ~40 error families × variants (different services, frameworks, line numbers, values) → `data/incidents_expanded.json`; each record tagged `synthetic: true`
- [ ] Families added: Python, Java/Spring, Go, Node/TS, Kubernetes, Docker, Terraform, GitHub Actions, AWS (IAM/S3/Lambda/DynamoDB), Postgres/MySQL/Redis/Kafka, TLS/DNS/network
- [ ] Expand the eval set to ~50 held-out logs (families present + 5–8 "no match")
- [ ] Schema: generated `search_tsv` column + GIN index
- [ ] `IncidentStore.search(mode=vector|keyword|hybrid)` with RRF (ADR-004)
- [ ] `MIN_SIMILARITY` threshold, tuned on the eval set
- [ ] Eval `--mode` flag → comparison table in `results/retrieval_modes.json`

**Acceptance criteria**
- ≥ 200 incidents indexed
- Table of hit@1 / hit@3 / MRR / no-match precision for all three modes
- Default mode = best measured; decision noted in ADR-004

---

## Phase 3 — Claude triage + tests 🔄
**Goal:** log in → root cause, severity, fix, next steps out. **Covers:** FR-6 to FR-9, NFR-4 to NFR-6.

**Tasks**
- [x] `app/llm.py` (prompt, structured output, refusal/error handling), `/triage` route (written, not run)
- [ ] `app/redact.py` — AWS keys, bearer/JWT tokens, `password=`, URLs with credentials, private keys → `[REDACTED]`
- [ ] Wire redaction into `/triage` and `/index-incident`
- [ ] `tests/test_triage.py` (fakes via dependency overrides):
  - response schema and severity score mapping
  - citation guard drops unseen IDs
  - `LLMError` → 502
  - 422 on empty / oversized log
  - normalisation and redaction unit tests
  - prompt contains tags, and injection text stays inside `<new_log>`
- [ ] `tests/test_rag.py` — DB-backed against `triage_test` (skipped if DB is down)
- [ ] Live test (`-m live`), skipped without `ANTHROPIC_API_KEY`

**Acceptance criteria**
- `pytest` green with no API key
- With a key: one real triage returns valid JSON and cites the right incident

---

## Phase 4 — Triage quality eval ⬜
**Goal:** a measured accuracy number for Claude's answers. See [EVAL_PLAN.md](EVAL_PLAN.md) §3.

**Tasks**
- [ ] Ground truth per eval query: expected incident, severity, key root-cause facts
- [ ] `scripts/eval_triage.py` — severity exact / ±1, citation precision/recall, rubric-graded root cause (LLM judge), latency, tokens/cost per triage
- [ ] Results → `results/triage_eval_<model>_<date>.json` + summary table
- [ ] One prompt-tuning pass, re-run, record before/after
- [ ] Optional: same eval on a second model via `CLAUDE_MODEL` to show the quality/latency trade-off

**Acceptance criteria:** a baseline table committed; README draft cites it.
**Needs:** `ANTHROPIC_API_KEY`. Cost estimate shown before running.

---

## Phase 5 — Feedback loop + noise reduction ⬜
**Covers:** FR-10 to FR-13.

**Tasks**
- [ ] Tables `triage_runs`, `feedback` (ARCHITECTURE §5)
- [ ] Fingerprint = SHA-256 of the normalised, redacted log; cached hit within `DEDUP_WINDOW_MIN` → return the stored result + `duplicate_of`, `duplicate_count`
- [ ] `POST /feedback`; when `index: true`, create an incident from the confirmed/corrected triage
- [ ] `GET /stats`
- [ ] `scripts/replay_noise.py` — replay a burst (e.g. 500 logs from 12 distinct failures with varying timestamps/IDs) → LLM calls made vs avoided
- [ ] Tests for dedup window, feedback → indexing

**Acceptance criteria:** the replay reports calls avoided; a corrected triage is retrievable on the next similar log.

---

## Phase 6 — Cloud workflow ⬜
**Covers:** FR-14, FR-15, NFR-7.

**Tasks**
- [ ] `Dockerfile` (python:3.12-slim, embedding model baked in); `api` service in compose on 8030
- [ ] Moto service on 4567
- [ ] `lambda_fn/handler.py` — S3 event → read object → POST `/triage` → `triaged/` or `failed/`; stdlib `urllib` for HTTP
- [ ] `scripts/simulate_s3_ingest.py` — create bucket, upload sample logs, invoke handler with S3-shaped events, print results
- [ ] CloudWatch: metrics from API + Lambda; `put_metric_alarm` on `SeverityScore >= 5`
- [ ] `tests/test_lambda.py` with Moto (`mock_aws`)

**Acceptance criteria:** uploading 3 logs yields 3 result files in `triaged/`; metrics listed via `list_metrics` on Moto.

---

## Phase 7 — CI + publish ⬜

**Tasks**
- [ ] `.github/workflows/ci.yml` — ruff, pytest, pgvector service container, seed + retrieval eval; fail if hit@3 < threshold
- [ ] `scripts/bench.py` — N warm triages → p50/p95 for embed / search / LLM / total → `results/bench.json`
- [ ] README: what it does, architecture diagram, quick start, API examples, measured results table, limitations
- [ ] Final review of docs vs what was built; update ROADMAP to ✅

**Acceptance criteria:** CI green on GitHub; every README number traceable to a file in `results/`.

---

## Optional extras (after Phase 7)
- Real AWS deploy (S3 notification → Lambda, API on ECS/App Runner)
- `LLM_PROVIDER=bedrock` switch
- Minimal web page: paste a log, see the triage
