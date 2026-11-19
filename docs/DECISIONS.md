# Design decisions

Short ADRs: what was chosen, why, and what it costs.

---

### ADR-001 — Postgres + pgvector for the incident store
- **Decision:** a single Postgres 16 instance with the pgvector extension and an HNSW cosine index.
- **Why:** incidents are relational data (service, severity, tags, feedback) *and* vectors. One database handles joins, filters, full-text search and similarity in a single query. Fine at thousands of rows.
- **Alternatives:** Pinecone / Qdrant (an extra service, no SQL joins); FAISS in memory (no persistence or filtering).
- **Cost:** HNSW recall is approximate. At this data size the planner may skip the index entirely, which is fine.

### ADR-002 — Local embeddings with fastembed (bge-small-en-v1.5, 384-dim)
- **Decision:** generate embeddings in-process with fastembed (ONNX, CPU).
- **Why:** Claude has no embeddings endpoint. Local inference is free, needs no second API key, and takes ~15–30 ms per log on a laptop (bench p50 20 ms, p95 29 ms), which protects the latency budget.
- **Alternatives:** Voyage AI (better quality, needs a key, adds a network hop); OpenAI embeddings (another vendor).
- **Cost:** a ~130 MB model download on first run (built into the Docker image); 512-token input limit, handled by normalisation and head/tail truncation.

### ADR-003 — Normalise logs before embedding
- **Decision:** replace timestamps, UUIDs, IPs, hex addresses and hashes with placeholders; keep the head and tail of long logs.
- **Why:** two occurrences of the same bug differ mostly in these tokens. Removing them pulls duplicates together in vector space and gives a stable fingerprint for dedup.
- **Long logs:** over 4,000 chars, the head (~65%) and tail (~35%) are kept with separate budgets, so the innermost frames survive. Lines are capped at 1,000 chars only in that case; normal error lines are never cut, because their key words are often at the end (a 300-char cap measurably hurt retrieval).
- **Cost:** may occasionally remove a meaningful value (for example, a specific IP that *is* the problem). The raw log still goes to the LLM.

### ADR-004 — Hybrid retrieval with reciprocal rank fusion (Phase 2)
- **Decision:** run vector search and Postgres full-text search, then merge the rankings with RRF (`score = Σ 1/(60 + rank)`).
- **Why:** embeddings capture meaning ("connection refused after failover"); keywords catch exact tokens embeddings blur (`EUSAGE`, `TS2532`, `exit code 137`). RRF needs no score calibration between the two.
- **Validation:** kept only if the eval shows hybrid ≥ vector-only on hit@1/hit@3.
- **Result (Phase 2):** hybrid hit@1 0.936 vs vector 0.894 vs keyword 0.872 on 53 held-out logs; hybrid is the default (`SEARCH_MODE`).
- **Detail:** keyword search runs over `search_text`, built with the same tokenizer as the query (normalised, lower-cased alphanumeric tokens, small stop-word list), so stored and query tokens match. Postgres `ts_rank` has no IDF weighting, which is why keyword alone is weakest.

### ADR-004b — No-match cutoff favours keeping correct matches
- **Decision:** `MIN_SIMILARITY` = the highest cutoff that keeps every correct match in the eval (0.73).
- **Why:** a wrongly dropped match removes useful context; a wrongly kept one is still filtered by Claude's "only cite genuine matches" rule. The two similarity ranges overlap, so no cutoff is perfect.
- **Update (Phase 5):** the cutoff gates the result set, not each result. If any of the top k clears it, all top k are shown; otherwise only deeper results that clear it (usually none → "no close match"). Filtering per result had dropped two correct matches at 0.708 / 0.729 while keeping wrong ones at 0.730–0.732. Right family shown: 44 → 46 of 47; no-match decisions unchanged.

### ADR-005 — Claude API direct, Claude Opus 5.5 at low effort
- **Decision:** the `anthropic` Python SDK, model `claude-opus-5-5`, `effort: low`, configurable via env.
- **Why:** triage quality depends on reasoning over unfamiliar stack traces; Opus is the strongest default. `low` effort trims thinking time for latency.
- **Risk:** the <2 s target may not hold with Opus. Phase 5 measures accuracy *and* latency for the configured model, and the README reports what was measured. Switching model is one env var, and the eval re-measures quality.
- **Alternative:** Amazon Bedrock, deferred (optional `LLM_PROVIDER=bedrock` switch).

### ADR-006 — Structured outputs instead of parsing text
- **Decision:** `client.beta.messages.parse(output_format=LLMTriage)`, so the response is validated against the Pydantic schema.
- **Why:** no regex/JSON-in-markdown parsing, no missing fields; the API contract is guaranteed.
- **Also:** server-side refusal fallback is enabled (`fallbacks="default"`); a final refusal or `max_tokens` stop returns HTTP 502.

### ADR-007 — Treat logs as untrusted; redact secrets
- **Decision:** logs go inside `<new_log>` tags, the system prompt says to ignore instructions inside them, and secrets are redacted before storage/LLM (Phase 3).
- **Why:** CI logs routinely contain tokens and env dumps, and any log can contain attacker-controlled strings (for example, a request path).
- **Cost:** regex redaction is best-effort, not a guarantee. Documented as such.

### ADR-008 — Citation guard
- **Decision:** drop any `matched_incident_ids` that weren't in the retrieved set.
- **Why:** prevents a hallucinated incident reference from reaching an engineer.

### ADR-009 — Moto emulator for S3 / CloudWatch
- **Decision:** run Moto in docker-compose (port 4567) and point boto3 at it with `AWS_ENDPOINT_URL`.
- **Why:** zero cost and no credentials; the application code is the same as for real AWS.
- **Cost:** Moto doesn't execute the Lambda from a real S3 notification here. A script calls the handler with a real-shaped S3 event instead. A real-AWS deploy is an optional extra.
- **Why not real AWS (decided 2026-11-19):** the project's rule is zero spend (NFR-10), and the AWS account available for it is past the free plan, so anything on real AWS would be billed.
  - Lambda, S3 and CloudWatch alone would cost pennies: Lambda and up to ~10 metrics/alarms fall in AWS's always-free tier.
  - The real cost is the rest: a Lambda in the cloud can't reach an API on a laptop, so the API, Postgres + pgvector and the embedding model would need hosting (e.g. EC2/ECS + RDS), roughly $15–30 a month, plus easy-to-miss charges such as a NAT gateway (~$32 a month) and public IPv4 addresses.
  - The emulator runs the same boto3 code against the same S3/CloudWatch APIs; only `AWS_ENDPOINT_URL` changes. So the design is demonstrated and tested without the bill.
  - Deploying for real later needs no code changes: point the handler at a reachable `TRIAGE_API_URL`, add an S3 notification filtered on `incoming/`, drop the endpoint override, and set an AWS Budget alert first.

### ADR-010 — Fingerprint dedup instead of semantic dedup (Phase 6)
- **Decision:** dedup on SHA-256 of the normalised log within a time window.
- **Why:** exact and cheap, with no false merges. Near-duplicates still go through retrieval and are matched there.
- **Alternative:** vector-similarity clustering. More recall, but risk of merging different bugs. Revisit if the replay shows many near-misses.

### ADR-011 — Tests run without an API key
- **Decision:** FastAPI dependency overrides inject a fake embedder, store and LLM for unit tests. DB-backed tests use a separate `triage_test` database. Live LLM tests are skipped unless both `RUN_LIVE=1` and `ANTHROPIC_API_KEY` are set (a key alone never spends).
- **Why:** CI must be free, fast and deterministic.

### ADR-012 — React + Vite + TypeScript + Tailwind for the web UI (Phase 4)
- **Decision:** a separate `frontend/` app; Vite proxies `/api` in dev, nginx serves the build and proxies `/api` in Docker.
- **Why:** lets people try the system without curl, and makes the AI's behaviour visible (matches, similarity, confidence, "no close match"). TypeScript types mirror the Pydantic schemas, so API drift shows up as compile errors.
- **Alternatives:** plain HTML served by FastAPI (simplest, but gets messy past one page); Streamlit (fast, but a separate Python app that looks like a notebook demo).
- **Cost:** a Node toolchain and a second container; about one day of work.

### ADR-013 — One incident per failure family in triage results (Phase 4)
- **Decision:** `/triage` asks search for the best-ranked incident from each family (`distinct_families=True`), over-fetching up to 40 candidates so k distinct families survive.
- **Why:** the UI showed 3 copies of the same failure, which tells the engineer nothing new and gives Claude one data point three times. Distinct families give k different hypotheses.
- **Result:** hybrid hit@3 0.957 → 0.979, MRR 0.950 → 0.957, hit@1 unchanged (eval run with `--no-distinct` reproduces the old numbers).
- **Cost:** recurrences in other services are no longer shown side by side; the incidents page still lists them.

### ADR-014 — Free demo mode is the default
- **Decision:** `LLM_MODE` defaults to `demo`. `DemoTriager` returns the closest past incident's root cause, fix and severity, with generic next steps and confidence = similarity. Only the exact value `claude` enables paid calls; typos stay free. The live test additionally needs `RUN_LIVE=1`.
- **Why:** the project must be runnable, testable and demoable without spending money. A key in `.env` alone must never cause a charge.
- **Honesty:** the API returns `model: "demo"`, the header shows "Demo mode", every answer carries a "not generated by AI" banner, and the cited match is labelled "Closest match", not "Cited by Claude".
- **Cost:** demo answers can't adapt a fix to the new log or reason about logs with no match; that is what Claude mode is for.

### ADR-015 — Lambda: keep the log's tail, retry only what can pass (Phase 7)
- **Decision:** logs over the API's 50k-char limit are cut to their last 50k chars and marked `truncated`; the handler retries once on unreachable/502–504 and never on 4xx; every failure is written to `failed/` with the error and status.
- **Why:** CI logs put the failure at the end. A 4xx (bad input) won't succeed on retry; a gateway error might. Writing failures to S3 means nothing disappears silently.
- **Also:** only keys under `incoming/` are processed, so the handler's own `triaged/` / `failed/` writes in the same bucket can't loop.

### ADR-016 — Containers behind a compose profile (Phase 7)
- **Decision:** `api` and `web` are in the `app` profile; `docker compose up -d` still starts only Postgres and Moto.
- **Why:** day-to-day development runs uvicorn and Vite on the same ports (8030, 5181). The profile keeps both workflows without port clashes; `API_PORT` / `WEB_PORT` move the containers when both run.
- **Seeding:** the `api` container runs `python -m scripts.seed --if-empty` before uvicorn, so a fresh clone gets the 235 incidents; an existing index is left alone.
- **Secrets:** `.dockerignore` keeps `.env` out of the image; the API container reads it at run time (`env_file`, optional). Moto gets dummy credentials, so real AWS credentials are never used.
