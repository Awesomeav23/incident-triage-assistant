"""FastAPI routes: /triage, /index-incident, /health."""

import time
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException

from app import metrics
from app.config import settings
from app.llm import LLMError, Triager, get_triager
from app.models import SEVERITY_SCORE, IncidentIn, IndexedIncident, TriageRequest, TriageResponse
from app.rag import Embedder, IncidentStore, get_embedder


@lru_cache(maxsize=1)
def get_store() -> IncidentStore:
    return IncidentStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up so the first request doesn't pay model-load / connect cost.
    if not app.dependency_overrides:
        get_store()
        get_embedder().embed("warmup")
    yield


app = FastAPI(title="Incident Triage Assistant", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health(store: IncidentStore = Depends(get_store)):
    return {"status": "ok", "incidents_indexed": store.count()}


@app.post("/index-incident", response_model=IndexedIncident, status_code=201)
def index_incident(
    inc: IncidentIn,
    store: IncidentStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
):
    # Embed only the log, the same input /triage embeds, so queries and docs match.
    vec = embedder.embed(inc.log_text)
    return IndexedIncident(id=store.add(inc, vec), title=inc.title)


@app.post("/triage", response_model=TriageResponse)
def triage(
    req: TriageRequest,
    store: IncidentStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
    triager: Triager = Depends(get_triager),
):
    t0 = time.perf_counter()
    vec = embedder.embed(req.log)
    t1 = time.perf_counter()
    similar = store.search(vec, req.top_k)
    t2 = time.perf_counter()
    try:
        result = triager.triage(req.log, similar)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    t3 = time.perf_counter()

    # Never let the model cite an incident it wasn't shown.
    shown = {s.id for s in similar}
    result.matched_incident_ids = [i for i in result.matched_incident_ids if i in shown]

    latency = {
        "embed": round((t1 - t0) * 1000, 1),
        "search": round((t2 - t1) * 1000, 1),
        "llm": round((t3 - t2) * 1000, 1),
        "total": round((t3 - t0) * 1000, 1),
    }
    score = SEVERITY_SCORE[result.severity]
    metrics.put_triage_metrics(latency, score, len(result.matched_incident_ids))
    return TriageResponse(
        **result.model_dump(),
        severity_score=score,
        similar_incidents=similar,
        latency_ms=latency,
        model=triager.model,
    )
