"""Collect a larger, deduplicated snapshot of licensed World Bank evidence."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .refresh_rag_publications import BASE, OUTPUT, POLICY, QUERY_GROUPS, is_egypt, publication, write_jsonl


EXTRA_QUERIES = {
    "egypt": ("Egypt economic monitor", "Egypt business investment", "Egypt labor trade finance",
              "Egypt food prices", "Egypt agriculture poultry", "Egypt tourism restaurants retail"),
    "regional": ("Middle East firms productivity", "MENA enterprise finance", "Morocco Jordan Tunisia SMEs",
                 "Middle East economic development", "North Africa labor market", "Gulf business trade"),
    "agriculture": ("agricultural markets", "agricultural productivity", "agricultural commercialization",
                    "agribusiness value chains", "food price volatility", "agricultural input prices"),
    "food": ("food prices", "food systems", "food value chain", "food retail", "livestock poultry",
             "food supply", "nutrition food manufacturing", "food imports"),
    "retail": ("consumer demand", "consumer markets competition", "retail sector", "retail payments",
               "household consumption", "urban retail businesses", "local competition markets"),
    "ecommerce": ("ecommerce trade", "digital platforms", "consumer digital payments"),
    "software": ("digital economy", "digital entrepreneurship", "digital services trade", "firm digitization"),
    "telecom": ("telecommunications regulation", "connectivity digital economy", "broadband infrastructure"),
    "fintech": ("financial inclusion", "mobile money", "payment systems", "digital finance",
                "credit technology", "financial intermediation"),
    "health": ("health expenditure", "healthcare markets", "health provider payment", "hospital management"),
    "pharma": ("pharmaceutical markets", "access to medicines", "medicine procurement", "health supply chains"),
    "education": ("private education", "skills development", "labor skills programs", "vocational training", "edtech"),
    "tourism": ("tourism development", "tourism demand", "hospitality markets", "air travel connectivity"),
    "logistics": ("transport costs trade", "trade facilitation", "supply chain resilience", "port competition"),
    "manufacturing": ("firm performance", "industrial productivity", "manufacturing firms", "industrial policy",
                      "manufacturing jobs", "firm innovation", "production efficiency"),
    "textiles": ("garment exports", "apparel factories", "textile supply chains"),
    "construction": ("construction markets", "infrastructure investment", "public procurement contractors"),
    "real-estate": ("urban housing", "rental markets", "urban land", "real estate development"),
    "energy": ("energy markets", "electricity tariff", "energy transition", "industrial energy efficiency",
               "power outage firms", "renewable energy investment"),
    "water": ("water resource management", "water supply services", "irrigation productivity"),
    "waste": ("circular economy", "waste markets", "resource efficiency", "industrial recycling"),
    "working-capital": ("trade credit", "firm liquidity", "working capital", "accounts receivable",
                        "inventory management", "business finance", "cash flow firms"),
    "pricing": ("price elasticity", "pricing strategies", "market structure competition", "product differentiation",
                "consumer prices", "cost pass-through"),
    "costs": ("inflation and firms", "foreign exchange markets", "input costs", "exchange rate devaluation",
              "fuel prices", "operating costs"),
    "finance": ("enterprise finance", "SME finance", "financial constraints", "microenterprise capital",
                "bank lending", "corporate debt", "capital markets", "investment finance", "venture capital",
                "business access to credit", "credit guarantees"),
    "exports": ("export competitiveness", "firm exports", "global value chains", "international trade firms",
                "tariff reforms", "trade adjustment firms"),
    "marketing": ("marketing practices", "sales growth", "consumer behavior", "business marketing", "competitive strategy"),
    "training": ("business training", "entrepreneurial skills", "firm management", "entrepreneur training"),
    "hiring": ("labor markets", "firm employment", "worker productivity", "jobs private sector",
               "labor demand", "minimum wage", "workforce development"),
    "productivity": ("private sector development", "enterprise performance", "total factor productivity",
                     "business environment", "technology adoption", "firm capabilities", "management practices"),
    "formalization": ("informal sector firms", "business regulation", "tax compliance firms", "entry deregulation"),
    "risk": ("business resilience", "macroeconomic shocks", "corporate bankruptcy", "economic uncertainty",
             "banking risk", "credit risk", "financial crises firms"),
    "climate": ("climate risk businesses", "climate change agriculture", "disaster resilience", "heat productivity"),
    "innovation": ("entrepreneurship", "research and development firms", "startup funding", "small enterprise growth",
                   "investment climate", "economic diversification", "innovation policy"),
    "business-services": ("services trade", "professional services", "outsourcing service sector"),
    "mobility": ("urban mobility", "transport service markets", "public transit accessibility"),
    "creative": ("creative economy", "cultural industries", "urban creative services"),
    "aquaculture": ("aquaculture development", "fish farming", "seafood value chain"),
}
PAGE_SIZE = 100


def fetch_page(group: str, query: str, page: int, cutoff: date) -> tuple[list[dict], int]:
    params = urlencode({
        "query": query, "size": PAGE_SIZE, "page": page, "f.entityType": "Publication,equals",
    })
    request = Request(
        f"{BASE}/discover/search/objects?{params}",
        headers={"Accept-Encoding": "gzip", "User-Agent": "ESIS evidence snapshot/2.0"},
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=50) as response:
                stream = gzip.GzipFile(fileobj=response) if response.headers.get("Content-Encoding") == "gzip" else response
                raw = stream.read(14_000_001)
            if len(raw) > 14_000_000:
                raise ValueError("Repository response exceeded the page budget")
            result = json.loads(raw)["_embedded"]["searchResult"]
            return [
                entry
                for wrapper in result.get("_embedded", {}).get("objects", [])
                if (entry := publication(wrapper["_embedded"]["indexableObject"], group, cutoff))
                and entry["license"] and len(entry["abstract"]) >= 120
            ], result["page"]["totalPages"]
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise exc
        time.sleep(2**attempt)
    raise RuntimeError("Unreachable retry state")


def rows_at(path: Path):
    reader = gzip.open if path.suffix == ".gz" else open
    with reader(path, "rt", encoding="utf-8") as source:
        yield from (json.loads(line) for line in source)


def keys(entry: dict) -> list[tuple[str, str]]:
    result = [("id", entry["id"]), ("title", re.sub(r"\W", "", entry["title"].casefold()))]
    if entry["doi"]:
        result.append(("doi", entry["doi"].casefold()))
    return result


class Collection:
    def __init__(self, source: Path):
        self.catalog = {entry["id"]: entry for entry in rows_at(source / "publications.jsonl")}
        self.aliases = {key: entry["id"] for entry in self.catalog.values() for key in keys(entry)}
        self.passages = {item["id"]: item for item in rows_at(source / "publication_passages.jsonl.gz")}
        self.usable = {item["document_id"] for item in self.passages.values()}

    def add(self, entry: dict) -> None:
        found = next((self.aliases[key] for key in keys(entry) if key in self.aliases), entry["id"])
        if found in self.catalog:
            saved = self.catalog[found]
            saved["groups"] = sorted(set(saved["groups"]) | set(entry["groups"]))
            if found in self.usable or saved["id"] != entry["id"]:
                return
            saved.update({key: val for key, val in entry.items() if key not in {"groups", "abstract"}})
        else:
            self.catalog[found] = {key: val for key, val in entry.items() if key != "abstract"}
        for key in keys(entry):
            self.aliases[key] = found
        identifier = found + "-abstract"
        self.passages[identifier] = {
            "id": identifier, "document_id": found, "kind": "licensed_abstract", "locator": "Publisher abstract",
            "text": entry["abstract"], "content_source": entry["source"],
        }
        self.usable.add(found)

    def save(self, destination: Path, cutoff: date, queries: int, incomplete: list[str]) -> dict:
        destination.mkdir(parents=True, exist_ok=True)
        catalog = sorted(self.catalog.values(), key=lambda row: row["id"])
        evidence = sorted(self.passages.values(), key=lambda row: row["id"])
        indexed = Counter(row["document_id"] for row in evidence)
        for entry in catalog:
            entry["indexed_records"] = indexed[entry["id"]]
        files = {"publications.jsonl": catalog, "publication_passages.jsonl.gz": evidence}
        for name, records in files.items():
            write_jsonl(destination / name, records)
        summary = {
            "as_of": cutoff.isoformat(), "repository_policy": POLICY, "publication_count": len(catalog),
            "egypt_publications": sum(is_egypt(entry) for entry in catalog),
            "indexed_egypt_publications": sum(is_egypt(entry) and entry["id"] in self.usable for entry in catalog),
            "indexed_publications": len(self.usable), "evidence_records": len(evidence),
            "full_text_publications": len({row["document_id"] for row in evidence if row["kind"] == "licensed_passage"}),
            "evidence_kinds": dict(Counter(row["kind"] for row in evidence)),
            "search_requests": queries, "incomplete_queries": incomplete,
            "snapshot_sha256": {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in files},
        }
        (destination / "publication_snapshot.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--minimum", type=int, default=10_000)
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    if args.output_dir.resolve() == OUTPUT.resolve():
        parser.error("stage the expanded snapshot in a separate directory")

    collection = Collection(OUTPUT)
    queries = {
        (group, query): args.pages for group, terms in EXTRA_QUERIES.items()
        for query in (*terms, *QUERY_GROUPS.get(group, ()))
    }
    failures, request_count = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for page in range(args.pages):
            active = [(group, query) for (group, query), cap in queries.items() if page < cap]
            if not active:
                break
            jobs = {
                pool.submit(fetch_page, group, query, page, args.as_of): (group, query)
                for group, query in active
            }
            request_count += len(jobs)
            index = 0
            for job in as_completed(jobs):
                group, query = jobs[job]
                try:
                    entries, pages = job.result()
                except (OSError, KeyError, ValueError) as exc:
                    failures.append(f"{group}/{query} page {page}: {type(exc).__name__}")
                    continue
                queries[(group, query)] = min(pages, args.pages)
                for entry in entries:
                    collection.add(entry)
                index += 1
                if index % 30 == 0:
                    print(f"Page wave {page + 1}: {index}/{len(jobs)} complete; {len(collection.usable)} usable publications", flush=True)
                if len(collection.usable) >= args.minimum:
                    for remaining in jobs:
                        remaining.cancel()
                    break
            print(f"After wave {page + 1}: {len(collection.usable)} usable publications", flush=True)
            collection.save(args.output_dir, args.as_of, request_count, failures)
            if len(collection.usable) >= args.minimum:
                break

    summary = collection.save(args.output_dir, args.as_of, request_count, failures)
    print(json.dumps({key: value for key, value in summary.items() if key != "incomplete_queries"}, indent=2), flush=True)
    return 0 if len(collection.usable) >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
