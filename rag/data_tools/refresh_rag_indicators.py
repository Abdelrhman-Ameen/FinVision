"""Fetch dated World Development Indicators for Egypt and comparator economies."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

from .refresh_rag_publications import OUTPUT, request, write_jsonl


BASE = "https://api.worldbank.org/v2"
COUNTRIES = (
    "EGY", "DZA", "MAR", "TUN", "JOR", "LBN", "IRQ", "SAU", "ARE", "OMN", "QAT", "KWT",
    "BHR", "TUR", "KEN", "NGA", "ZAF", "PAK", "IND", "IDN", "VNM", "BRA", "MEX",
)
CONFIG = OUTPUT.parent / "coverage" / "wdi_indicators.json"


def fetch(path: str, **parameters) -> list:
    url = f"{BASE}/{path}?" + urlencode({"format": "json", "source": 2, **parameters})
    data = json.loads(request(url, 5_000_000))
    if not isinstance(data, list) or len(data) != 2 or not isinstance(data[1], list):
        raise ValueError(f"Unexpected World Bank API response for {path}")
    return data


def series(spec: dict, start: int, end: int) -> tuple[dict, list[dict]]:
    indicator = spec["id"]
    _, definitions = fetch(f"indicator/{indicator}")
    if not definitions:
        raise ValueError(f"Missing definition for {indicator}")
    definition = definitions[0]
    path = f"country/{';'.join(COUNTRIES)}/indicator/{indicator}"
    metadata, observations = fetch(path, date=f"{start}:{end}", per_page=1000)
    if metadata["pages"] != 1:
        raise ValueError(f"Incomplete page for {indicator}")
    records = []
    for row in observations:
        if row["countryiso3code"] not in COUNTRIES or row["value"] is None:
            continue
        country = row["countryiso3code"]
        year = int(row["date"])
        records.append({
            "indicator": indicator, "country": country, "year": year, "value": row["value"],
            "status": row.get("obs_status") or None, "unit": spec["unit"],
            "source": f"{BASE}/country/{country}/indicator/{indicator}?format=json&date={year}&source=2",
        })
    records.sort(key=lambda row: (row["country"], row["year"]))
    egypt_years = [row["year"] for row in records if row["country"] == "EGY"]
    details = {
        **spec, "name": definition["name"], "definition": definition.get("sourceNote", ""),
        "source_organization": definition.get("sourceOrganization", ""),
        "last_updated": metadata.get("lastupdated"), "observations": len(records),
        "country_count": len({row["country"] for row in records}),
        "egypt_latest_year": max(egypt_years, default=None),
        "source": f"https://data.worldbank.org/indicator/{indicator}",
    }
    return details, records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    specifications = json.loads(CONFIG.read_text(encoding="utf-8"))
    metrics, rows, failed = [], [], []
    with ThreadPoolExecutor(max_workers=7) as pool:
        jobs = [(spec["id"], pool.submit(series, spec, args.start_year, args.as_of.year)) for spec in specifications]
        for code, job in jobs:
            try:
                definition, records = job.result()
                if not records:
                    raise ValueError("No populated observations in the selected countries and years")
                metrics.append(definition)
                rows.extend(records)
            except (OSError, ValueError, KeyError) as exc:
                failed.append({"indicator": code, "reason": str(exc)})
    if not metrics:
        print(json.dumps(failed, indent=2), file=sys.stderr)
        return 1
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    observation_file = output / "annual_observations.jsonl.gz"
    write_jsonl(observation_file, rows)
    summary = {
        "retrieved_for": args.as_of.isoformat(), "start_year": args.start_year,
        "source": "https://data.worldbank.org/indicator",
        "data_terms": "https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets",
        "countries": list(COUNTRIES), "observations": len(rows),
        "observations_sha256": hashlib.sha256(observation_file.read_bytes()).hexdigest(),
        "series": metrics, "unavailable": failed,
    }
    (output / "indicator_snapshot.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(metrics)} series, {len(rows)} observations, {len(failed)} unavailable series")
    if failed:
        print(json.dumps(failed, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
