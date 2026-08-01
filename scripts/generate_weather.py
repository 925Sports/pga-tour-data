#!/usr/bin/env python3
"""Pull tournament weather from Google Sheets → data/weather.csv"""
from __future__ import annotations

from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "weather.csv"

WEATHER_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTAvsaoeBBocLCwCz8bqHJnTOgXd55ObC2lCnbu-hikUp_OHGCrOyTNLZlzxQWdhJl5x__28UAUDCdq"
    "/pub?gid=353419593&single=true&output=csv"
)


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(WEATHER_URL, headers={"User-Agent": "925Sports-weather-bot/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        # Google sometimes serves UTF-8 BOM
        text = raw.decode("utf-8-sig", errors="replace")
        if len(text.strip()) < 20:
            print("Weather CSV empty — skip write")
            return 0
        OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT} ({len(text)} bytes, {text.count(chr(10))} lines)")
        return 0
    except Exception as e:
        print("Weather fetch failed:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
