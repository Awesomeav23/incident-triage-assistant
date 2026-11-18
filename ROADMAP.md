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
| 5 | Triage quality eval | 🔄 | Eval built + free demo baseline done; paid Claude run waits for approval |
| 6 | Feedback loop + noise reduction | ⬜ | `/feedback`, dedup, `/stats`, feedback buttons + stats page in the UI |
| 7 | Cloud workflow | ⬜ | S3 → Lambda → `/triage` → S3, CloudWatch on Moto, everything in Docker |
| 8 | CI + publish | ⬜ | GitHub Actions, latency bench, README with screenshots and results |

## Next up (as of 2026-11-18)

- **Phase 5 status:** answer key, eval script, both judges and the spending guard are built and tested (20 tests). Free demo baseline is in `results/`.
- **Waiting on approval:** the paid Claude run. Estimate: **~$2.21** full run (Opus triage + Opus judge, 53 + 47 calls), **~$1.90** with a Sonnet judge, **~$0.22** for a 5-query smoke run. Then one prompt-tuning pass + re-run (same cost again).
- **Without paying:** Phase 5 can't measure Claude's quality or latency, so the next free option is Phase 6 (feedback + dedup).
- **No-spend rule:** the app defaults to `LLM_MODE=demo` (no Claude calls). Paid runs (`--triager claude`, `LLM_MODE=claude`, `RUN_LIVE=1`) happen only after an explicit go-ahead with a cost estimate. The eval script refuses paid calls without `--yes`.
- **Open questions the paid run answers:** Claude takes 7–15 s per triage (target 2 s); citations of the top match were inconsistent across runs.
- **Running locally:** `docker compose up -d db`, `uvicorn app.main:app --port 8030 --env-file .env`, `cd frontend && npm run dev` → http://localhost:5181

## Docs
- [Requirements](docs/REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Design decisions](docs/DECISIONS.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Evaluation plan](docs/EVAL_PLAN.md)
