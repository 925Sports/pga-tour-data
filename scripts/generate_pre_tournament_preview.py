#!/usr/bin/env python3
"""Pull PRE TOURNAMENT PREVIEW from Google Sheets → data/pre_tournament_preview.csv"""
from __future__ import annotations
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "pre_tournament_preview.csv"

URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTAvsaoeBBocLCwCz8bqHJnTOgXd55ObC2lCnbu-hikUp_OHGCrOyTNLZlzxQWdhJl5x__28UAUDCdq"
    "/pub?gid=1157787536&single=true&output=csv"
)

def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "925Sports-preview-bot/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        text = raw.decode("utf-8-sig", errors="replace")
        if len(text.strip()) < 20:
            print("Preview CSV empty — skip")
            return 0
        OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT} ({len(text)} bytes)")
        return 0
    except Exception as e:
        print("Preview fetch failed:", e)
        return 1

if __name__ == "__main__":
    sys.exit(main())
