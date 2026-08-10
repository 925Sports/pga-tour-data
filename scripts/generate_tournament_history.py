#!/usr/bin/env python3
"""
Generate per-tournament history packages under data/tournaments/<key>/.

This is the ORIGINAL Course / Tournament History setup:
  - Reads data/tournaments/_index.json
  - For each year → PGA Tour event id (e.g. R2025027)
  - Pulls event-level stats from PGA Tour GraphQL
  - Writes data/tournaments/<key>/<year>.csv

Usage:
  python scripts/generate_tournament_history.py fedex_st_jude
  python scripts/generate_tournament_history.py rocket_classic
  python scripts/generate_tournament_history.py --auto
  python scripts/generate_tournament_history.py --list

--auto picks the tournament key from the live field / DK slate
(upcoming_field.csv, recent_form.csv, dk_salaries.csv) by matching
event/course names against _index.json (and a small alias table).

Does NOT change Hub Course History logic (cheat_sheet / field logs).
Those still come from generate_cheat_sheet.py + field_player_logs.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INDEX_PATH = DATA / "tournaments" / "_index.json"

# Same stat set as the original Course History packages
STATS = [
    ("02675", "SG_Total"),
    ("02674", "SG_Tee_to_Green"),
    ("02567", "SG_OTT"),
    ("02568", "SG_APP"),
    ("02569", "SG_ARG"),
    ("02564", "SG_PUTT"),
    ("101", "Driving_Distance"),
    ("102", "Driving_Accuracy"),
    ("103", "GIR"),
    ("130", "Scrambling"),
    ("120", "Scoring_Average"),
    ("156", "Birdie_Average"),
    ("119", "Putts_Per_Round"),
]

# Name / course aliases → tournament key (when auto-detecting)
EVENT_ALIASES = [
    (re.compile(r"fedex\s*st\.?\s*jude|st\.?\s*jude", re.I), re.compile(r"southwind", re.I), "fedex_st_jude"),
    (re.compile(r"wyndham", re.I), re.compile(r"sedgefield", re.I), "wyndham"),
    (re.compile(r"rocket(\s*mortgage)?(\s*classic)?", re.I), re.compile(r"detroit", re.I), "rocket_classic"),
    (re.compile(r"bmw", re.I), re.compile(r"caves\s*valley|congr", re.I), "bmw_championship"),
    (re.compile(r"tour\s*championship", re.I), re.compile(r"east\s*lake", re.I), "tour_championship"),
    (re.compile(r"memorial", re.I), re.compile(r"muirfield", re.I), "memorial"),
    (re.compile(r"travelers", re.I), re.compile(r"river\s*highland", re.I), "travelers"),
    (re.compile(r"players", re.I), re.compile(r"sawgrass", re.I), "the_players"),
    (re.compile(r"genesis", re.I), re.compile(r"riviera", re.I), "genesis"),
    (re.compile(r"arnold\s*palmer", re.I), re.compile(r"bay\s*hill", re.I), "arnold_palmer"),
    (re.compile(r"3m\s*open", re.I), re.compile(r"twin\s*cities", re.I), "3m_open"),
    (re.compile(r"john\s*deere", re.I), re.compile(r"deere", re.I), "john_deere"),
]


def load_index() -> dict:
    if not INDEX_PATH.exists():
        raise SystemExit(f"Missing {INDEX_PATH}")
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_stat(stat_id: str, event_id: str) -> pd.DataFrame:
    """Pull one stat for a specific tournament (event-level)."""
    year = int(str(event_id)[1:5])  # R2025027 → 2025
    payload = {
        "operationName": "StatDetails",
        "variables": {
            "tourCode": "R",
            "statId": str(stat_id),
            "year": year,
            "eventQuery": {"eventId": event_id},
        },
        "query": """query StatDetails($tourCode: TourCode!, $statId: String!, $year: Int, $eventQuery: StatDetailEventQuery) {
          statDetails(tourCode: $tourCode, statId: $statId, year: $year, eventQuery: $eventQuery) {
            rows {
              ... on StatDetailsPlayer {
                __typename
                playerId
                playerName
                country
                rank
                stats { statName statValue }
              }
            }
          }
        }""",
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "x-pgat-platform": "web",
        "x-amz-user-agent": "aws-amplify/3.0.7",
        "Origin": "https://www.pgatour.com",
        "Referer": "https://www.pgatour.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
    }
    r = requests.post(
        "https://orchestrator.pgatour.com/graphql",
        json=payload,
        headers=headers,
        timeout=45,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        print(f"      GraphQL errors: {data['errors'][:1]}")
        return pd.DataFrame()
    if not data.get("data") or not data["data"].get("statDetails"):
        return pd.DataFrame()

    rows = []
    for item in data["data"]["statDetails"]["rows"] or []:
        if item.get("__typename") != "StatDetailsPlayer":
            continue
        rows.append(
            {
                "player_id": item["playerId"],
                "player": item["playerName"],
                "country": item.get("country", ""),
                "rank": item["rank"],
                "value": item["stats"][0]["statValue"] if item.get("stats") else None,
            }
        )
    return pd.DataFrame(rows)


def process_tournament(tournament_key: str, years_filter: list[str] | None = None) -> None:
    index = load_index()
    if tournament_key not in index:
        known = ", ".join(sorted(index.keys()))
        raise SystemExit(
            f"Tournament '{tournament_key}' not found in _index.json.\nKnown keys: {known}"
        )

    tournament = index[tournament_key]
    print(f"\nProcessing: {tournament['name']}")
    print(f"Course: {tournament.get('course', '')}\n")

    output_dir = DATA / "tournaments" / tournament_key
    output_dir.mkdir(parents=True, exist_ok=True)

    years = tournament.get("years") or {}
    if not years:
        raise SystemExit(f"No years configured for {tournament_key} in _index.json")

    # Sort newest first for nicer logs
    year_items = sorted(years.items(), key=lambda kv: str(kv[0]), reverse=True)
    if years_filter:
        want = {str(y) for y in years_filter}
        year_items = [(y, eid) for y, eid in year_items if str(y) in want]
        if not year_items:
            raise SystemExit(f"No matching years in filter {years_filter}")

    for year, event_id in year_items:
        print(f"→ {year} ({event_id})")
        dfs = []

        for sid, name in STATS:
            try:
                df = fetch_stat(sid, event_id)
                if df.empty:
                    print(f"   ✗ {name}: no data")
                    continue
                df = df.rename(columns={"rank": f"{name}_Rank", "value": name})
                dfs.append(df[["player_id", "player", "country", name, f"{name}_Rank"]])
                print(f"   ✓ {name}: {len(df)} players")
                time.sleep(0.7)
            except Exception as e:
                print(f"   ✗ {name}: {e}")

        if not dfs:
            print(f"   No data for {year}, skipping...\n")
            continue

        merged = dfs[0]
        for df in dfs[1:]:
            merged = merged.merge(
                df.drop(columns=["player", "country"]),
                on="player_id",
                how="outer",
            )

        merged["year"] = year
        merged["event_id"] = event_id
        merged["tournament"] = tournament["name"]
        merged["course"] = tournament.get("course", "")
        merged["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        if "SG_Total_Rank" in merged.columns:
            merged = merged.sort_values("SG_Total_Rank", na_position="last")

        output_path = output_dir / f"{year}.csv"
        merged.to_csv(output_path, index=False)
        print(f"   Saved → {output_path} ({len(merged)} players)\n")

    # Touch a tiny meta file for the hub / humans
    meta = {
        "key": tournament_key,
        "name": tournament["name"],
        "course": tournament.get("course", ""),
        "years": list(years.keys()),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    with open(output_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {output_dir / 'meta.json'}")


def _majority_value(series: pd.Series) -> str:
    s = series.dropna().astype(str).str.strip()
    s = s[s != ""]
    if s.empty:
        return ""
    return s.value_counts().index[0]


def detect_live_event_label() -> tuple[str, str]:
    """
    Return (event_name, course_name) from the best available live feed.
    Prefer upcoming_field / recent_form / cheat_sheet / dk_salaries.
    """
    candidates = [
        DATA / "upcoming_field.csv",
        DATA / "recent_form.csv",
        DATA / "cheat_sheet.csv",
        DATA / "dk_salaries.csv",
        DATA / "fantasy_points.csv",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, nrows=500)
        except Exception as e:
            print(f"  skip {path.name}: {e}")
            continue
        cols = {c.lower(): c for c in df.columns}
        ev_col = (
            cols.get("event_name")
            or cols.get("tournament")
            or cols.get("slate-tournament")
            or cols.get("event")
        )
        course_col = cols.get("course_name") or cols.get("course")
        if not ev_col and not course_col:
            continue
        ev = _majority_value(df[ev_col]) if ev_col else ""
        course = _majority_value(df[course_col]) if course_col else ""
        # Strip Showdown / Classic prefixes from DK
        ev = re.sub(r"^(Showdown[^:]*:\s*|Classic:\s*)", "", ev, flags=re.I).strip()
        if ev or course:
            print(f"  live signal from {path.name}: event={ev!r} course={course!r}")
            return ev, course
    return "", ""


def resolve_key_from_label(event_name: str, course_name: str, index: dict) -> str | None:
    ev = (event_name or "").lower()
    course = (course_name or "").lower()

    # 1) Alias table
    for ev_re, course_re, key in EVENT_ALIASES:
        if key not in index:
            continue
        if ev and ev_re.search(ev):
            return key
        if course and course_re.search(course):
            return key

    # 2) Match against index name / course
    for key, meta in index.items():
        name = str(meta.get("name") or "").lower()
        crs = str(meta.get("course") or "").lower()
        if ev and name and (name in ev or ev in name or name[:10] in ev):
            return key
        if course and crs and (crs in course or course in crs or crs[:8] in course):
            return key
        # key tokens
        key_words = key.replace("_", " ")
        if ev and key_words in ev:
            return key

    return None


def auto_detect_key() -> str:
    index = load_index()
    ev, course = detect_live_event_label()
    if not ev and not course:
        raise SystemExit(
            "Auto-detect failed: no event/course found in upcoming_field / "
            "recent_form / cheat_sheet / dk_salaries. Pass a key explicitly, e.g.\n"
            "  python scripts/generate_tournament_history.py fedex_st_jude"
        )
    key = resolve_key_from_label(ev, course, index)
    if not key:
        known = ", ".join(sorted(index.keys()))
        raise SystemExit(
            f"Could not map live event {ev!r} / course {course!r} to an _index.json key.\n"
            f"Known keys: {known}\n"
            f"Add the tournament to data/tournaments/_index.json then re-run."
        )
    print(f"Auto-detected tournament key: {key} ({index[key].get('name')})")
    return key


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        print("\nKnown keys:")
        try:
            for k, m in sorted(load_index().items()):
                print(f"  {k:20}  {m.get('name')} · {m.get('course')}")
        except Exception:
            pass
        return 0

    if argv[0] == "--list":
        for k, m in sorted(load_index().items()):
            years = ", ".join(sorted(m.get("years", {}), reverse=True))
            print(f"{k:20}  {m.get('name')} · {m.get('course')} · years: {years}")
        return 0

    years_filter = None
    if "--years" in argv:
        i = argv.index("--years")
        years_filter = argv[i + 1].split(",")
        del argv[i : i + 2]

    if argv[0] == "--auto" or argv[0] == "":
        key = auto_detect_key()
    else:
        key = argv[0].strip().lower().replace(" ", "_").replace("-", "_")
        # common aliases typed by hand
        aliases = {
            "rocket": "rocket_classic",
            "rocket_mortgage": "rocket_classic",
            "rocket_mortgage_classic": "rocket_classic",
            "st_jude": "fedex_st_jude",
            "fedex": "fedex_st_jude",
            "fedex_st_jude_championship": "fedex_st_jude",
            "southwind": "fedex_st_jude",
            "wyndham_championship": "wyndham",
        }
        key = aliases.get(key, key)

    process_tournament(key, years_filter=years_filter)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
