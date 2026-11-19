# Roadmap

Status: ✅ done · 🔄 in progress · ⬜ not started
Task-level detail: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)

| # | Phase | Status | Outcome |
|---|---|---|---|
| 0 | Docs | ✅ | Requirements, architecture, decisions, plan, eval plan |
| 1 | Core search | ✅ | pgvector store, seed, `/index-incident`, retrieval eval |
| 2 | Bigger dataset + hybrid search | ✅ | 200+ incidents, vector + keyword search compared |
| 3 | Claude triage + tests | ✅ | `/triage`, redaction, citation guard, pytest suite |
| 4 | Frontend | ✅ | React web UI: paste a log, see the triage; browse/add incidents |
| 5 | Triage quality eval | ✅ | Eval + answer key + free demo baseline; cutoff fix (right incident shown 44→46 of 47). Claude numbers not measured (staying free) |
| 6 | Feedback loop + noise reduction | ✅ | Repeated logs answered from history (500-log burst → 12 model calls, 97.6% avoided), 👍/✏️ feedback, Stats page |
| 7 | Cloud workflow | ✅ | S3 upload → Lambda → `/triage` → `triaged/`, CloudWatch metrics + alarm on Moto; API + web in Docker |
| 8 | CI + publish | ✅ | GitHub Actions (lint, 140 + 35 tests, retrieval gate, Docker builds), latency bench, final README |

## Status (as of 2026-11-19)

**All 8 phases done.** Everything runs free: demo mode (no Claude calls), local embeddings, AWS on Moto, CI without secrets.

- **Free by default:** paid runs (`LLM_MODE=claude`, `RUN_LIVE=1`, `eval_triage --triager claude --yes`) only after an explicit go-ahead with a cost estimate.
- **Optional extras, not planned:** Claude answer-quality run (~$2.21, or ~$0.22 for `--limit 5`); real AWS deploy (needs a paid AWS account); Bedrock provider switch.
- **Running locally:** `docker compose up -d db moto`, `uvicorn app.main:app --port 8030 --env-file .env`, `cd frontend && npm run dev` → http://localhost:5181. Or everything in containers: `docker compose --profile app up -d --build`. S3 flow: `python -m scripts.simulate_s3_ingest`

## Docs
- [Requirements](docs/REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Design decisions](docs/DECISIONS.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Evaluation plan](docs/EVAL_PLAN.md)
