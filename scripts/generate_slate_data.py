"""
Pull weekly slate data from Google Sheets and write cleaned CSVs for the Fantasy Golf Hub.
Sources (published CSV):
  - Draftable (DK salaries / images)
  - Betting odds
  - Combined props (PP + UD)
  - Fantasy points (if present)
"""
from __future__ import annotations

import csv
import re
from io import StringIO
from pathlib import Path
from urllib.request import urlopen, Request
from datetime import datetime

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

URLS = {
    "draftable": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=0&single=true&output=csv",
    "betting": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=749430247&single=true&output=csv",
    "combined": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=2034553651&single=true&output=csv",
    "underdog": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=1306744503&single=true&output=csv",
    "prizepicks": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=849548751&single=true&output=csv",
    "fantasy_points": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=654148847&single=true&output=csv",
}


def fetch_csv(url: str) -> list[dict]:
    req = Request(url, headers={"User-Agent": "925Sports-Hub/1.0"})
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(StringIO(raw))
    return list(reader)


def norm_name(name: str) -> str:
    if not name:
        return ""
    s = str(name).strip()
    # "Young, Cameron" -> "Cameron Young"
    if "," in s and not s.lower().startswith("de "):
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            s = f"{parts[1]} {parts[0]}"
    s = re.sub(r"\s+", " ", s)
    return s


def norm_key(name: str) -> str:
    s = norm_name(name).lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def write_rows(path: Path, rows: list[dict], fieldnames: list[str]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  wrote {len(rows)} → {path}")


def process_draftable(rows: list[dict]):
    out = []
    seen = set()
    for r in rows:
        name = r.get("Player Name") or ""
        if not name.strip():
            continue
        key = norm_key(name)
        if key in seen:
            continue
        seen.add(key)
        try:
            sal = int(float(str(r.get("Salary") or "0").replace(",", "")))
        except Exception:
            sal = None
        out.append({
            "player_name": norm_name(name),
            "salary": sal or "",
            "tournament": r.get("Tournament") or r.get("Game") or "",
            "slate_type": r.get("Slate Type") or "",
            "player_image": r.get("Player Image") or "",
            "player_id": r.get("Player ID") or "",
            "draftable_id": r.get("Draftable ID") or "",
            "game_start": r.get("Game Start Time") or "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
    write_rows(
        DATA_DIR / "dk_salaries.csv",
        out,
        ["player_name", "salary", "tournament", "slate_type", "player_image",
         "player_id", "draftable_id", "game_start", "updated_at"],
    )


def process_betting(rows: list[dict]):
    out = []
    for r in rows:
        tour = (r.get("tour") or "").upper()
        market = (r.get("market") or "").lower().strip()
        name = r.get("player_name") or ""
        if not name:
            continue
        # Prefer PGA tour rows for main event; keep OPP as secondary
        out.append({
            "tour": tour,
            "market": market,
            "player_name": norm_name(name),
            "player_name_raw": name,
            "average_odds": r.get("average_odds") or "",
            "implied_probability": r.get("implied_probability") or "",
            "true_implied_probability": r.get("true_implied_probability") or "",
            "odds_draftkings": r.get("odds.draftkings") or "",
            "odds_fanduel": r.get("odds.fanduel") or "",
            "odds_bet365": r.get("odds.bet365") or "",
            "odds_datagolf_baseline": r.get("odds.datagolf.baseline") or "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
    write_rows(
        DATA_DIR / "betting_odds.csv",
        out,
        ["tour", "market", "player_name", "player_name_raw", "average_odds",
         "implied_probability", "true_implied_probability",
         "odds_draftkings", "odds_fanduel", "odds_bet365",
         "odds_datagolf_baseline", "updated_at"],
    )


def process_combined(rows: list[dict]):
    out = []
    for r in rows:
        name = r.get("Player Name") or ""
        if not name.strip():
            continue
        out.append({
            "player_name": norm_name(name),
            "stat_type": r.get("Stat Type") or "",
            "prizepicks_line": r.get("PrizePicks Line") or "",
            "underdog_line": r.get("Underdog Line") or "",
            "average_line": r.get("Average Line") or "",
            "higher_price_ud": r.get("Higher Price (Underdog)") or "",
            "lower_price_ud": r.get("Lower Price (Underdog)") or "",
            "headshot": r.get("Combined Headshot URL") or r.get("Player Image URL (Underdog)") or "",
            "date": r.get("Combined Date") or "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
    write_rows(
        DATA_DIR / "props_combined.csv",
        out,
        ["player_name", "stat_type", "prizepicks_line", "underdog_line",
         "average_line", "higher_price_ud", "lower_price_ud", "headshot",
         "date", "updated_at"],
    )


def process_fantasy_points(rows: list[dict]):
    if not rows:
        print("  fantasy_points empty — skip")
        return
    # Keep flexible columns
    fields = list(rows[0].keys())
    # Normalize a player_name column if possible
    cleaned = []
    for r in rows:
        row = dict(r)
        for cand in ["Player Name", "player_name", "Name", "PLAYER"]:
            if cand in row and row[cand]:
                row["player_name"] = norm_name(row[cand])
                break
        row["updated_at"] = datetime.utcnow().isoformat() + "Z"
        cleaned.append(row)
    if "player_name" not in fields:
        fields = ["player_name"] + fields
    if "updated_at" not in fields:
        fields.append("updated_at")
    write_rows(DATA_DIR / "fantasy_points.csv", cleaned, fields)


def main():
    print("Fetching slate sources…")
    for key, url in URLS.items():
        print(f"\n[{key}]")
        try:
            rows = fetch_csv(url)
            print(f"  fetched {len(rows)} rows")
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        if key == "draftable":
            process_draftable(rows)
        elif key == "betting":
            process_betting(rows)
        elif key == "combined":
            process_combined(rows)
        elif key == "fantasy_points":
            process_fantasy_points(rows)
        else:
            # raw dump for underdog / prizepicks
            if not rows:
                continue
            fields = list(rows[0].keys())
            write_rows(DATA_DIR / f"{key}.csv", rows, fields)

    print("\nDone.")


if __name__ == "__main__":
    main()
