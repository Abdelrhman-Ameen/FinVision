"""Evaluate the actual local hybrid retriever against the knowledge-base questions."""

import argparse
import json
from time import perf_counter

from .data_tools.rag_library import ROOT, load_json

from .indicators import infer_country
from .retrieval import HybridRetriever
from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, choices=range(1, 16), metavar="1..15", default=6)
    args = parser.parse_args()
    retriever = HybridRetriever(Settings.load().cache)
    cases = load_json(ROOT / "evaluation_questions.json") + load_json(ROOT / "coverage" / "evaluation_v2.json")
    timings = []
    misses = []
    for case in cases:
        start = perf_counter()
        question = case["question"]
        hits = retriever.search(question, infer_country(question, "EGY"), limit=args.top_k)
        timings.append((perf_counter() - start) * 1000)
        ids = [hit["id"] for hit in hits]
        if not set(ids) & set(case["evidence_ids"]):
            misses.append({"id": case["id"], "expected": case["evidence_ids"], "retrieved": ids})
    timings.sort()
    print(json.dumps({
        "questions": len(cases), "top_k": args.top_k, "hits": len(cases) - len(misses),
        "median_retrieval_ms": round(timings[len(timings) // 2]),
        "misses": misses,
    }, indent=2))


if __name__ == "__main__":
    main()
