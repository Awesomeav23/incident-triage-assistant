# Evaluation plan

Three things get measured: **retrieval** (does search find the right past incident?),
**triage quality** (is Claude's answer right?), and **latency/cost**. Every reported
number comes from a script in `scripts/` and is saved to `results/`.

---

## 1. Datasets

| File | Contents | Used for |
|---|---|---|
| `data/sample_logs.json` | 20 hand-written base incidents | Source |
| `data/families_extra.json` | 27 hand-written base incidents, each with a held-out log | Source |
| `data/eval_queries.json` | 15 hand-written held-out logs | Source |
| `data/incidents_expanded.json` | 235 incidents: 47 families × (1 base + 4 synthetic recurrences, `synthetic: true`) | Index (Phase 2+) |
| `data/eval_set.json` | 53 held-out logs **never indexed**, each with `expected_family` (or `null` = no real match) | All evals |
| `data/triage_truth.json` | Answer key per family: severity + 2 key root-cause facts with keywords | Triage eval (Phase 5) |
| `data/samples.json` | 7 held-out logs for the UI's sample buttons, never indexed | Web UI |

All incidents are fictional. `scripts/expand_dataset.py` builds the last two files deterministically.

Rules:
- Eval logs are new occurrences of known bugs: different service names, hosts, values and line numbers, not copies.
- At least ~10% of eval queries have no real match, to test that the system says "no close match" instead of forcing one.
- Eval queries are never added to the index, so evaluation can't leak.

---

## 2. Retrieval eval — `scripts/eval_retrieval.py`

| Metric | Definition |
|---|---|
| hit@1 | Share of matchable queries whose top result is from the expected **family** (any recurrence counts) |
| hit@3 | … in the top 3 |
| MRR | Mean of 1/rank of the first result from the expected family (0 if not in top 10) |
| No-match precision (Phase 2+) | Share of no-match queries where every result is below `MIN_SIMILARITY` |
| Similarity gap | Mean top-1 similarity for matchable vs no-match queries (tells us where to set the threshold) |
| Latency | Embed and search p50 / p95 (ms), warm, after one warm-up call |

Run per mode in Phase 2: `--mode vector | keyword | hybrid`.
**Targets:** hit@1 ≥ 80%, hit@3 ≥ 90%, search p95 < 200 ms.

---

## 3. Triage quality eval — `scripts/eval_triage.py` (Phase 5)

Each eval query gets ground truth: expected incident, expected severity, and 2
**key facts** the root cause must mention (e.g. "connection leak", "sessions not closed").

| Metric | How it's scored |
|---|---|
| Severity exact | predicted == expected |
| Severity within ±1 | \|score − expected score\| ≤ 1 |
| Citation recall | expected incident ∈ `matched_incident_ids` (matchable queries) |
| Citation precision | cited IDs that are correct / all cited IDs |
| No-match honesty | for no-match queries: `matched_incident_ids` empty and confidence < 0.5 |
| Root-cause score | LLM judge, rubric 0–2 per key fact (0 missing, 1 vague, 2 clear) → normalised 0–1 |
| Fix actionable | judge yes/no: names a concrete change (config key, code change, command) |
| Calibration | mean confidence on correct vs incorrect answers |

Ground truth lives in `data/triage_truth.json` (per family: severity + key facts with keywords).

**Judges** (`--judge`):
- `claude`: a separate Claude call with the rubric, the expected facts and the triage
  output, using structured output (`{fact_scores: [0|1|2, ...], actionable: bool, rationale}`).
  To check the judge itself, a sample of ~10 judgements is read by hand and agreement is noted.
- `keyword` (free): a fact scores 2 if any of its keywords appears in the root cause, else 0.
  Coarse but deterministic; used for the demo baseline and quick regression checks.

Two extra diagnostics separate retrieval misses from Claude misses: **retrieval ceiling**
(the expected family was among the incidents shown) and **citation recall when shown**.

**Spending guard:** with a paid triager or judge the script prints a cost estimate and exits
unless `--yes` is passed (`--estimate-only` stops after the estimate; `--limit N` for smoke runs).

**Targets:** severity exact ≥ 70% / ±1 ≥ 95%, citation recall ≥ 85%, root-cause score ≥ 0.8.

**Prompt changes:** one baseline run, one tuned run, both saved. A change is kept only if
it doesn't lower any metric beyond noise (re-run once if the difference is small).

**Cost:** estimated before running (queries × (triage + judge) tokens) and confirmed first.
Results: `results/triage_eval_<model|demo>_<judge>[_<tag>]_<date>.json`.

---

## 4. Latency + cost — `scripts/bench.py` (Phase 8)

- N = 30 warm requests per sample log (7 logs) through the real `/triage` route in-process, after 3 warm-up calls
- Two paths: **fresh** (history bypassed: embed + search + answer) and **repeat** (answered from history)
- Report p50 / p95 / max for `embed`, `search`, `llm`, `total` (from the response's `latency_ms`) plus client-side wall time
- Demo mode (free): `llm` is ~0. Claude latency and tokens per triage aren't benchmarked (cost); observed 6.7–15 s per call in Phases 3–4
- The <2 s target is checked against **total p50**; if it's missed, the README says so and shows the per-step breakdown
- **Result (2026-11-19):** fresh total p50 25 ms / p95 39 ms (embed 20 ms, search 4.7 ms); repeat p50 2.5 ms. Target met in demo mode, missed with Claude

---

## 5. Noise reduction — `scripts/replay_noise.py` (Phase 6)

- Generate a burst of logs from a small number of distinct failures (e.g. 500 logs, 12 failures) with randomised timestamps, request IDs and IPs
- Replay through `/triage` and report: total logs, LLM calls made, calls avoided by dedup, % reduction, time saved (avoided calls × mean LLM latency)
- Runs in demo mode (free); Claude time/cost saved are estimates (9 s, $0.03 per call from Phase 3). Only runs created by the replay count, and they're deleted afterwards unless `--keep`
- **Result (2026-11-19):** 500 logs, 12 failures → 12 model calls, 488 avoided (97.6%)

---

## 6. In CI (Phase 8)

- `ruff check`, unit + DB tests (no key; Moto in-memory for AWS), frontend typecheck/test/build, Docker image builds
- Seed + retrieval eval against a pgvector service container; **fail if hybrid hit@3 < 0.93** (baseline 0.979 minus 5 points): `eval_retrieval --mode hybrid --min-hit3 0.93`
- Triage eval is **not** run in CI (needs a key and costs money); run manually and commit the results

---

## 7. Reporting format

Every script writes `results/<eval>_<YYYY-MM-DD>.json`:
```json
{ "run_at": "...", "git_sha": "...", "config": { "model": "...", "mode": "hybrid", "top_k": 3 },
  "dataset": { "indexed": 214, "queries": 50 },
  "metrics": { "hit@1": 0.84, "hit@3": 0.94, "mrr": 0.89 },
  "per_query": [ { "name": "...", "expected": "...", "rank": 1, "top_similarity": 0.91 } ] }
```
and prints a short table to the terminal. The README links to the specific result files.
