"""Measure citation retrieval and report sector and scenario coverage."""

import argparse
from collections import Counter
from contextlib import closing
from pathlib import Path

from .rag_library import DATA, ROOT, connect_readonly, json_lines, load_json, lookup_indicator, rank


def evaluate(index: Path, limit: int) -> tuple[dict, list[str]]:
    original = load_json(ROOT / "evaluation_questions.json")
    expanded = load_json(ROOT / "coverage" / "evaluation_v2.json")
    sectors = load_json(ROOT / "coverage" / "sectors.json")
    scenarios = load_json(ROOT / "coverage" / "scenarios.json")
    assert {item["id"] for item in sectors} == {case["sector"] for case in expanded}
    assert {item["id"] for item in scenarios} == {case["scenario"] for case in expanded}
    cases = original + expanded
    assert len({case["id"] for case in cases}) == len(cases)
    raw_hit, routed_hit, misses = 0, 0, []
    verified = []
    with closing(connect_readonly(index)) as db:
        for case in cases:
            accepted = set(case["evidence_ids"])
            available = {
                row[0] for row in db.execute(
                    "SELECT id FROM evidence WHERE id IN (" + ",".join("?" for _ in accepted) + ")",
                    tuple(accepted),
                )
            }
            if available != accepted:
                raise ValueError(f"Unknown evidence reference in {case['id']}")
            raw = rank(db, case["question"], limit=limit)
            routed = rank(db, case["question"], sector=case.get("sector"), scenario=case.get("scenario"), limit=limit)
            raw_hit += any(record["id"] in accepted for record in raw)
            good = any(record["id"] in accepted for record in routed)
            routed_hit += good
            if not good:
                misses.append(f"{case['id']} — {case['question']}")
        for code, country, year in (
            ("FP.CPI.TOTL.ZG", "EGY", 2024), ("ST.INT.ARVL", "MAR", 2019),
            ("IT.NET.USER.ZS", "JOR", 2022),
        ):
            value = lookup_indicator(db, code, country, year)
            if value["year"] != year or value["indicator"] != code or value["country"] != country:
                raise ValueError("Indicator lookup returned a different period or series")
            verified.append(f"{code}/{country}/{year}")
        try:
            lookup_indicator(db, "FP.CPI.TOTL.ZG", "EGY", 1900)
        except ValueError:
            verified.append("Missing historical observation is reported")
        else:
            raise ValueError("A missing year unexpectedly produced a value")
    return {"raw_hits": raw_hit, "routed_hits": routed_hit, "cases": len(cases), "limit": limit,
            "indicator_checks": verified}, misses


def report(result: dict, misses: list[str]) -> str:
    sectors = load_json(ROOT / "coverage" / "sectors.json")
    scenarios = {item["id"]: item for item in load_json(ROOT / "coverage" / "scenarios.json")}
    catalog_size, indexed = Counter(), Counter()
    for entry in json_lines(DATA / "publications.jsonl"):
        for group in entry["groups"]:
            catalog_size[group] += 1
            indexed[group] += entry["indexed_records"] > 0
    limit, total = result["limit"], result["cases"]
    lines = [
        "# RAG coverage and retrieval checks", "",
        "The Arabic sector and scenario names are the labels used in the coverage files.", "",
        f"- Search found at least one accepted source in the top {limit} for "
        f"**{result['raw_hits']}/{total}** author-written queries with query text alone.",
        f"- Search with known sector and scenario context found a source for "
        f"**{result['routed_hits']}/{total}** queries. Context is supplied in the expanded cases.",
        "- Retrieval was tested locally with SQLite FTS5. Answer quality and citation grounding require a separate evaluation with the production retriever and language model.",
        "- Numeric checks passed for a specific annual period in Egypt, Morocco and Jordan, and for an unavailable historical period.",
        "",
        "## Sector coverage", "",
        "Counts show publications found by a sector search group. The same publication may match several groups. "
        "The priority scenarios are starting points for a business in that sector. Relevance and the strength of a claim depend on the source and firm context.",
        "",
        "| Sector | Sources in catalog group | Group sources indexed | Priority scenarios |",
        "| --- | ---: | ---: | --- |",
    ]
    for sector in sectors:
        key = sector["id"] if sector["id"] != "general" else "egypt"
        themes = "، ".join(scenarios[name]["title_ar"] for name in sector["priority_scenarios"])
        lines.append(f"| {sector['title_ar']} | {catalog_size[key]} | {indexed[key]} | {themes} |")
    lines.extend(["", "## Retrieval misses", ""])
    lines.extend([f"- {item}" for item in misses] or [
        "Every routed case in this evaluation returned at least one accepted source within the selected limit."
    ])
    lines.extend([
        "", "## Coverage gaps to track", "",
        "- Query examples are authored around the project’s data and goals. Add fresh founder questions and paraphrases before using this score as a release criterion.",
        "- Local city, district and business-level benchmarks need separate dated observations. National indicators cannot answer store-level demand, local rents or competitor performance.",
        "- Many catalog entries contribute an abstract. Inspect full results and subgroup definitions before applying a study beyond what the indexed text directly supports.",
        "- The broad document snapshot uses one publisher repository. Egyptian agencies and other international organizations appear in the reviewed cards and are priorities for additional licensed feeds.",
        "- The dated WDI snapshot serves historical numbers. Latest exchange rates, tariffs, policy rates and regulatory decisions need a current source at query time.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "esis_index.sqlite")
    parser.add_argument("--limit", type=int, default=5, choices=range(1, 21), metavar="1..20")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result, misses = evaluate(args.index, args.limit)
    output = report(result, misses)
    if args.report:
        args.report.write_text(output, encoding="utf-8", newline="\n")
    print(f"Top {args.limit}: raw {result['raw_hits']}/{result['cases']}, routed {result['routed_hits']}/{result['cases']}")
    for miss in misses:
        print(f"Missed {miss.split(' — ', 1)[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
