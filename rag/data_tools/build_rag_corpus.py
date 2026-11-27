"""Validate the curated evidence cards and export them as UTF-8 JSON Lines."""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2] / "data" / "knowledge_base"
KINDS = {
    "official_series", "official_methodology", "descriptive", "survey", "rct",
    "quasi_experimental", "observational", "practice",
}
TEXT_FIELDS = {
    "id", "kind", "scope", "source", "locator", "title_ar", "finding_ar",
    "application_ar", "limit_ar", "text_en",
}


def read_cards(base: Path = ROOT) -> list[dict]:
    cards_by_id = {}
    for path in sorted((base / "cards").glob("*.json")):
        cards = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cards, list) or not cards:
            raise ValueError(f"{path.name} must contain a non-empty array")
        for index, card in enumerate(cards, 1):
            context = f"{path.name}, entry {index}"
            if not isinstance(card, dict) or set(card) - {"published"} != TEXT_FIELDS | {"regions", "year", "tags"}:
                raise ValueError(f"{context}: unexpected card schema")
            if any(not isinstance(card[key], str) or not card[key].strip() for key in TEXT_FIELDS):
                raise ValueError(f"{context}: a required text field is blank")
            card_id = card["id"]
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", card_id) or card_id in cards_by_id:
                raise ValueError(f"{context}: invalid or duplicate ID {card_id}")
            link = urlsplit(card["source"])
            if link.scheme != "https" or not link.hostname:
                raise ValueError(f"{context}: expected a primary source HTTPS URL")
            if card["kind"] not in KINDS:
                raise ValueError(f"{context}: invalid evidence kind")
            year = card["year"]
            if year is not None and (type(year) is not int or not 1900 <= year <= 2100):
                raise ValueError(f"{context}: invalid source year")
            if "published" in card and not re.fullmatch(
                rf"{year}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12][0-9]|3[01]))?", str(card["published"])
            ):
                raise ValueError(f"{context}: invalid publication date")
            for key in ("regions", "tags"):
                values = card[key]
                if not isinstance(values, list) or not values or any(
                    not isinstance(value, str) or not value.strip() for value in values
                ):
                    raise ValueError(f"{context}: expected a non-empty {key} list")
            cards_by_id[card_id] = card
    if not cards_by_id:
        raise ValueError("No evidence cards found")

    cases = json.loads((base / "evaluation_questions.json").read_text(encoding="utf-8"))
    for case in cases:
        ids = case["evidence_ids"]
        if not ids or set(ids) - cards_by_id.keys():
            raise ValueError(f"Invalid evidence references in evaluation case: {case['id']}")
    return list(cards_by_id.values())


def export_corpus(cards: list[dict], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for card in cards:
            text = "\n".join(
                (
                    card["title_ar"],
                    "النتيجة: " + card["finding_ar"],
                    "استخدامها: " + card["application_ar"],
                    "حدودها: " + card["limit_ar"],
                    "Scope: " + card["scope"],
                    card["text_en"],
                    " ".join(card["tags"]),
                )
            )
            stream.write(json.dumps({**card, "text": text}, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a retrieval-ready JSONL file")
    args = parser.parse_args()
    try:
        cards = read_cards()
        if args.output:
            export_corpus(cards, args.output)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Corpus validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Validated {len(cards)} evidence cards." + (f" Wrote {args.output}." if args.output else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
