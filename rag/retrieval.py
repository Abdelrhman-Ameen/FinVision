"""Versioned local vector index and hybrid retrieval over the existing evidence library."""

from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

from .data_tools import rag_library

from .book_search import book_query
from .embeddings import EMBEDDING_SIZE, MAX_TOKENS, REVISION, LocalEmbeddings, download_model
from .food_decisions import decision_route, search_profile


DATABASE = "evidence.sqlite"
VECTORS = "embeddings.npz"
VECTOR_SCHEMA = "esis-evidence-v2"
EMBEDDING_SCHEMA = f"{VECTOR_SCHEMA}:{REVISION}:{MAX_TOKENS}"
RESULTS = 6


def corpus_revision() -> str:
    root = rag_library.ROOT
    inputs = [
        *sorted((root / "cards").glob("*.json")),
        root / "coverage" / "sectors.json", root / "coverage" / "scenarios.json",
        root / "evaluation_questions.json",
        root / "data" / "publication_snapshot.json", root / "data" / "indicator_snapshot.json",
        root / "data" / "book_snapshot.json",
        Path(rag_library.__file__),
    ]
    institutional = root / "data" / "institutional_book_snapshot.json"
    if institutional.is_file():
        inputs.append(institutional)
    digest = hashlib.sha256(EMBEDDING_SCHEMA.encode())
    for path in inputs:
        digest.update(path.name.encode())
        # Python source and authored JSON can be checked out with either newline style.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def semantic_records(database: Path) -> tuple[int, list[dict]]:
    with closing(rag_library.connect_readonly(database)) as connection:
        count, = connection.execute("SELECT COUNT(*) FROM evidence").fetchone()
        # Abstracts sort before page excerpts under the same bibliographic ID.
        rows = connection.execute("""
            SELECT evidence.payload
            FROM (SELECT COALESCE(MAX(CASE WHEN id = document_id || '-abstract' THEN id END), MIN(id)) AS id
                  FROM evidence GROUP BY document_id) AS representatives
            JOIN evidence USING (id) ORDER BY evidence.id
        """)
        return count, [json.loads(payload) for (payload,) in rows]


def vector_text(record: dict) -> str:
    title = record["title"]
    if record["origin"] == "curated":
        return "\n".join((title, record["text"], " ".join(record["terms"])))
    if record["publication_type"] == "Open textbook":
        return "\n".join((title, " ".join(record["terms"])))
    # The complete evidence stays in FTS. The semantic index represents each source once.
    lead = " ".join(record["text"].split()[:18])
    return f"{title}\n{lead}"


def load_vectors(path: Path, records: list[dict], revision: str) -> np.ndarray:
    with np.load(path, allow_pickle=False) as index:
        if str(index["revision"].item()) != revision:
            raise ValueError("The knowledge base changed. Run prepare to rebuild the index.")
        if index["ids"].tolist() != [record["id"] for record in records]:
            raise ValueError("The vector index and the source records do not match. Run prepare.")
        vectors = index["vectors"]
    if vectors.dtype != np.float32 or vectors.shape != (len(records), EMBEDDING_SIZE):
        raise ValueError("Invalid embedding index dimensions. Run prepare.")
    if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-3):
        raise ValueError("Invalid normalized vectors. Run prepare.")
    return vectors


def prepare(cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    download_model(cache / "model")
    revision = corpus_revision()
    database = cache / DATABASE
    vector_file = cache / VECTORS
    previous = {}
    if database.is_file() and vector_file.is_file():
        count, representatives = semantic_records(database)
        try:
            load_vectors(vector_file, representatives, revision)
            return {"indexed": count, "embedded_sources": len(representatives),
                    "cached": True, "embedding_dimensions": EMBEDDING_SIZE}
        except (OSError, KeyError, ValueError):
            pass
        try:
            with np.load(vector_file, allow_pickle=False) as index:
                if str(index["embedding_schema"].item()) == EMBEDDING_SCHEMA:
                    vectors = index["vectors"]
                    valid = (vectors.shape == (len(index["hashes"]), EMBEDDING_SIZE)
                             and vectors.dtype == np.float32 and np.isfinite(vectors).all()
                             and np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-3))
                    if valid:
                        previous = dict(zip(index["hashes"].tolist(), vectors))
        except (OSError, KeyError, ValueError):
            pass

    rag_library.build_index(database)
    count, representatives = semantic_records(database)
    texts = [vector_text(record) for record in representatives]
    hashes = [hashlib.sha256(body.encode()).hexdigest() for body in texts]
    matrix = np.empty((len(texts), EMBEDDING_SIZE), dtype=np.float32)
    missing = []
    for index, digest in enumerate(hashes):
        if digest in previous:
            matrix[index] = previous[digest]
        else:
            missing.append(index)
    missing.sort(key=lambda index: len(texts[index]))
    if missing:
        encoder = LocalEmbeddings(cache / "model")
        batch_size = 80
        for position in range(0, len(missing), batch_size):
            batch = missing[position : position + batch_size]
            matrix[batch] = encoder.encode([texts[index] for index in batch])
            if position % (batch_size * 10) == 0:
                print(f"Embedded {position + len(batch)}/{len(missing)} source representatives", flush=True)
    with NamedTemporaryFile(dir=cache, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            np.savez_compressed(
                stream, ids=np.asarray([record["id"] for record in representatives]),
                revision=revision, vectors=matrix, hashes=np.asarray(hashes), embedding_schema=EMBEDDING_SCHEMA,
            )
            stream.close()
            load_vectors(temporary, representatives, revision)
            temporary.replace(vector_file)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)
    return {"indexed": count, "embedded_sources": len(representatives),
            "reused_embeddings": len(texts) - len(missing), "cached": False, "embedding_dimensions": EMBEDDING_SIZE}


class HybridRetriever:
    def __init__(self, cache: Path):
        self.database = cache / DATABASE
        self.indexed_records, self.representatives = semantic_records(self.database)
        self.vectors = load_vectors(cache / VECTORS, self.representatives, corpus_revision())
        self.by_id = {record["id"]: record for record in self.representatives}
        self.encoder = LocalEmbeddings(cache / "model")

    def search(self, question: str, country: str = "EGY", limit: int = RESULTS) -> list[dict]:
        if limit <= 0 or not rag_library.words(question):
            return []
        if book_request := book_query(question):
            terms, egypt = book_request
            with closing(rag_library.connect_readonly(self.database)) as db:
                return rag_library.rank(db, terms, limit=limit, country=country, book_search=True, egypt_books=egypt)
        route = decision_route(question)
        anchor_ids, expansion, scenario = search_profile(route, country)
        food_sector = route in {"profit", "branch"}
        expanded_question = f"{question}\n{expansion}" if expansion else question
        with closing(rag_library.connect_readonly(self.database)) as db:
            lexical = rag_library.rank(db, expanded_question, limit=25, country=country,
                                       sector="food" if food_sector else None, scenario=scenario)
            # For specific decisions, prefer candidates linked to both sector and scenario.
            linked = set()
            if food_sector:
                linked = {row[0] for row in db.execute(
                    "SELECT document_id FROM source_topics WHERE topic = 'topic:food' INTERSECT "
                    "SELECT document_id FROM source_topics WHERE topic = ?", (f"scenario:{scenario}",)
                )}
        queries = self.encoder.encode([question, expansion] if expansion else [question], query=True)
        similarities = (self.vectors @ queries.T).max(axis=1)
        dense = np.argsort(-similarities, kind="stable")[:50]
        combined = defaultdict(float)
        lexical_by_source = {item["document_id"]: item["id"] for item in lexical}
        contextual = {item["id"]: item for item in lexical}
        for rank, hit in enumerate(lexical, 1):
            combined[hit["id"]] += 1.15 / (24 + rank)
        for rank, index in enumerate(dense, 1):
            if similarities[index] >= 0.73:
                source = self.representatives[index]
                identifier = lexical_by_source.get(source["document_id"], source["id"])
                contextual.setdefault(identifier, source)
                combined[identifier] += 1.0 / (24 + rank)
        best = sorted(
            combined,
            key=lambda identifier: (
                -combined[identifier] * (1 + (rag_library.geography_boost(contextual[identifier], country) - 1) / 2)
                * (1.2 if contextual[identifier]["document_id"] in linked else 1.0),
                identifier,
            ),
        )
        result, sources = [], set()
        for identifier in [*(key for key in anchor_ids if key in self.by_id), *best]:
            record = contextual[identifier] if identifier in contextual else self.by_id[identifier]
            if record["document_id"] not in sources:
                result.append(record)
                sources.add(record["document_id"])
                if len(result) == limit:
                    break
        return result
