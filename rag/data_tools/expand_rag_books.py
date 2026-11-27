"""Collect page-level excerpts from verified World Bank books in the publication catalog."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from .index_rag_books import excerpts
from .refresh_rag_publications import BASE, OUTPUT, api, is_egypt, write_jsonl


TOPICS = {
    "egypt": r"egypt",
    "regional": r"middle east|north africa|arab|morocco|jordan|tunisia|gulf|mena",
    "finance": r"financ|banking|credit|invest|debt|capital market|fintech",
    "innovation": r"firm|business|entrepreneur|innovation|private sector|informality|competit",
    "food": r"food|agricultur|agrifood|farm|commodit|nutrition|fisher",
    "hiring": r"jobs|labo[u]?r|employment|workforce|workplace|skill|career",
    "exports": r"trade|export|value chain|globalization|logistic|port efficien",
    "software": r"digital|e-commerce|technology|connectivity|telecom|information|broadband",
    "costs": r"economic|macro|growth|productivity|fiscal|price|inflation",
    "energy": r"energy|power sector|electricity|water|climate|green|carbon",
    "health": r"health|pharma|medicine|pandemic",
    "construction": r"urban|cities|housing|construction|transport|infrastructure",
    "education": r"education|training|school|learning",
}
MAX_DOWNLOAD = 32 * 1024 * 1024
FIGURE = re.compile(r"^\s*(?:table|figure|photo|source:|map)\s*\d", re.I | re.M)
ENDMATTER = re.compile(r"(?mi)^ {0,12}(?:references|bibliography|index|endnotes)\s*$")


def tags(entry: dict) -> set[str]:
    text = entry["title"].casefold()
    matches = {group for group, pattern in TOPICS.items() if re.search(pattern, text)}
    if not matches:
        text = " ".join(entry["topics"]).casefold()
        matches = {group for group, pattern in TOPICS.items() if re.search(pattern, text)}
    if is_egypt(entry):
        matches.add("egypt")
    return matches


def candidates() -> list[dict]:
    with (OUTPUT / "publications.jsonl").open(encoding="utf-8") as stream:
        books = [row for row in map(json.loads, stream)
                 if row["publication_type"] == "Book" and row["license"] and row["indexed_records"]
                 and not re.search(r"little data book|overview booklet|executive summary booklet", row["title"], re.I)]
    for entry in books:
        entry["book_topics"] = sorted(tags(entry))
    books = sorted(books, key=lambda item: (
        bool(tags(item)), item["published"], item["id"],
    ), reverse=True)
    ordered = {item["id"]: item for item in books if is_egypt(item)}
    buckets = {group: iter([entry for entry in books if group in entry["book_topics"]]) for group in TOPICS}
    while buckets:
        for group, bucket in tuple(buckets.items()):
            item = next((entry for entry in bucket if entry["id"] not in ordered), None)
            if item is None:
                del buckets[group]
            else:
                ordered[item["id"]] = item
    for item in books:
        ordered.setdefault(item["id"], item)
    return list(ordered.values())


def choose_pdf(entry: dict) -> dict:
    bundles = api(f"core/items/{entry['uuid']}/bundles").get("_embedded", {}).get("bundles", [])
    original = next(bundle["uuid"] for bundle in bundles if bundle["name"] == "ORIGINAL")
    bits = api(f"core/bundles/{original}/bitstreams", size=100).get("_embedded", {}).get("bitstreams", [])
    possible = [bit for bit in bits if bit["name"].casefold().endswith(".pdf")
                and 400_000 <= bit.get("sizeBytes", 0) <= MAX_DOWNLOAD
                and not re.search(r"cover|(?:^|[_-])(?:fr|es|pt|ru|zh|ar)(?:[_\-.]|$)|summary|overview", bit["name"], re.I)]
    return next(iter(sorted(possible, key=lambda bit: bit["sizeBytes"], reverse=True)))


def download(url: str, path: Path) -> str:
    digest = hashlib.sha256()
    request = Request(url, headers={"User-Agent": "ESIS evidence snapshot/2.0"})
    with urlopen(request, timeout=45) as response, path.open("wb") as stream:
        size = 0
        while chunk := response.read(1024 * 256):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise ValueError("PDF exceeded the download limit")
            digest.update(chunk)
            stream.write(chunk)
    return digest.hexdigest()


def acquire(entry: dict, cache: Path, limit: int) -> tuple[dict, list]:
    cached = cache / (entry["id"] + ".json")
    if cached.is_file():
        saved = json.loads(cached.read_text(encoding="utf-8"))
        if saved["limit"] == limit and saved["source"]["license"] == entry["license"]:
            return saved["source"], saved["passages"]
    url = entry.get("pdf_url")
    if not url:
        bit = choose_pdf(entry)
        url = f"https://openknowledge.worldbank.org/bitstreams/{bit['uuid']}/download"
    if urlsplit(url).hostname != "openknowledge.worldbank.org":
        raise ValueError("Expected the World Bank publisher URL")
    with TemporaryDirectory(dir=cache) as temp:
        pdf = Path(temp) / "publisher.pdf"
        alternative = url.replace("/bitstreams/", "/server/api/core/bitstreams/").removesuffix("/download") + "/content"
        for attempt, address in enumerate((url, alternative, url), 1):
            try:
                digest = download(address, pdf)
                with pdf.open("rb") as stream:
                    if stream.read(5) != b"%PDF-":
                        raise ValueError("Publisher response was not a PDF")
                if entry.get("pdf_sha256") and digest != entry["pdf_sha256"]:
                    raise ValueError("Publisher PDF changed since the edition was verified")
                break
            except (OSError, ValueError):
                if attempt == 3:
                    raise
        result = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"],
                                check=True, capture_output=True, timeout=90)
    pages = result.stdout.decode("utf-8").rstrip("\f").split("\f")
    if len(pages) < 55:
        raise ValueError("The PDF is too short for the main book edition")
    license_text = " ".join(" ".join(pages[:12]).casefold().split())
    if not re.search(r"creative commons.{0,130}(?:attribution|cc by)|creativecommons\.org/licenses/by/", license_text):
        raise ValueError("Book front matter does not confirm its reuse rights")

    selected = []
    for number, page in enumerate(pages, 1):
        if number < 10 or ENDMATTER.search(page):
            continue
        for position, body in enumerate(excerpts(page), 1):
            if len(body.split()) < 36 or FIGURE.search(body):
                continue
            selected.append((number, position, body))

    unique = {}
    for number, position, body in selected:
        unique.setdefault(body, (number, position, body))
    prose = list(unique.values())
    if len(prose) < 24:
        raise ValueError("Too little searchable English prose")
    if limit and len(prose) > limit:
        prose = [prose[index * (len(prose) - 1) // (limit - 1)] for index in range(limit)]
    passages = [{
        "id": f"{entry['id']}-book-p{number:04d}-{position}", "document_id": entry["id"],
        "kind": "licensed_passage", "locator": f"PDF page {number}", "pdf_page": number,
        "content_source": f"{url}#page={number}", "text": text,
    } for number, position, text in prose]
    book = {**entry, "indexed_records": len(passages), "pages": len(pages),
            "pdf_sha256": digest, "pdf_url": url, "groups": sorted(set(entry["groups"] + entry["book_topics"]))}
    temporary = cached.with_suffix(".partial")
    temporary.write_text(json.dumps({"limit": limit, "source": book, "passages": passages}, ensure_ascii=False),
                         encoding="utf-8")
    temporary.replace(cached)
    return book, passages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum", type=int, default=108)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--excerpts", type=int, default=0, help="Maximum passages per book, or zero to retain all usable passages")
    parser.add_argument("--selection", type=Path, help="Book JSONL from a prior snapshot to rebuild directly from verified editions")
    args = parser.parse_args()
    cache = args.output_dir / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    books, passages, failures = [], [], {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        if args.selection:
            with args.selection.open(encoding="utf-8") as stream:
                queue = list(map(json.loads, stream))
        else:
            queue = candidates()
        for offset in range(0, len(queue), args.workers * 2):
            batch = {pool.submit(acquire, entry, cache, args.excerpts): entry for entry in queue[offset:offset + args.workers * 2]}
            for job in as_completed(batch):
                try:
                    book, parts = job.result()
                except (OSError, StopIteration, ValueError, KeyError, subprocess.SubprocessError) as exc:
                    failures[batch[job]["id"]] = str(exc) or type(exc).__name__
                    continue
                books.append(book)
                passages.extend(parts)
                if len(books) % 10 == 0 or is_egypt(book):
                    print(f"{len(books)} books with searchable pages: {book['title']}", flush=True)
            if not args.selection and len(books) >= args.minimum:
                break
    files = {"institutional_books.jsonl": books, "institutional_book_passages.jsonl.gz": passages}
    for name, records in files.items():
        write_jsonl(args.output_dir / name, sorted(records, key=lambda row: row["id"]))
    summary = {
        "publication_count": len(books), "evidence_records": len(passages),
        "passage_limit_per_book": args.excerpts or None,
        "egypt_books": sum(is_egypt(book) for book in books),
        "topics": dict(Counter(group for book in books for group in book["book_topics"])),
        "failed_downloads": failures,
        "snapshot_sha256": {name: hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest() for name in files},
    }
    (args.output_dir / "institutional_book_snapshot.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "failed_downloads"}, indent=2))
    return 0 if len(books) >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
