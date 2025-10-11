import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch a JSON payload from an HTTP API and store it locally."
    )
    parser.add_argument(
        "--api",
        required=True,
        help="HTTP(S) endpoint that returns JSON (GET request).",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory where the JSON file will be written.",
    )
    parser.add_argument(
        "--file_name",
        default=None,
        help="Output file name (defaults to derived name or 'response.json').",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Indentation level for the saved JSON file.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting an existing output file.",
    )
    return parser.parse_args()


def derive_default_filename(api_url: str) -> str:
    """Derive a readable filename from the API URL."""
    parsed = urlparse(api_url)
    candidate = Path(parsed.path).name or "response"
    if not candidate.lower().endswith(".json"):
        candidate = f"{candidate}.json"
    return candidate


def fetch_json(api_url: str, timeout: float):
    response = requests.get(api_url, timeout=timeout)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("API response is not valid JSON.") from exc


def write_json_file(data, output_path: Path, indent: int, overwrite: bool):
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists. Use --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
        f.write("\n")


def main():
    args = parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    file_name = args.file_name or derive_default_filename(args.api)
    output_path = output_dir / file_name

    try:
        payload = fetch_json(args.api, timeout=args.timeout)
        write_json_file(payload, output_path, indent=args.indent, overwrite=args.overwrite)
    except (requests.RequestException, RuntimeError, FileExistsError) as exc:
        print(f"Failed to store JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print(f"JSON saved to {output_path}")


if __name__ == "__main__":
    main()
