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

| ID | Requirement | Phase |
|---|---|---|
| FR-1 | `POST /index-incident` stores a resolved incident (title, service, error type, log, root cause, fix, severity, tags) and its embedding | 1 |
| FR-2 | Logs are normalised before embedding: timestamps, UUIDs, IPs, hex addresses and hashes are replaced with placeholders, so repeats of one bug embed close together | 1 |
| FR-3 | Retrieve the top-k (default 3, max 10) most similar past incidents, each with a similarity score | 1 |
| FR-4 | Hybrid retrieval: vector similarity combined with Postgres full-text keyword search | 2 |
| FR-5 | Matches below a similarity threshold are reported as "no close match" instead of being passed to the LLM as if relevant | 2 |
| FR-6 | `POST /triage` returns root-cause summary, severity (5 levels + 1–5 score), severity reason, suggested fix, up to 5 next steps, cited incident IDs, confidence (0–1), the similar incidents, and per-step latency | 3 |
| FR-7 | Claude's output is schema-validated (structured outputs); malformed or refused responses return HTTP 502 with a clear message | 3 |
| FR-8 | Claude may only cite incidents it was shown; any other IDs are dropped | 3 |
| FR-9 | Obvious secrets in logs (API keys, bearer tokens, passwords in URLs/env dumps, AWS keys) are redacted before the log is sent to the LLM or stored | 3 |
| FR-10 | `POST /feedback` lets an engineer mark a triage correct or supply the real root cause/fix; confirmed results can be indexed as new incidents | 6 |
| FR-11 | Every triage run is stored (log fingerprint, result, latency, model, feedback) | 6 |
| FR-12 | Duplicate suppression: a log whose normalised fingerprint was triaged within a configurable window returns the cached result plus a duplicate count, without calling the LLM | 6 |
| FR-13 | `GET /stats` reports incidents indexed, triages run, duplicates suppressed, feedback accuracy | 6 |
| FR-14 | Uploading a log to S3 `incoming/` triggers a Lambda that calls `/triage` and writes the result to `triaged/<key>.json` | 7 |
| FR-15 | Triage metrics (latency, severity, matches, duplicates) are published to CloudWatch; an alarm fires on critical-severity triages | 7 |
| FR-16 | `GET /health` reports DB connectivity and incident count | 1 |
| FR-17 | Web UI triage page: paste a log (or pick a sample), see severity, root cause, fix, next steps, confidence, matched incidents with similarity, and latency breakdown | 4 |
| FR-18 | Web UI incidents page: search/filter past incidents, view details, add a resolved incident via a form | 4 |
| FR-19 | `GET /incidents` (paginated, text + severity filter), `GET /incidents/{id}`, `GET /samples` | 4 |
| FR-20 | Web UI shows loading, error (422/502 message) and "no close match" states clearly | 4 |
| FR-21 | Web UI feedback buttons, duplicate badge, and stats page | 6 |

## 5. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Retrieval latency (embed + search), warm | p95 < 200 ms |
| NFR-2 | End-to-end triage latency, warm | p50 < 2 s target, **measured not assumed** (dominated by the LLM call; see DECISIONS ADR-005) |
| NFR-3 | Runs fully locally with no cloud bill: Docker Postgres, local embeddings, AWS emulator | required |
| NFR-4 | Test suite runs without an Anthropic API key (LLM stubbed); live tests are opt-in | required |
| NFR-5 | Log input is untrusted: prompt-injection text inside a log must not change the system's instructions | required |
| NFR-6 | Max log size 50,000 chars; longer input rejected with 422 | required |
| NFR-7 | Metrics publishing never fails a triage request | required |
| NFR-9 | Web UI usable at phone width and in light and dark mode | required |
| NFR-8 | Every number in the README comes from a script in this repo that anyone can re-run | required |

## 6. Success metrics

| Metric | Target | Measured by |
|---|---|---|
| Historical incidents indexed | ≥ 200 patterns | `GET /health` / seed output |
| Retrieval hit@3 on held-out logs | ≥ 90% | `scripts/eval_retrieval.py` |
| Retrieval hit@1 | ≥ 80% | `scripts/eval_retrieval.py` |
| Severity accuracy (exact / within ±1) | ≥ 70% / ≥ 95% | `scripts/eval_triage.py` |
| Correct past incident cited | ≥ 85% recall | `scripts/eval_triage.py` |
| Triage latency p50 / p95 | < 2 s / reported | `scripts/bench.py` |
| LLM calls avoided by dedup on a noisy replay | reported | Phase 6 replay script |

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
- `ANTHROPIC_API_KEY` is not set yet; Phases 1–2 don't need it
- The historical dataset starts with 20 hand-written incidents. The 200+ set includes synthetic variants, labelled as such
