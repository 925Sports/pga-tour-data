#!/usr/bin/env python3
"""
Generate recent_form.csv (this week) and/or recent_form_upcoming.csv (next PGA event).

Usage:
  python scripts/generate_recent_form.py              # both (default)
  python scripts/generate_recent_form.py --scope current
  python scripts/generate_recent_form.py --scope upcoming
  EVENT_SCOPE=both python scripts/generate_recent_form.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# allow `python scripts/generate_recent_form.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))
from field_utils import (  # noqa: E402
    load_field_for_scope,
    parse_scopes,
    scoped_path,
)

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
                df = pd.read_csv(path, low_memory=False)
                df["source_file"] = path.name
                frames.append(df)
            else:
                print(f"Skipping missing file: {path.name}")
    if not frames:
        raise FileNotFoundError("No historical log files found")
    return pd.concat(frames, ignore_index=True)


def normalize_name(name) -> str:
    if pd.isna(name):
        return ""
    return str(name).lower().replace(" ", "").replace(".", "").replace("-", "")


def get_player_history(logs: pd.DataFrame, player_name, name_adjusted=None) -> pd.DataFrame:
    candidates = [c for c in [player_name, name_adjusted] if c and str(c).strip()]
    for cand in candidates:
        if "player_name" in logs.columns:
            mask = logs["player_name"] == cand
            if mask.any():
                return logs[mask]
        if "name_adjusted" in logs.columns:
            mask = logs["name_adjusted"] == cand
            if mask.any():
                return logs[mask]
    target = normalize_name(player_name or name_adjusted)
    if "player_name" in logs.columns:
        temp = logs.copy()
        temp["_norm"] = temp["player_name"].apply(normalize_name)
        subset = temp[temp["_norm"] == target]
        if len(subset) > 0:
            return subset
    return pd.DataFrame()


def format_finish(row) -> str:
    fin = str(row.get("fin_text", "")).upper()
    if "CUT" in fin:
        return "CUT"
    pos = row.get("pos") or row.get("POS") or row.get("fin_text") or ""
    sg = row.get("sg_total")
    if pd.isna(sg):
        return str(pos) if pos else "—"
    try:
        return f"{pos} ({float(sg):.2f})"
    except Exception:
        return str(pos)


def calculate_cut_streak(history: pd.DataFrame) -> int:
    streak = 0
    for _, row in history.iterrows():
        fin = str(row.get("fin_text", "")).upper()
        if "CUT" in fin:
            break
        streak += 1
    return streak


def build_recent_form(field: pd.DataFrame, logs: pd.DataFrame) -> pd.DataFrame:
    date_col = "event_completed"
    if date_col not in logs.columns:
        raise KeyError(f"'{date_col}' column not found in logs")
    logs = logs.copy()
    logs[date_col] = pd.to_datetime(logs[date_col], errors="coerce")
    logs = logs.sort_values(date_col, ascending=False)

    rows = []
    for _, player in field.iterrows():
        history = get_player_history(logs, player.get("player_name"), player.get("name_adjusted"))
        history = history.copy()
        last7 = history.head(7)

        rf = []
        for i in range(7):
            if i < len(last7):
                rf.append(format_finish(last7.iloc[i]))
            else:
                rf.append("—")

        cut_streak = calculate_cut_streak(history)

        finishes = []
        for _, row in history.iterrows():
            fin = str(row.get("fin_text", "")).upper()
            if "CUT" not in fin:
                pos = row.get("pos") or row.get("POS")
                try:
                    finishes.append(float(pos))
                except Exception:
                    pass

        avg5 = round(float(np.mean(finishes[:5])), 1) if len(finishes) >= 5 else None
        avg10 = (
            round(float(np.mean(finishes[:10])), 1)
            if len(finishes) >= 10
            else (round(float(np.mean(finishes)), 1) if finishes else None)
        )

        weights = [3.2, 2.6, 2.1, 1.6, 1.3, 1.0, 0.8]
        weighted_sum = 0.0
        weight_total = 0.0
        for i in range(min(7, len(last7))):
            sg = last7.iloc[i].get("sg_total")
            if pd.notna(sg):
                try:
                    weighted_sum += float(sg) * weights[i]
                    weight_total += weights[i]
                except Exception:
                    pass
        value = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0

        made = sum(
            1
            for _, r in history.iterrows()
            if "CUT" not in str(r.get("fin_text", "")).upper()
        )
        cut_pct = round(made / len(history) * 100, 1) if len(history) > 0 else None

        rows.append({
            "player_name": player.get("player_name"),
            "name_adjusted": player.get("name_adjusted") or player.get("player_name"),
            "salary": player.get("salary"),
            "event_name": player.get("event_name"),
            "date_start": player.get("date_start"),
            "course_name": player.get("course_name"),
            "tour": player.get("tour"),
            "cut_streak": cut_streak,
            "rf1": rf[0],
            "rf2": rf[1],
            "rf3": rf[2],
            "rf4": rf[3],
            "rf5": rf[4],
            "rf6": rf[5],
            "rf7": rf[6],
            "rflst5": avg5,
            "rflst10": avg10,
            "value": value,
            "cut_pct": cut_pct,
            "starts_count": len(history),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["value_rank"] = df["value"].rank(ascending=False, method="min").astype("Int64")
    df["rflst5_rank"] = df["rflst5"].rank(ascending=True, method="min").astype("Int64")
    df["rflst10_rank"] = df["rflst10"].rank(ascending=True, method="min").astype("Int64")
    return df.sort_values("value_rank")


def main(argv=None) -> int:
    scopes = parse_scopes(argv)
    field_path = DATA_DIR / "upcoming_field.csv"
    if not field_path.exists():
        raise SystemExit(f"Missing {field_path}")

    print("Loading historical logs (2017–2026) once…")
    logs = load_all_logs()
    print(f"Total log rows: {len(logs)}")

    for scope in scopes:
        print(f"\n=== Recent Form scope={scope} ===")
        field = load_field_for_scope(field_path, scope)
        if field is None or field.empty:
            print(f"No field for scope={scope} — skip")
            continue
        print(f"Players: {len(field)}")
        df = build_recent_form(field, logs)
        out = scoped_path(DATA_DIR, "recent_form.csv", scope)
        df.to_csv(out, index=False)
        print(f"Wrote {out} ({len(df)} players)")
        if len(df):
            print("  event:", df.iloc[0].get("event_name"), "| top:", df.iloc[0].get("player_name"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
