"""Embedding generation (fastembed, local ONNX) and pgvector similarity search."""

import re
from contextlib import contextmanager
from functools import lru_cache

import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from app.config import settings
from app.models import IncidentIn, SimilarIncident

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS incidents (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    service     TEXT NOT NULL,
    error_type  TEXT NOT NULL,
    log_text    TEXT NOT NULL,
    root_cause  TEXT NOT NULL,
    fix         TEXT NOT NULL,
    severity    TEXT NOT NULL,
    tags        TEXT[] NOT NULL DEFAULT '{{}}',
    embedding   vector({settings.embed_dim}) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS incidents_embedding_hnsw
    ON incidents USING hnsw (embedding vector_cosine_ops);
"""

# ---------- log normalisation ----------
# Strip run-specific noise so two occurrences of the same bug embed close together.
_NOISE = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?\b"), "<ts>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<addr>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{12,40}\b", re.I), "<hash>"),
    (re.compile(r"[ \t]+"), " "),
]
MAX_EMBED_CHARS = 4000  # bge-small truncates at 512 tokens anyway


def normalize_log(text: str) -> str:
    for pattern, repl in _NOISE:
        text = pattern.sub(repl, text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Keep the head (error message) and tail (innermost frames / "Caused by").
    if sum(len(ln) for ln in lines) > MAX_EMBED_CHARS and len(lines) > 30:
        lines = lines[:20] + ["..."] + lines[-10:]
    return "\n".join(lines)[:MAX_EMBED_CHARS]


# ---------- embeddings ----------


class Embedder:
    def __init__(self, model_name: str = settings.embed_model):
        from fastembed import TextEmbedding  # heavy import, keep lazy

        self._model = TextEmbedding(model_name=model_name)

    def embed(self, text: str) -> np.ndarray:
        return next(iter(self._model.embed([normalize_log(text)])))

    def embed_many(self, texts: list[str]) -> list[np.ndarray]:
        return list(self._model.embed([normalize_log(t) for t in texts]))


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()


# ---------- database ----------


class IncidentStore:
    def __init__(self, dsn: str = settings.database_url):
        # Schema first (the vector type must exist before register_vector).
        boot = psycopg2.connect(dsn)
        with boot, boot.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        boot.close()
        self._pool = ThreadedConnectionPool(1, 10, dsn)
        self._registered: set[int] = set()

    @contextmanager
    def conn(self):
        c = self._pool.getconn()
        if id(c) not in self._registered:
            register_vector(c)
            self._registered.add(id(c))
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            self._pool.putconn(c)

    def add(self, inc: IncidentIn, embedding: np.ndarray) -> int:
        with self.conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO incidents
                   (title, service, error_type, log_text, root_cause, fix, severity, tags, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (inc.title, inc.service, inc.error_type, inc.log_text, inc.root_cause,
                 inc.fix, inc.severity.value, inc.tags, embedding),
            )
            return cur.fetchone()[0]

    def search(self, embedding: np.ndarray, top_k: int) -> list[SimilarIncident]:
        with self.conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id, title, service, error_type, root_cause, fix, severity,
                          1 - (embedding <=> %s) AS similarity
                   FROM incidents ORDER BY embedding <=> %s LIMIT %s""",
                (embedding, embedding, top_k),
            )
            return [SimilarIncident(**row) for row in cur.fetchall()]

    def count(self) -> int:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM incidents")
            return cur.fetchone()[0]

    def truncate(self) -> None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("TRUNCATE incidents RESTART IDENTITY")
