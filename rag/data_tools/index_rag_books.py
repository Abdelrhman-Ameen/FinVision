"""Create a citation-ready snapshot of openly licensed textbook editions."""

import argparse
from collections import Counter
from hashlib import file_digest
import json
from pathlib import Path
import re
import subprocess

from .refresh_rag_publications import write_jsonl


ARCHIVE = "https://openlibrary-repo.ecampusontario.ca/jspui/bitstream/123456789/"
CC = "https://creativecommons.org/licenses/"
BOOKS = (
    ("food-service", "Basic Kitchen and Food Service Management", "2015", "BCcampus",
     ["The BC Cook Articulation Committee"], "by/4.0/", "Attribution 4.0",
     ARCHIVE + "522/1/Basic-Kitchen-and-Food-Service-Management-1539194230.pdf", 10,
     "food pricing costs inventory branch waste supply-disruption", "recipe costing, yield, menu engineering, purchasing"),
    ("intro-business", "Introduction to Business", "2018", "OpenStax, Rice University",
     ["Lawrence J. Gitman", "Carl McDaniel", "Amit Shah", "Monique Reece", "Linda Koffel",
      "Bethann Talsma", "James C. Hyatt"], "by/4.0/", "Attribution 4.0",
     ARCHIVE + "705/2/IntroductionToBusiness-OP_8D04gAa.pdf", 13,
     "general marketing pricing equity hiring branch inventory exports", "entrepreneurship, distribution, marketing, finance"),
    ("accounting-perspective", "Accounting Principles: A Business Perspective", "2013", "Global Text Project",
     ["Roger H. Hermanson", "James Don Edwards", "Michael W. Maher"], "by/3.0/", "Attribution 3.0",
     ARCHIVE + "425/1/Accounting%20Principles%20A%20Business%20Perspective.pdf", 9,
     "general pricing costs collections inventory funding branch", "financial accounting, cash flow, budgeting, cost-volume-profit"),
    ("venture-toolkit", "Entrepreneurship and Innovation Toolkit, third edition", "2017", "University of Saskatchewan",
     ["Lee A. Swanson"], "by-sa/4.0/", "Attribution-ShareAlike 4.0",
     ARCHIVE + "634/6/Entrepreneurship-and-Innovation-Toolkit-1568142286.pdf", 8,
     "general branch equity funding product marketing", "opportunity testing, business models, growth, financing"),
    ("management", "Principles of Management", "2019", "OpenStax, Rice University",
     ["David S. Bright", "Anastasia H. Cortes", "OpenStax contributors"], "by/4.0/", "Attribution 4.0",
     ARCHIVE + "811/2/PrinciplesofManagement-OP_mGBMvoU.pdf", 13,
     "general branch hiring productivity costs risk", "competitive environment, business strategy, organization, operating controls"),
    ("core-marketing", "Core Concepts of Marketing", "2011", "Global Text Project",
     ["John Burnett"], "by/3.0/", "Attribution 3.0",
     ARCHIVE + "312/2/Core%20Concepts%20of%20Marketing.pdf", 10,
     "marketing retail pricing branch product ecommerce", "market segmentation, customer research, pricing, products, distribution"),
    ("project-management", "Project Management", "2014", "BCcampus",
     ["Adrienne Watt"], "by/4.0/", "Attribution 4.0",
     ARCHIVE + "299/9/Project-Management-1600365270.pdf", 12,
     "risk costs training productivity supply-disruption general", "budget planning, procurement, scheduling, quality, risk management"),
    ("hospitality-hr", "Human Resources in the Food Service and Hospitality Industry", "2014", "BCcampus",
     ["go2HR", "BC Cook Articulation Committee"], "by/4.0/", "Attribution 4.0",
     ARCHIVE + "264/1/Human-Resources-in-the-Food-Service-and-Hospitality-Industry-1446147634.pdf", 9,
     "food tourism hiring training productivity", "hospitality staffing, recruitment, training, employee retention"),
    ("media-entrepreneurship", "Media Innovation and Entrepreneurship", "2018", "Rebus Community",
     ["Michelle Ferrier", "Elizabeth Mays", "contributing authors"], "by/4.0/", "Attribution 4.0",
     ARCHIVE + "509/1/Media-Innovation-and-Entrepreneurship-1539291858.pdf", 17,
     "creative software marketing equity innovation general", "business models, audience development, product validation, revenue strategies"),
    ("egypt-inequality", "Inside Inequality in the Arab Republic of Egypt : Facts and Perceptions across People, Time, and Space",
     "2014-04-02", "World Bank", ["Paolo Verme", "Branko Milanovic", "Sherine Al-Shawarby",
     "Sahar El Tawila", "May Gadallah", "Enas Ali A. El-Majeed"], "by/3.0/igo/", "Attribution 3.0 IGO",
     "https://openknowledge.worldbank.org/bitstreams/3b7fe382-9793-58f5-b6ca-c549e46fdce8/download", 13,
     "egypt regional economy costs household-consumption", "Egypt economy, inequality, household expenditure, domestic markets"),
)
ATTRIBUTION = {
    "intro-business": "Download for free at https://openstax.org/details/books/Introduction-Business.",
    "management": "Download for free at https://openstax.org/details/books/principles-management.",
}
THIRD_PARTY = re.compile(r"all rights reserved|used (?:under|with) permission|reprinted with permission|"
                         r"third.party copyright|license:|licen[cs]ed under|image (?:source|credit):|"
                         r"cover (?:image|art)|source: \s*(?:©|copyright)", re.I)
HEADER = re.compile(r"^\s*(?:\d{1,4}|(?:This OpenStax book is available.*)|(?:\d+\s+Chapter .{1,90}))\s*$")


def paragraphs(page: str):
    for paragraph in re.split(r"\n\s*\n", page):
        lines = [line.strip() for line in paragraph.splitlines() if line.strip() and not HEADER.match(line)]
        if not lines or THIRD_PARTY.search(" ".join(lines)):
            continue
        body = " ".join(lines)
        body = re.sub(r"\s+", " ", body).strip()
        if len(re.findall(r"[A-Za-z]{3,}", body)) >= 15:
            yield body


def excerpts(page: str):
    buffer = ""
    for paragraph in paragraphs(page):
        tokens = paragraph.split()
        while tokens:
            candidate = " ".join(tokens[:168])
            if buffer and len(buffer.split()) + len(candidate.split()) > 180:
                yield buffer
                buffer = ""
            buffer = " ".join(part for part in (buffer, candidate) if part)
            tokens = tokens[168:]
    if buffer:
        yield buffer


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    catalog, evidence, seen = [], [], set()
    for key, title, year, publisher, authors, license_path, proof, url, first, groups, topics in BOOKS:
        path = args.pdf_dir / (key + ".pdf")
        if not path.is_file() and args.allow_missing:
            continue
        raw = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
                             check=True, capture_output=True).stdout.decode("utf-8")
        pages = raw.split("\f")
        if not pages[-1].strip():
            pages.pop()
        if "Creative Commons " + proof not in " ".join(" ".join(pages[:7]).split()):
            raise ValueError(f"{title}: PDF front matter does not verify {proof}")
        egypt = key == "egypt-inequality"
        identity = "okr-17583" if egypt else "book-" + key
        for number, page in enumerate(pages, 1):
            if number < first:
                continue
            for position, excerpt in enumerate(excerpts(page), 1):
                if excerpt in seen:
                    continue
                seen.add(excerpt)
                evidence.append({
                    "id": f"{identity}-p{number:04d}-{position}", "document_id": identity, "kind": "licensed_passage",
                    "locator": f"PDF page {number}", "text": excerpt, "content_source": f"{url}#page={number}",
                    "pdf_page": number,
                })
        catalog.append({
            "id": identity, "title": title, "source": "https://hdl.handle.net/10986/17583" if egypt else url,
            "pdf_url": url, "published": year,
            "regions": ["Middle East and North Africa"] if egypt else ["GLOBAL"],
            "countries": ["Egypt, Arab Republic of"] if egypt else [], "authors": authors, "publisher": publisher,
            "publication_type": "Book" if egypt else "Open textbook", "license": CC + license_path, "rights_holder": publisher,
            "groups": groups.split(), "topics": topics.split(", "), "doi": None,
            "pages": len(pages), "pdf_sha256": digest(path),
            "attribution": ATTRIBUTION.get(key, ""),
        })
    counts = Counter(item["document_id"] for item in evidence)
    for book in catalog:
        book["indexed_records"] = counts[book["id"]]
        if not book["indexed_records"]:
            raise ValueError(f"No useful text: {book['title']}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = {"books.jsonl": catalog, "book_passages.jsonl.gz": evidence}
    for name, records in files.items():
        write_jsonl(args.output_dir / name, records)
    manifest = {"publication_count": len(catalog), "evidence_records": len(evidence),
                "snapshot_sha256": {name: digest(args.output_dir / name) for name in files}}
    (args.output_dir / "book_snapshot.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
