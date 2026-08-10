"""
cli.py — Command-line interface for the Far West Legacy extraction pipeline.

Usage:
    python -m src.cli --text "Obituary text here..."
    python -m src.cli --file path/to/obituary.txt
    python -m src.cli --url https://example.com/obituary
    python -m src.cli --stith-search "Hughes"
"""

import argparse
import json
import sys
from pathlib import Path

from src.extract import ExtractionError, extract_from_text
from src.fetch import FetchError, fetch_obituary_text
from src.obituary_source import SearchUnavailable
from src.sources.stith_source import StithSource, StithSourceError

OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _output_filename(result: dict) -> Path:
    deceased = result.get("deceased", {})
    surname = deceased.get("surname", "unknown").strip() or "unknown"
    given = deceased.get("given_names", "").strip().replace(" ", "_") or "unknown"
    return OUTPUT_DIR / f"{surname}_{given}.json"


def _stith_search_and_pick(query: str) -> tuple[str | None, str]:
    """Search Stith by name, print numbered matches, prompt for a pick, fetch full text.
    Returns (None, "") if there's nothing to extract (no matches, or user cancelled)."""
    source = StithSource()
    results = source.search(query)
    if isinstance(results, SearchUnavailable):
        print(f"Search unavailable: {results.reason}", file=sys.stderr)
        return None, ""
    if not results:
        print(f"No matches found for {query!r}.", file=sys.stderr)
        return None, ""

    for i, stub in enumerate(results, start=1):
        print(f"  [{i}] {stub.name} ({stub.date or 'date unknown'})", file=sys.stderr)

    choice = input(f"Pick 1-{len(results)} (or blank to cancel): ").strip()
    if not choice:
        return None, ""
    try:
        picked = results[int(choice) - 1]
    except (ValueError, IndexError):
        print(f"Invalid choice: {choice!r}", file=sys.stderr)
        return None, ""

    detail = source.fetch_detail(picked.detail_url)
    return detail.text, detail.source_url


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract genealogical data from an obituary."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", metavar="TEXT", help="Obituary text (pasted inline)")
    group.add_argument("--file", metavar="PATH", help="Path to a plain-text obituary file")
    group.add_argument("--url", metavar="URL", help="URL of an obituary page")
    group.add_argument(
        "--stith-search", metavar="NAME",
        help="Search Stith Family Funeral Home listings by name, pick a match, extract it",
    )

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
    elif args.url:
        source_url = args.url
        try:
            obituary_text = fetch_obituary_text(args.url)
        except FetchError as exc:
            print(f"Fetch error: {exc}", file=sys.stderr)
            return 1
    else:  # --stith-search
        try:
            obituary_text, source_url = _stith_search_and_pick(args.stith_search)
        except StithSourceError as exc:
            print(f"Stith fetch error: {exc}", file=sys.stderr)
            return 1
        if obituary_text is None:
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
