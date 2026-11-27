"""Build and query the local evidence index and the dated economic series."""

import argparse
from collections import Counter
from contextlib import closing
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import NamedTemporaryFile
import unicodedata

from .build_rag_corpus import ROOT, read_cards


DATA = ROOT / "data"
LICENSES = {"https://creativecommons.org/licenses/by/3.0/igo/", "https://creativecommons.org/licenses/by/4.0/"}
BOOK_LICENSES = LICENSES | {"https://creativecommons.org/licenses/by/3.0/",
                            "https://creativecommons.org/licenses/by-sa/4.0/"}
COUNTRY_TERMS = {
    "EGY": "egypt", "DZA": "algeria", "MAR": "morocco", "TUN": "tunisia",
    "JOR": "jordan", "LBN": "lebanon", "IRQ": "iraq", "SAU": "saudi arabia",
    "ARE": "united arab emirates", "OMN": "oman", "QAT": "qatar", "KWT": "kuwait",
    "BHR": "bahrain", "TUR": "turk", "KEN": "kenya", "NGA": "nigeria",
    "ZAF": "south africa", "PAK": "pakistan", "IND": "india", "IDN": "indonesia",
    "VNM": "viet nam", "BRA": "brazil", "MEX": "mexico",
}
MENA = {"EGY", "DZA", "MAR", "TUN", "JOR", "LBN", "IRQ", "SAU", "ARE", "OMN", "QAT", "KWT", "BHR"}
ARABIC = str.maketrans("أإآٱىةـ", "اااايه ")
STOP_WORDS = frozenset(
    "a an and are at by for from how i in is it of on or our the to was what when where which with would "
    "ازاي ايه انه ان عند عن ف في فيه لو هل اللي ده دي ديه من كان عام حتى لما بتاع عندي علي او و يعني كام حسب".split()
)
POSSESSIVE_TERMS = {
    "تسعيري": "تسعير", "سعري": "سعر", "ارباحي": "ارباح", "ربحي": "ربح",
    "تكلفتي": "تكلفه", "هامشي": "هامش", "عوائدي": "عوائد",
    "مصاريفي": "مصاريف", "مبيعاتي": "مبيعات", "مخزوني": "مخزون",
    "سيولتي": "سيوله", "فواتيري": "فواتير", "اشتراكي": "اشتراك", "لسعر": "سعر",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_lines(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def words(value: str) -> list[str]:
    text = unicodedata.normalize("NFKC", value).translate(ARABIC).casefold()
    text = re.sub(r"[\u064B-\u065F\u0670]", "", text)
    text = re.sub(r"\ba\s*/\s*b\b", " ab ", text)
    tokens = []
    for word in re.findall(r"[^\W_]+", text):
        if len(word) > 4 and word.startswith(("وال", "بال", "لل")):
            word = word[2:] if word.startswith("لل") else word[3:]
        elif len(word) > 4 and word.startswith("ال"):
            word = word[2:]
        word = POSSESSIVE_TERMS.get(word, word)
        if len(word) > 1 and word not in STOP_WORDS:
            tokens.append(word)
    return tokens


def searchable(value: str) -> str:
    return " ".join(words(value))


def card_record(card: dict) -> dict:
    return {
        "id": card["id"], "document_id": card["id"], "kind": card["kind"], "origin": "curated",
        "title": card["title_ar"], "source": card["source"], "locator": card["locator"],
        "published": card.get("published") or (str(card["year"]) if card["year"] else None),
        "regions": card["regions"], "groups": [], "terms": card["tags"],
        "scope": card["scope"], "finding_ar": card["finding_ar"],
        "application_ar": card["application_ar"], "limit_ar": card["limit_ar"],
        "text": "\n".join((card["finding_ar"], card["application_ar"], card["limit_ar"], card["text_en"], card["scope"])),
    }


def publisher_record(passage: dict, publication: dict) -> dict:
    return {
        **passage, "origin": "publisher",
        "title": publication["title"],
        "source": passage["content_source"] if publication["publication_type"] == "Open textbook"
        or passage.get("pdf_page") else publication["source"],
        "published": publication["published"],
        "regions": publication["regions"], "countries": publication["countries"],
        "authors": publication["authors"], "publisher": publication["publisher"],
        "publication_type": publication["publication_type"], "doi": publication["doi"],
        "license": publication["license"], "rights_holder": publication["rights_holder"],
        "groups": publication["groups"], "terms": publication["topics"],
        "modifications": "Excerpted; layout and whitespace normalized." + (
            f" {publication['attribution']}" if publication.get("attribution") else ""
        ),
    }


def validated_inputs():
    card_list = read_cards(ROOT)
    cards = {card["id"]: card for card in card_list}
    sectors = load_json(ROOT / "coverage" / "sectors.json")
    scenarios = load_json(ROOT / "coverage" / "scenarios.json")
    scenario_ids = {item["id"] for item in scenarios}
    for entries in (sectors, scenarios):
        if len({item["id"] for item in entries}) != len(entries):
            raise ValueError("Duplicate coverage IDs")
        for item in entries:
            if not item["evidence_ids"] or set(item["evidence_ids"]) - cards.keys():
                raise ValueError(f"Missing curated evidence for {item['id']}")
            if set(item.get("priority_scenarios", [])) - scenario_ids:
                raise ValueError(f"Unknown scenario in {item['id']}")

    catalog, passages = published_records("publication", "publications", LICENSES)
    book_catalog, book_passages = published_records("book", "books", BOOK_LICENSES)
    for identity in catalog.keys() & book_catalog.keys():
        existing, book = catalog[identity], book_catalog[identity]
        if existing["indexed_records"] or existing["title"] != book["title"] or existing["source"] != book["source"]:
            raise ValueError(f"Book ID collides with an unrelated or already indexed publication: {identity}")
    if (DATA / "institutional_book_snapshot.json").is_file():
        institutional, parts = published_records("institutional_book", "institutional_books", LICENSES)
        for identity, book in institutional.items():
            previous = catalog[identity]
            if (book["publication_type"] != "Book" or book["source"] != previous["source"]
                    or book["title"] != previous["title"] or book["license"] != previous["license"]):
                raise ValueError(f"Book edition does not match the publication catalog: {identity}")
        catalog.update(institutional)
        book_passages.extend(parts)
    return cards, sectors, scenarios, catalog | book_catalog, passages + book_passages


def published_records(prefix: str, catalog_name: str, licenses: set[str]) -> tuple[dict, list]:
    snapshot = load_json(DATA / f"{prefix}_snapshot.json")
    for name, expected in snapshot["snapshot_sha256"].items():
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Publication snapshot hash mismatch for {name}")
    catalog = {}
    for publication in json_lines(DATA / f"{catalog_name}.jsonl"):
        identity = publication["id"]
        if identity in catalog:
            raise ValueError(f"Duplicate document ID {identity}")
        catalog[identity] = publication
    if len(catalog) != snapshot["publication_count"]:
        raise ValueError("Publication count does not match snapshot")
    passages, ids, per_document = [], set(), Counter()
    for passage in json_lines(DATA / f"{prefix}_passages.jsonl.gz"):
        publication = catalog[passage["document_id"]]
        if publication["license"] not in licenses or not publication["rights_holder"]:
            raise ValueError(f"Unverified evidence license for {passage['id']}")
        if passage["id"] in ids or passage["kind"] not in {"licensed_abstract", "licensed_passage"}:
            raise ValueError(f"Duplicate or invalid evidence ID {passage['id']}")
        ids.add(passage["id"])
        per_document[publication["id"]] += 1
        passages.append(publisher_record(passage, publication))
    if len(passages) != snapshot["evidence_records"] or any(
        publication["indexed_records"] != per_document[identity] for identity, publication in catalog.items()
    ):
        raise ValueError("Publication passage counts do not match the catalog")
    return catalog, passages


def geography_boost(record: dict, country: str) -> float:
    names = [name.casefold() for name in record["regions"] + record.get("countries", [])]
    country_name = COUNTRY_TERMS.get(country)
    if country.casefold() in names or (country_name and any(country_name in name for name in names)):
        return 1.28
    if country in MENA and any(
        name == "mena" or "middle east" in name or "north africa" in name for name in names
    ):
        return 1.12
    return 1.0


def build_index(destination: Path) -> dict:
    cards, sectors, scenarios, catalog, passages = validated_inputs()
    metadata = load_json(DATA / "indicator_snapshot.json")
    observation_file = DATA / "annual_observations.jsonl.gz"
    if hashlib.sha256(observation_file.read_bytes()).hexdigest() != metadata["observations_sha256"]:
        raise ValueError("Indicator snapshot hash mismatch")
    indicator_codes = {series["id"] for series in metadata["series"]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, prefix=destination.name + ".", suffix=".partial", delete=False) as temp:
        partial = Path(temp.name)
    try:
        with closing(sqlite3.connect(partial)) as db, db:
            db.executescript(
                """
                CREATE TABLE evidence (id TEXT UNIQUE, document_id TEXT, payload TEXT);
                CREATE INDEX evidence_by_source ON evidence (document_id, id);
                CREATE VIRTUAL TABLE evidence_fts USING fts5(title, terms, body, tokenize='unicode61');
                CREATE TABLE observations (
                    indicator TEXT, country TEXT, year INTEGER, value REAL, payload TEXT,
                    PRIMARY KEY (indicator, country, year)
                ) WITHOUT ROWID;
                CREATE TABLE config (name TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE source_topics (
                    topic TEXT, document_id TEXT,
                    PRIMARY KEY (topic, document_id)
                ) WITHOUT ROWID;
                """
            )
            for item in [*(card_record(card) for card in cards.values()), *passages]:
                cursor = db.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?)",
                    (item["id"], item["document_id"], json.dumps(item, ensure_ascii=False)),
                )
                terms = " ".join(item["terms"] + item["groups"] + item.get("countries", []))
                db.execute(
                    "INSERT INTO evidence_fts(rowid, title, terms, body) VALUES (?, ?, ?, ?)",
                    (cursor.lastrowid, searchable(item["title"]), searchable(terms), searchable(item["text"])),
                )
            connections = set()
            indexed_ids = {item["document_id"] for item in passages}
            for document_id in indexed_ids:
                source = catalog[document_id]
                connections.update((f"topic:{group}", document_id) for group in source["groups"])
                connections.update((f"region:{region}", document_id) for region in source["regions"])
                connections.update((f"country:{country}", document_id) for country in source["countries"])
                if source.get("pdf_sha256"):
                    connections.add(("type:book-text", document_id))
                    if any("egypt" in country.casefold() for country in source["countries"]):
                        connections.add(("type:egypt-book", document_id))
            for sector in sectors:
                connections.update((f"topic:{sector['id']}", identity) for identity in sector["evidence_ids"])
            for scenario in scenarios:
                connections.update((f"scenario:{scenario['id']}", identity) for identity in scenario["evidence_ids"])
                groups = set(scenario["groups"])
                connections.update(
                    (f"scenario:{scenario['id']}", identity) for identity in indexed_ids
                    if groups.intersection(catalog[identity]["groups"])
                )
            db.executemany("INSERT INTO source_topics VALUES (?, ?)", sorted(connections))
            count = 0
            for row in json_lines(observation_file):
                if row["indicator"] not in indicator_codes or row["country"] not in metadata["countries"]:
                    raise ValueError(f"Invalid indicator provenance in {row}")
                if isinstance(row["value"], bool) or not math.isfinite(row["value"]):
                    raise ValueError(f"Invalid indicator value in {row}")
                db.execute(
                    "INSERT INTO observations VALUES (?, ?, ?, ?, ?)",
                    (row["indicator"], row["country"], row["year"], row["value"], json.dumps(row, ensure_ascii=False)),
                )
                count += 1
            if count != metadata["observations"]:
                raise ValueError("Incomplete indicator snapshot")
            db.executemany(
                "INSERT INTO config VALUES (?, ?)",
                [(name, json.dumps(value, ensure_ascii=False)) for name, value in (
                    ("sectors", sectors), ("scenarios", scenarios), ("indicator_snapshot", metadata),
                )],
            )
        partial.replace(destination)
    finally:
        if partial.exists():
            partial.unlink()
    return {
        "curated_cards": len(cards), "catalog_publications": len(catalog),
        "indexed_publications": len({passage["document_id"] for passage in passages}),
        "indexed_passages": len(passages), "scenarios": len(scenarios), "sectors": len(sectors),
        "indicators": len(indicator_codes), "observations": count, "source_relationships": len(connections),
    }


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def config(db: sqlite3.Connection, name: str):
    return json.loads(db.execute("SELECT payload FROM config WHERE name = ?", (name,)).fetchone()[0])


def select_context(db: sqlite3.Connection, sector: str | None, scenario: str | None) -> tuple[dict, dict]:
    sectors = config(db, "sectors") if sector else []
    scenarios = config(db, "scenarios") if scenario else []
    selected_sector = next((item for item in sectors if item["id"] == sector), {})
    selected_scenario = next((item for item in scenarios if item["id"] == scenario), {})
    if (sector and not selected_sector) or (scenario and not selected_scenario):
        raise ValueError("Unknown sector or scenario ID")
    return selected_sector, selected_scenario


def rank(db: sqlite3.Connection, query: str, *, sector: str | None = None,
         scenario: str | None = None, limit: int = 8, country: str = "EGY",
         book_search: bool = False, egypt_books: bool = False) -> list[dict]:
    selected_sector, selected_scenario = select_context(db, sector, scenario)
    # Percentages in what-if questions are usually scenario inputs, not search terms.
    subject = re.sub(r"\b\d+(?:[.,٫]\d+)?\s*[%٪]", " ", query)
    raw = list(dict.fromkeys(words(subject)))[:24]
    if not raw:
        return []
    groups = set(selected_scenario.get("groups", []))
    if sector:
        groups.add(sector)
    source_ids = set(selected_sector.get("evidence_ids", []) + selected_scenario.get("evidence_ids", []))
    expansion = list(dict.fromkeys(words(" ".join(sorted(groups) + [
        selected_sector.get("title_en", ""), selected_scenario.get("title_en", ""),
    ]))))
    seeds = raw + [term for term in expansion if term not in raw][:10]
    pattern = " OR ".join(f'"{word}"' for word in seeds)
    scope = ""
    if book_search:
        scope = """AND e.document_id IN (SELECT document_id FROM source_topics WHERE topic = 'type:book-text')
                   AND json_extract(e.payload, '$.kind') = 'licensed_passage'"""
    if egypt_books:
        scope += " AND e.document_id IN (SELECT document_id FROM source_topics WHERE topic = 'type:egypt-book')"
    rows = db.execute(
        f"""WITH matches AS MATERIALIZED (
               SELECT e.rowid AS evidence_row, e.document_id, -bm25(evidence_fts, 5.0, 2.7, 1.0) AS text_score
               FROM evidence_fts JOIN evidence AS e ON e.rowid = evidence_fts.rowid
               WHERE evidence_fts MATCH ? {scope}
               ORDER BY text_score DESC LIMIT 2200
           ), ranked AS (
               SELECT evidence_row, text_score,
                      ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY text_score DESC, evidence_row) AS hit
               FROM matches
           )
           SELECT e.payload, ranked.text_score
           FROM ranked JOIN evidence AS e ON e.rowid = ranked.evidence_row
           WHERE ranked.hit <= 2 ORDER BY ranked.text_score DESC LIMIT 320""",
        (pattern,),
    )
    best = {}
    for payload, text_score in rows:
        item = json.loads(payload)
        matched = len(set(raw) & set(words(item["title"] + " " + item["text"] + " " + " ".join(item["terms"]))))
        boost = 1 + 0.10 * matched
        if item["origin"] == "curated":
            boost *= 1.30
        if item["id"] in source_ids:
            boost *= 1.65
        if groups & set(item["groups"]):
            boost *= 1.18
        boost *= geography_boost(item, country)
        score = text_score * boost
        if item["document_id"] not in best or score > best[item["document_id"]][0]:
            best[item["document_id"]] = score, {**item, "matched_query_terms": matched, "search_score": score}
    return [item for _, item in sorted(best.values(), key=lambda result: (-result[0], result[1]["id"]))[:limit]]


def lookup_indicator(db: sqlite3.Connection, indicator: str, country: str, year: int | None) -> dict:
    rows = db.execute(
        """SELECT payload FROM observations WHERE indicator = ? AND country = ?
           AND (? IS NULL OR year = ?) ORDER BY year DESC LIMIT 1""",
        (indicator, country, year, year),
    ).fetchone()
    if not rows:
        raise ValueError("No dated observation for that indicator, country and year")
    metadata = config(db, "indicator_snapshot")
    value = json.loads(rows[0])
    series = next(item for item in metadata["series"] if item["id"] == indicator)
    return {**value, "name": series["name"], "source_organization": series["source_organization"],
            "snapshot_as_of": metadata["retrieved_for"], "source_last_updated": series["last_updated"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "esis_index.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--sector")
    search.add_argument("--scenario")
    search.add_argument("--country", default="EGY")
    search.add_argument("--limit", type=int, default=8, choices=range(1, 21), metavar="1..20")
    indicator = sub.add_parser("indicator")
    indicator.add_argument("code")
    indicator.add_argument("--country", default="EGY")
    indicator.add_argument("--year", type=int)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build_index(args.index)
        else:
            with closing(connect_readonly(args.index)) as db:
                if args.command == "indicator":
                    result = lookup_indicator(db, args.code, args.country.upper(), args.year)
                else:
                    sector, scenario = select_context(db, args.sector, args.scenario)
                    ranked = rank(db, args.query, sector=args.sector, scenario=args.scenario,
                                  limit=args.limit, country=args.country.upper())
                    result = {"sector": sector or None, "scenario": scenario or None, "evidence": ranked}
    except (OSError, KeyError, ValueError, sqlite3.Error) as exc:
        print(f"Knowledge library: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
