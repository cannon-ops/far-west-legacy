"""
cli.py — Command-line interface for the Far West Legacy extraction pipeline.

Usage:
    python -m src.cli --text "Obituary text here..."
    python -m src.cli --file path/to/obituary.txt
    python -m src.cli --url https://example.com/obituary
"""

import argparse
import json
import sys
from pathlib import Path

from src.extract import ExtractionError, extract_from_text
from src.fetch import FetchError, fetch_obituary_text

OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _output_filename(result: dict) -> Path:
    deceased = result.get("deceased", {})
    surname = deceased.get("surname", "unknown").strip() or "unknown"
    given = deceased.get("given_names", "").strip().replace(" ", "_") or "unknown"
    return OUTPUT_DIR / f"{surname}_{given}.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract genealogical data from an obituary."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", metavar="TEXT", help="Obituary text (pasted inline)")
    group.add_argument("--file", metavar="PATH", help="Path to a plain-text obituary file")
    group.add_argument("--url", metavar="URL", help="URL of an obituary page")

    args = parser.parse_args(argv)

    source_url = ""

    if args.text:
        obituary_text = args.text
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            return 1
        obituary_text = path.read_text(encoding="utf-8")
    else:  # --url
        source_url = args.url
        try:
            obituary_text = fetch_obituary_text(args.url)
        except FetchError as exc:
            print(f"Fetch error: {exc}", file=sys.stderr)
            return 1

    try:
        result = extract_from_text(obituary_text, source_url=source_url)
    except ExtractionError as exc:
        print(f"Extraction error: {exc}", file=sys.stderr)
        return 1

    json_output = json.dumps(result, indent=2, ensure_ascii=False)
    print(json_output)

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = _output_filename(result)
    out_path.write_text(json_output, encoding="utf-8")
    print(f"\nSaved to: {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
