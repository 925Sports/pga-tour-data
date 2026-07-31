#!/usr/bin/env python3
"""Precompute specialist / course-fit summary for the current field + course.

Reads:
  data/recent_form.csv (or cheat_sheet.csv) for field + course_name
  data/field_player_logs.csv

Writes:
  data/specialist_board.csv
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def snake_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")
        for c in df.columns
    ]
    return df


def main() -> int:
    field_path = DATA / "recent_form.csv"
    if not field_path.exists():
        field_path = DATA / "cheat_sheet.csv"
    if not field_path.exists():
        print("No field file")
        return 0

    field = snake_cols(pd.read_csv(field_path, low_memory=False))
    course = ""
    if "course_name" in field.columns and len(field):
        course = str(field["course_name"].dropna().astype(str).iloc[0])
    print("course:", course)

    players = set()
    for col in ("player_name", "name_adjusted"):
        if col in field.columns:
            players |= set(field[col].dropna().astype(str).str.strip().tolist())

    logs_path = DATA / "field_player_logs.csv"
    out_cols = ["player_name", "course_name", "starts", "sg_total_avg", "rounds"]
    if not logs_path.exists():
        print("No field_player_logs.csv")
        pd.DataFrame(columns=out_cols).to_csv(DATA / "specialist_board.csv", index=False)
        return 0

    logs = snake_cols(pd.read_csv(logs_path, low_memory=False))
    if "sg_total" in logs.columns:
        logs["sg_total"] = pd.to_numeric(logs["sg_total"], errors="coerce")

    course_logs = logs
    if course and "course_name" in logs.columns:
        cl = course.lower()
        mask = logs["course_name"].astype(str).str.lower().str.contains(
            re.escape(cl[:12]) if len(cl) > 12 else re.escape(cl), na=False
        )
        if "detroit" in cl:
            mask = mask | logs["course_name"].astype(str).str.lower().str.contains("detroit", na=False)
        if mask.any():
            course_logs = logs[mask]

    if "player_name" not in course_logs.columns:
        print("no player_name in logs")
        return 1

    rows = []
    for pname in sorted(players):
        sub = course_logs[
            course_logs["player_name"].astype(str).str.strip().str.lower() == pname.lower()
        ]
        if sub.empty:
            last = pname.split(",")[0].strip().lower() if "," in pname else pname.split()[-1].lower()
            sub = course_logs[
                course_logs["player_name"].astype(str).str.lower().str.contains(re.escape(last), na=False)
            ]
        starts = int(sub["event_name"].nunique()) if "event_name" in sub.columns and len(sub) else len(sub)
        sg = None
        if "sg_total" in sub.columns and sub["sg_total"].notna().any():
            sg = float(sub["sg_total"].mean())
        rows.append({
            "player_name": pname,
            "course_name": course,
            "starts": starts,
            "sg_total_avg": None if sg is None else round(sg, 3),
            "rounds": len(sub),
        })

    out = pd.DataFrame(rows)
    out_path = DATA / "specialist_board.csv"
    out.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
