"""Snapshot primary publications with documented reuse rights from World Bank OKR."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


BASE = "https://openknowledge.worldbank.org/server/api"
POLICY = "https://www.worldbank.org/ext/en/legal/terms-conditions/open-knowledge-repository"
OUTPUT = Path(__file__).resolve().parents[2] / "data" / "knowledge_base" / "data"
ALLOWED_LICENSES = {
    "/licenses/by/3.0/igo": "https://creativecommons.org/licenses/by/3.0/igo/",
    "/licenses/by/4.0": "https://creativecommons.org/licenses/by/4.0/",
}
QUERY_GROUPS = {
    "egypt": ('"Egypt"', 'Egypt AND "private sector"', 'Egypt AND "economic growth"'),
    "regional": ('"Middle East and North Africa" AND firms', '"North Africa" AND trade', '"Arab countries" AND finance'),
    "agriculture": ('Egypt AND agriculture', '"agricultural value chains"', 'irrigation AND agriculture'),
    "food": ('Egypt AND "food security"', '"food processing"', '"cold chain"'),
    "aquaculture": ('Egypt AND aquaculture', 'aquaculture AND markets'),
    "retail": ('"retail firms"', '"small retailers"', '"consumer markets"'),
    "ecommerce": ('"e-commerce" AND "Middle East"', 'e-commerce AND "small firms"'),
    "software": ('Egypt AND digital', '"digital businesses"', '"ICT services"'),
    "telecom": ('Egypt AND broadband', '"broadband affordability"'),
    "fintech": ('"digital payments" AND "Middle East"', '"financial technology"', '"digital financial services"'),
    "health": ('Egypt AND healthcare', '"health financing"', '"private health sector"'),
    "pharma": ('pharmaceutical AND Egypt', '"pharmaceutical supply chains"'),
    "education": ('Egypt AND "vocational education"', '"education technology"', '"vocational training" AND firms'),
    "tourism": ('Egypt AND tourism', 'tourism AND "small businesses"'),
    "logistics": ('Egypt AND logistics', '"trade logistics"', '"freight transport"'),
    "manufacturing": ('Egypt AND manufacturing', '"manufacturing firms" AND productivity'),
    "textiles": ('Egypt AND textile', 'apparel AND firms'),
    "construction": ('Egypt AND construction', 'infrastructure AND contractors'),
    "real-estate": ('Egypt AND "affordable housing"', '"urban housing markets"'),
    "energy": ('Egypt AND energy', '"energy efficiency" AND firms', '"renewable energy" AND Egypt'),
    "water": ('Egypt AND water', '"industrial water use"'),
    "waste": ('Egypt AND "solid waste"', 'recycling AND investment'),
    "creative": ('"creative industries"', '"creative economy" AND jobs'),
    "business-services": ('"business process outsourcing"', '"services exports" AND Egypt'),
    "mobility": ('Egypt AND "urban transport"', '"urban mobility" AND Africa'),
    "working-capital": ('"working capital" AND firms', '"trade credit" AND firms', '"late payments"'),
    "pricing": ('"pricing" AND "small firms"', '"price elasticity" AND consumers', '"market power" AND firms'),
    "costs": ('Egypt AND "input prices"', '"cost increases" AND firms', '"exchange rate pass-through"'),
    "finance": ('"SME finance" AND Egypt', '"small firms" AND credit', '"business grants" AND firms'),
    "exports": ('Egypt AND exporters', '"export promotion" AND firms', '"exporting" AND "firm performance"'),
    "marketing": ('"marketing training"', '"customer acquisition"', '"business practices" AND firms'),
    "training": ('"business training"', '"training" AND "firm performance"', '"personal initiative"'),
    "hiring": ('Egypt AND employment', '"hiring" AND firms', '"wage subsidies" AND employment'),
    "productivity": ('"management practices" AND firms', '"technology adoption" AND firms', '"firm productivity"'),
    "formalization": ('"small firms" AND formalization', '"informal firms" AND registration'),
    "risk": ('Egypt AND "economic shocks"', '"business resilience" AND firms', '"firm survival"'),
    "climate": ('Egypt AND climate', '"climate risks" AND firms', '"heat stress" AND workers'),
    "innovation": ('Egypt AND innovation', '"entrepreneurship" AND "small firms"', '"venture capital" AND startups'),
}


def request(url: str, max_bytes: int) -> bytes:
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "ESIS evidence snapshot/1.0"})
            with urlopen(req, timeout=20) as response:
                result = response.read(max_bytes + 1)
            if len(result) > max_bytes:
                raise ValueError("Remote item exceeds the byte budget")
            return result
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2**attempt)
    raise RuntimeError("Unreachable request state")


def api(path: str, **parameters) -> dict:
    suffix = "?" + urlencode(parameters) if parameters else ""
    return json.loads(request(f"{BASE}/{path}{suffix}", 12_000_000))


def clean(value: str) -> str:
    return " ".join(value.replace("\u00ad", "").split())


def metadata_values(metadata: dict, name: str) -> list[str]:
    return [clean(field["value"]) for field in metadata.get(name, []) if field.get("value")]


def first(metadata: dict, name: str) -> str:
    return next(iter(metadata_values(metadata, name)), "")


def publication(item: dict, group: str, cutoff: date) -> dict | None:
    metadata = item.get("metadata", {})
    issued = first(metadata, "dc.date.issued")
    if not re.fullmatch(r"(?:19|20)\d\d(?:-\d\d(?:-\d\d)?)?", issued):
        return None
    if not 2000 <= int(issued[:4]) <= cutoff.year or issued > cutoff.isoformat():
        return None
    handle = item.get("handle", "")
    if not re.fullmatch(r"10986/\d+", handle):
        return None
    title = first(metadata, "okr.crossref.title") or first(metadata, "dc.title")
    if len(title) < 12:
        return None
    language = metadata_values(metadata, "okr.language.supported") or metadata_values(metadata, "dc.language.iso")
    if language and not any(lang.lower().startswith(("en", "ar")) for lang in language):
        return None
    holder = first(metadata, "dc.rights.holder")
    rights_url = first(metadata, "dc.rights.uri")
    parsed = urlsplit(rights_url)
    license_url = (
        ALLOWED_LICENSES.get(parsed.path.rstrip("/"))
        if parsed.hostname in {"creativecommons.org", "www.creativecommons.org"} else None
    )
    rights_holders = {"world bank", "world bank group", "the world bank", "the world bank group"}
    if holder.casefold() not in rights_holders or len(metadata_values(metadata, "dc.rights.uri")) != 1:
        license_url = None
    countries = metadata_values(metadata, "okr.region.country")
    regions = metadata_values(metadata, "okr.region.administrative")
    if re.search(r"\begypt(?:ian)?\b", title, re.IGNORECASE) and "Egypt, Arab Republic of" not in countries:
        countries.append("Egypt, Arab Republic of")
    return {
        "id": f"okr-{handle.split('/')[1]}",
        "uuid": item["uuid"],
        "title": title,
        "source": f"https://hdl.handle.net/{handle}",
        "publication_type": first(metadata, "dc.type"),
        "published": issued,
        "authors": metadata_values(metadata, "dc.contributor.author"),
        "publisher": first(metadata, "dc.publisher") or "World Bank",
        "countries": sorted(set(countries)),
        "regions": sorted(set(regions)),
        "topics": metadata_values(metadata, "dc.subject")[:16],
        "groups": [group],
        "doi": first(metadata, "dc.identifier.doi"),
        "license": license_url,
        "publisher_rights": first(metadata, "dc.rights"),
        "rights_holder": holder,
        "abstract": first(metadata, "dc.description.abstract"),
    }


def search(query: str, group: str, rows: int, cutoff: date) -> list[dict]:
    data = api("discover/search/objects", query=query, size=rows, **{"f.entityType": "Publication,equals"})
    results = data.get("_embedded", {}).get("searchResult", {})
    return [
        entry
        for record in results.get("_embedded", {}).get("objects", [])
        if (entry := publication(record["_embedded"]["indexableObject"], group, cutoff)) is not None
    ]


def unique_publications(groups: list[list[dict]]) -> list[dict]:
    entries = {}
    aliases = {}
    for results in groups:
        for entry in results:
            keys = [entry["id"], re.sub(r"\W", "", entry["title"].casefold())]
            if entry["doi"]:
                keys.append(entry["doi"].casefold())
            root = next((aliases[key] for key in keys if key in aliases), entry["id"])
            for key in keys:
                aliases[key] = root
            if root not in entries:
                entries[root] = entry
            else:
                entries[root]["groups"] = sorted(set(entries[root]["groups"] + entry["groups"]))
    return sorted(entries.values(), key=lambda entry: entry["id"])


def is_egypt(entry: dict) -> bool:
    return any("egypt" in country.casefold() for country in entry["countries"])


def select_full_text(entries: list[dict], budget: int) -> list[dict]:
    ranked = sorted(
        entries,
        key=lambda entry: (is_egypt(entry), bool(entry["published"] >= "2018"), len(entry["groups"]), entry["published"]),
        reverse=True,
    )
    selected = {}
    for index in range(budget):
        group = list(QUERY_GROUPS)[index % len(QUERY_GROUPS)]
        candidate = next((entry for entry in ranked if group in entry["groups"] and entry["id"] not in selected), None)
        candidate = candidate or next((entry for entry in ranked if entry["id"] not in selected), None)
        if candidate is None:
            break
        selected[candidate["id"]] = candidate
    return list(selected.values())


def chapters(raw: str) -> list[tuple[str, str]]:
    # Index prose with a conservative character budget; skip tables and reference lists.
    raw = re.sub(r"-\s*\r?\n\s*(?=[a-z])", "", raw)
    pieces = re.split(r"\n\s*\n|\f", raw)
    heading, kept = "Publisher text", []
    for piece in pieces:
        paragraph = clean(piece)
        if re.fullmatch(r"(?i)(?:\d+[.]?\s+)?(?:bibliography|references|references cited)", paragraph) and len(kept) > 8:
            break
        title = re.fullmatch(
            r"(?i)(?:\d+(?:\.\d+)*[.)]?\s+)?(executive summary|introduction|background|methodology|"
            r"results(?: and discussion)?|findings|discussion|conclusions?|policy implications|recommendations)",
            paragraph,
        )
        if title:
            heading = title.group(1).title()
        elif 140 <= len(paragraph) <= 9000 and len(paragraph.split()) >= 25 and re.search(r"[.!?][”\"]?$", paragraph):
            if len(re.findall(r"[0-9]", paragraph)) > len(paragraph) // 8 or paragraph.count("©"):
                continue
            kept.append((heading, paragraph))
    return kept


def segments(paragraphs: list[tuple[str, str]], limit: int = 38) -> list[tuple[str, str]]:
    parts, current = [], []
    prior, length = "", 0
    for heading, paragraph in paragraphs:
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\u0600-\u06ff])", paragraph):
            if len(sentence) > 1800:
                continue
            if current and (heading != prior or length + len(sentence) > 1400):
                if length >= 220:
                    parts.append((prior, " ".join(current)))
                current, length = [], 0
            current.append(sentence)
            prior, length = heading, length + len(sentence) + 1
    if length >= 220:
        parts.append((prior, " ".join(current)))
    if len(parts) > limit:
        return [parts[index * (len(parts) - 1) // (limit - 1)] for index in range(limit)]
    return parts


def full_text(entry: dict) -> tuple[str, list[tuple[str, str]]] | None:
    bundles = api(f"core/items/{entry['uuid']}/bundles").get("_embedded", {}).get("bundles", [])
    original = next((bundle["uuid"] for bundle in bundles if bundle["name"] == "ORIGINAL"), None)
    if not original:
        return None
    bits = api(f"core/bundles/{original}/bitstreams").get("_embedded", {}).get("bitstreams", [])
    bit = next(
        (
            item for item in bits
            if item["name"].casefold().endswith(".txt") and 2000 <= item.get("sizeBytes", 0) <= 2_000_000
        ),
        None,
    )
    if not bit:
        return None
    url = f"{BASE}/core/bitstreams/{bit['uuid']}/content"
    raw = request(url, 2_000_000).decode("utf-8", errors="replace")
    if raw.lstrip().lower().startswith(("<html", "<!doctype", "<?xml")):
        return None
    results = segments(chapters(raw))
    return (url, results) if results else None


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    blob = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    temporary.write_bytes(gzip.compress(blob, mtime=0) if path.suffix == ".gz" else blob)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--rows-per-query", type=int, default=30, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--full-text-budget", type=int, default=85)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    queries = [(query, group) for group, terms in QUERY_GROUPS.items() for query in terms]
    catalog, evidence, errors = [], [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [(query, pool.submit(search, query, group, args.rows_per_query, args.as_of)) for query, group in queries]
        matches = []
        for query, job in futures:
            try:
                matches.append(job.result())
            except (OSError, ValueError, KeyError) as exc:
                errors.append(f"Search {query}: {exc}")
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        catalog = unique_publications(matches)
        reusable = [entry for entry in catalog if entry["license"]]
        if not reusable:
            raise RuntimeError("No explicitly licensed publications found")
        for entry in reusable:
            if len(entry["abstract"]) >= 100:
                evidence.append({
                    "id": entry["id"] + "-abstract", "document_id": entry["id"],
                    "kind": "licensed_abstract", "locator": "Publisher abstract",
                    "text": entry["abstract"], "content_source": entry["source"],
                })
        targets = select_full_text(reusable, args.full_text_budget)
        jobs = [(entry, pool.submit(full_text, entry)) for entry in targets]
        for entry, job in jobs:
            try:
                found = job.result()
            except (OSError, ValueError, KeyError) as exc:
                errors.append(f"{entry['id']}: {exc}")
                continue
            if found:
                url, passages = found
                evidence.extend(
                    {"id": f"{entry['id']}-p{index:03}", "document_id": entry["id"],
                     "kind": "licensed_passage", "locator": f"{heading}; text excerpt {index}",
                     "text": paragraph, "content_source": url}
                    for index, (heading, paragraph) in enumerate(passages, 1)
                )

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    statuses = Counter(item["document_id"] for item in evidence)
    for entry in catalog:
        entry.pop("abstract")
        entry["indexed_records"] = statuses[entry["id"]]
    write_jsonl(output / "publications.jsonl", catalog)
    write_jsonl(output / "publication_passages.jsonl.gz", evidence)
    summary = {
        "as_of": args.as_of.isoformat(), "repository_policy": POLICY,
        "publication_count": len(catalog), "search_queries": len(queries), "rows_per_query": args.rows_per_query,
        "egypt_publications": sum(is_egypt(entry) for entry in catalog),
        "indexed_publications": len(statuses), "evidence_records": len(evidence),
        "evidence_kinds": dict(Counter(item["kind"] for item in evidence)),
        "full_text_publications": len({item["document_id"] for item in evidence if item["kind"] == "licensed_passage"}),
        "full_text_attempted": len(targets), "full_text_errors": errors,
        "snapshot_sha256": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in ("publications.jsonl", "publication_passages.jsonl.gz")
        },
    }
    (output / "publication_snapshot.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
