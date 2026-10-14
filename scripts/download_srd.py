"""Download the official SRD 5.2.1 PDF (CC-BY-4.0) into data/raw/srd/.

The PDF isn't committed; this script fetches it from D&D Beyond and checks
it's the expected document before the ingestion step uses it.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import httpx

URL = "https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf"
DEST = Path(__file__).resolve().parents[1] / "data/raw/srd/SRD_CC_v5.2.1.pdf"
EXPECTED_PAGES = 364


def main() -> int:
    if DEST.exists():
        print(f"Already present: {DEST}")
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL}")
    with httpx.stream("GET", URL, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        tmp = DEST.with_suffix(".part")
        with tmp.open("wb") as f:
            for block in r.iter_bytes():
                f.write(block)

    import pymupdf

    with pymupdf.open(tmp) as doc:
        pages = doc.page_count
    if pages != EXPECTED_PAGES:
        tmp.unlink()
        print(f"Unexpected page count {pages} (wanted {EXPECTED_PAGES}); aborting", file=sys.stderr)
        return 1
    tmp.replace(DEST)
    print(f"Saved {DEST} ({pages} pages, sha256 {hashlib.sha256(DEST.read_bytes()).hexdigest()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
