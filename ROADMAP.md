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
| 7 | Cloud workflow | ⬜ | S3 → Lambda → `/triage` → S3, CloudWatch on Moto, everything in Docker |
| 8 | CI + publish | ⬜ | GitHub Actions, latency bench, README with screenshots and results |

## Next up (as of 2026-11-19)

- **Now:** Phase 7, cloud workflow: S3 upload → Lambda → `/triage` → result in S3, CloudWatch metrics, all on the free local AWS emulator (Moto).
- **Staying free (decided 2026-11-18):** the app defaults to `LLM_MODE=demo` (no Claude calls). Claude-quality numbers are marked "not measured (demo mode)". Paid runs (`--triager claude`, `LLM_MODE=claude`, `RUN_LIVE=1`) only after an explicit go-ahead with a cost estimate; the eval script refuses them without `--yes`.
- **If credits are ever added:** `python -m scripts.eval_triage --triager claude --judge claude --limit 5 --yes` (~$0.22) first, then the full run (~$2.21).
- **Running locally:** `docker compose up -d db`, `uvicorn app.main:app --port 8030 --env-file .env`, `cd frontend && npm run dev` → http://localhost:5181

## Docs
- [Requirements](docs/REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Design decisions](docs/DECISIONS.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Evaluation plan](docs/EVAL_PLAN.md)
