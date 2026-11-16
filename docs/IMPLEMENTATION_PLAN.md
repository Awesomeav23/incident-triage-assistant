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

## Phase 4 — Frontend ⬜
**Goal:** anyone can paste a log in a browser and read the triage. **Covers:** FR-17 to FR-20. See ADR-012.

**Stack:** React 18 + Vite + TypeScript + Tailwind CSS, in `frontend/`. Dev server on **5181**; `/api/*` proxied to the API on 8030 (same origin, so no CORS).

**Backend additions**
- [ ] `GET /incidents?q=&severity=&limit=&offset=` — paginated list for the browse page
- [ ] `GET /incidents/{id}` — full incident, including the log
- [ ] `GET /samples` — a few eval logs for the "Try a sample" buttons

**Pages**
- [ ] **Triage** (`/`)
  - log text area with a character count (50k limit), `top_k` selector, Triage button
  - "Try a sample" chips that fill the box
  - loading state with elapsed time; errors shown inline (422 / 502 with the API's message)
  - result card: severity badge (colour by level), root cause, severity reason, fix (code-formatted when it contains code), numbered next steps, confidence meter
  - matched past incidents: similarity bar, "cited by Claude" marker, expandable root cause + fix
  - "no close match" state when retrieval finds nothing above the threshold
  - latency breakdown bar (embed / search / Claude)
- [ ] **Incidents** (`/incidents`) — searchable, filterable table; detail drawer; "Add incident" form → `POST /index-incident`
- [ ] Layout: header nav, health indicator (incident count from `/health`), responsive down to phone width, light/dark

**Tests**
- [ ] Vitest + React Testing Library: result card renders each severity; error and no-match states; sample chip fills the box
- [ ] Typed API client (`src/api.ts`) mirroring the Pydantic schemas

**Acceptance criteria**
- `npm run dev` → paste a sample log → result card renders with matched incidents and latency
- Incident added through the form shows up in a triage of a similar log
- `npm run build` and `npm test` pass
- Screenshots saved to `docs/screenshots/`

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
**Needs:** `ANTHROPIC_API_KEY`. Cost estimate shown before running.

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
