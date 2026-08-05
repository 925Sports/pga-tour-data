#!/usr/bin/env python3
"""
Generate field_player_logs.csv and/or field_player_logs_upcoming.csv —
historical round logs filtered to the chosen event's field.

Usage:
  python scripts/generate_field_player_logs.py              # both
  python scripts/generate_field_player_logs.py --scope current
  python scripts/generate_field_player_logs.py --scope upcoming
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from field_utils import load_field_for_scope, parse_scopes, scoped_path  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
YEARS = list(range(2017, 2027))
TOUR_TYPES = ["PGA", "OTHER"]


def load_all_logs() -> pd.DataFrame:
    frames = []
    for year in YEARS:
        for t in TOUR_TYPES:
            path = DATA_DIR / f"pga_tour_player_logs_{year}_{t}.csv"
            if path.exists():
                print(f"Loading {path.name}...")
                frames.append(pd.read_csv(path, low_memory=False))
            else:
                print(f"Skipping missing file: {path.name}")
    if not frames:
        raise FileNotFoundError("No historical log files found")
    return pd.concat(frames, ignore_index=True)


def normalize_name(name) -> str:
    if pd.isna(name):
        return ""
    return str(name).lower().replace(" ", "").replace(".", "").replace("-", "")


def build_field_logs(field: pd.DataFrame, logs: pd.DataFrame) -> pd.DataFrame:
    name_map = {}
    for _, row in field.iterrows():
        for col in ["name_adjusted", "player_name"]:
            val = row.get(col)
            if pd.notna(val) and str(val).strip():
                name_map[normalize_name(val)] = str(val).strip()

    work = logs.copy()
    work["_norm_name"] = work["player_name"].apply(normalize_name)
    matched = work[work["_norm_name"].isin(name_map.keys())].copy()
    print(f"Matched starts for field players: {len(matched)}")

    if "event_completed" in matched.columns:
        matched["event_completed"] = pd.to_datetime(matched["event_completed"], errors="coerce")
        matched = matched.sort_values(["_norm_name", "event_completed"], ascending=[True, False])

    matched = matched.drop(columns=["_norm_name"], errors="ignore")
    matched["name_adjusted"] = matched["player_name"]
    matched["updated_at"] = datetime.utcnow().isoformat() + "Z"
    # stamp which event field this extract is for
    if len(field) and "event_name" in field.columns:
        matched["field_event_name"] = field.iloc[0].get("event_name")
        matched["field_course_name"] = field.iloc[0].get("course_name") if "course_name" in field.columns else ""
    return matched


def main(argv=None) -> int:
    scopes = parse_scopes(argv)
    field_path = DATA_DIR / "upcoming_field.csv"
    if not field_path.exists():
        raise SystemExit(f"Missing {field_path}")

    print("Loading historical logs once…")
    logs = load_all_logs()
    print(f"Total historical rows: {len(logs)}")

    for scope in scopes:
        print(f"\n=== Field logs scope={scope} ===")
        field = load_field_for_scope(field_path, scope)
        if field is None or field.empty:
            print(f"No field for scope={scope} — skip")
            continue
        print(f"Players in field: {len(field)}")
        matched = build_field_logs(field, logs)
        out = scoped_path(DATA_DIR, "field_player_logs.csv", scope)
        matched.to_csv(out, index=False)
        print(f"Wrote {out} ({len(matched)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
