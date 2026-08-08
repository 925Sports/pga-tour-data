#!/usr/bin/env python3
"""
Generate recent_form.csv (this week) and/or recent_form_upcoming.csv (next PGA event).

RF definition (event-level, not round-level):
  - Collapse round logs → one row per tournament start
  - RF1–RF7 = last 7 starts (most recent first), finish + event SG Total
  - Event SG Total = mean of that week's round sg_total (CUT weeks use 2-round mean)
  - value        = combined event SG over those starts (sum of event SG)
  - value_rank   = field rank by value (higher SG = better = rank 1)
  - cut_streak   = consecutive made cuts from most recent start (tournaments, not rounds)
  - rflst5/10    = avg finish over last 5/10 starts (CUT counts as 72)

Usage:
  python scripts/generate_recent_form.py              # both (default)
  python scripts/generate_recent_form.py --scope current
  python scripts/generate_recent_form.py --scope upcoming
  EVENT_SCOPE=both python scripts/generate_recent_form.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from field_utils import (  # noqa: E402
    load_field_for_scope,
    parse_scopes,
    scoped_path,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
YEARS = list(range(2017, 2027))
TOUR_TYPES = ["PGA", "OTHER"]

CUT_FINISH_NUM = 72
WD_FINISH_NUM = 80


def load_all_logs() -> pd.DataFrame:
    frames = []
    for name in ("field_player_logs.csv", "field_player_logs_upcoming.csv"):
        path = DATA_DIR / name
        if path.exists():
            print(f"Loading {path.name}...")
            df = pd.read_csv(path, low_memory=False)
            df["source_file"] = path.name
            frames.append(df)
    if not frames:
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
    s = str(name).strip().lower()
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) == 2:
            s = parts[1] + " " + parts[0]
    return re.sub(r"[^a-z0-9]", "", s)


def get_player_history(logs: pd.DataFrame, player_name, name_adjusted=None) -> pd.DataFrame:
    candidates = [c for c in [player_name, name_adjusted] if c and str(c).strip()]
    for cand in candidates:
        if "player_name" in logs.columns:
            mask = logs["player_name"] == cand
            if mask.any():
                return logs.loc[mask]
        if "name_adjusted" in logs.columns:
            mask = logs["name_adjusted"] == cand
            if mask.any():
                return logs.loc[mask]
    target = normalize_name(player_name or name_adjusted)
    if not target:
        return pd.DataFrame()
    for col in ("player_name", "name_adjusted"):
        if col not in logs.columns:
            continue
        temp = logs[[col]].copy()
        temp["_norm"] = temp[col].map(normalize_name)
        idx = temp.index[temp["_norm"] == target]
        if len(idx):
            return logs.loc[idx]
    return pd.DataFrame()


def _parse_year(row) -> float | None:
    for col in ("year", "season"):
        if col in row.index and pd.notna(row.get(col)):
            try:
                y = int(float(row[col]))
                if 1990 <= y <= 2100:
                    return float(y)
            except Exception:
                pass
    for col in ("event_completed", "date", "Date"):
        if col in row.index and pd.notna(row.get(col)):
            dt = pd.to_datetime(row[col], errors="coerce")
            if pd.notna(dt):
                return float(dt.year)
    return None


def _event_key(row) -> str:
    ename = re.sub(r"\s+", " ", str(row.get("event_name") or "").strip().lower())
    ename = ename.replace("rocket mortgage classic", "rocket classic")
    year = _parse_year(row)
    y = str(int(year)) if year is not None and not pd.isna(year) else ""
    completed = ""
    for col in ("event_completed", "date", "Date"):
        if col in row.index and pd.notna(row.get(col)):
            dt = pd.to_datetime(row[col], errors="coerce")
            if pd.notna(dt):
                completed = dt.strftime("%Y-%m-%d")
                break
            completed = str(row[col])[:10]
            break
    eid = str(row.get("event_id") or row.get("eventId") or "")
    return f"{ename}||{y}||{completed}||{eid}"


def _finish_label(fin_text, pos=None) -> str:
    fin = str(fin_text or "").strip().upper()
    if "CUT" in fin or fin == "MC":
        return "CUT"
    if re.search(r"\bWD\b|WITHDRAW", fin):
        return "WD"
    if re.search(r"\bDQ\b|DISQUAL", fin):
        return "DQ"
    raw = str(fin_text or pos or "").strip()
    if not raw or raw.upper() in {"NAN", "NONE", "—", "-"}:
        return "—"
    m = re.search(r"(T?)(\d+)", raw, re.I)
    if m:
        return (("T" if m.group(1) else "") + m.group(2))
    return raw


def _finish_num(label: str):
    u = str(label or "").upper()
    if u in {"CUT", "MC"}:
        return CUT_FINISH_NUM
    if u in {"WD", "DQ", "DNS"}:
        return WD_FINISH_NUM
    m = re.search(r"(\d+)", u)
    return int(m.group(1)) if m else None


def events_from_rounds(history: pd.DataFrame) -> pd.DataFrame:
    """Collapse round logs → one row per tournament. Most recent first."""
    if history is None or history.empty:
        return pd.DataFrame(
            columns=[
                "event_name", "year", "event_completed", "fin_label",
                "fin_num", "sg_total", "n_rounds", "_dt",
            ]
        )

    df = history.copy()
    df["_ek"] = df.apply(_event_key, axis=1)

    rows = []
    for ek, g in df.groupby("_ek", sort=False):
        g = g.copy()
        fin_raw = None
        for col in ("fin_text", "finish", "pos", "POS"):
            if col not in g.columns:
                continue
            vals = g[col].dropna().astype(str)
            vals = vals[vals.str.strip().str.upper().isin({"", "NAN", "NONE", "—", "-"}) == False]
            if len(vals):
                non_cut = vals[~vals.str.upper().str.contains("CUT", na=False)]
                fin_raw = (non_cut.iloc[0] if len(non_cut) else vals.iloc[0])
                break
        label = _finish_label(fin_raw, g.iloc[0].get("pos") if "pos" in g.columns else None)

        sgs = pd.to_numeric(g.get("sg_total"), errors="coerce").dropna()
        sg_avg = float(sgs.mean()) if len(sgs) else np.nan

        year = None
        for _, r in g.iterrows():
            year = _parse_year(r)
            if year is not None:
                break

        completed = None
        dt = pd.NaT
        for col in ("event_completed", "date", "Date"):
            if col in g.columns:
                parsed = pd.to_datetime(g[col], errors="coerce").dropna()
                if len(parsed):
                    dt = parsed.max()
                    completed = dt.strftime("%Y-%m-%d")
                    break
        if pd.isna(dt) and year is not None:
            dt = pd.Timestamp(f"{int(year)}-07-01")

        ename = ""
        if "event_name" in g.columns:
            ename = str(g["event_name"].dropna().iloc[0]) if g["event_name"].notna().any() else ""

        rows.append({
            "event_name": ename,
            "year": int(year) if year is not None else None,
            "event_completed": completed,
            "fin_label": label,
            "fin_num": _finish_num(label),
            "sg_total": sg_avg,
            "n_rounds": int(len(sgs)) if len(sgs) else int(len(g)),
            "_dt": dt,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("_dt", ascending=False, na_position="last").reset_index(drop=True)


def format_rf_cell(event_row) -> str:
    lab = str(event_row.get("fin_label") or "—")
    sg = event_row.get("sg_total")
    if pd.isna(sg):
        return lab
    try:
        v = float(sg)
        return f"{lab} ({v:+.2f})" if v != 0 else f"{lab} (0.00)"
    except Exception:
        return lab


def calculate_cut_streak(events: pd.DataFrame) -> int:
    streak = 0
    for _, row in events.iterrows():
        lab = str(row.get("fin_label") or "").upper()
        if lab in {"CUT", "MC", "WD", "DQ", "DNS"}:
            break
        if not lab or lab in {"—", "-", "NAN"}:
            continue
        streak += 1
    return streak


def build_recent_form(field: pd.DataFrame, logs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, player in field.iterrows():
        history = get_player_history(
            logs, player.get("player_name"), player.get("name_adjusted")
        )
        events = events_from_rounds(history)
        last7 = events.head(7)

        rf = []
        for i in range(7):
            if i < len(last7):
                rf.append(format_rf_cell(last7.iloc[i]))
            else:
                rf.append("—")

        cut_streak = calculate_cut_streak(events)

        fin_nums = [
            int(n) for n in events["fin_num"].tolist()
            if n is not None and not (isinstance(n, float) and np.isnan(n))
        ]
        avg5 = round(float(np.mean(fin_nums[:5])), 1) if len(fin_nums) >= 5 else (
            round(float(np.mean(fin_nums)), 1) if fin_nums else None
        )
        avg10 = round(float(np.mean(fin_nums[:10])), 1) if len(fin_nums) >= 10 else (
            round(float(np.mean(fin_nums)), 1) if fin_nums else None
        )

        sgs = pd.to_numeric(last7.get("sg_total"), errors="coerce").dropna()
        if len(sgs):
            value = round(float(sgs.sum()), 2)       # combined SG
            value_avg = round(float(sgs.mean()), 2)
            rf_sg_starts = int(len(sgs))
        else:
            value = 0.0
            value_avg = None
            rf_sg_starts = 0

        made = sum(
            1 for _, r in events.iterrows()
            if str(r.get("fin_label") or "").upper() not in {"CUT", "MC", "WD", "DQ", "—", ""}
        )
        cut_pct = round(made / len(events) * 100, 1) if len(events) > 0 else None

        missed_cuts_7 = sum(
            1 for _, r in last7.iterrows()
            if str(r.get("fin_label") or "").upper() in {"CUT", "MC"}
        )

        rows.append({
            "player_name": player.get("player_name"),
            "name_adjusted": player.get("name_adjusted") or player.get("player_name"),
            "salary": player.get("salary"),
            "event_name": player.get("event_name"),
            "date_start": player.get("date_start"),
            "course_name": player.get("course_name"),
            "tour": player.get("tour"),
            "cut_streak": cut_streak,
            "rf1": rf[0], "rf2": rf[1], "rf3": rf[2], "rf4": rf[3],
            "rf5": rf[4], "rf6": rf[5], "rf7": rf[6],
            "rflst5": avg5,
            "rflst10": avg10,
            "value": value,
            "value_avg": value_avg,
            "rf_sg_starts": rf_sg_starts,
            "missed_cuts_7": missed_cuts_7,
            "cut_pct": cut_pct,
            "starts_count": len(events),
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

    print("Loading historical logs once…")
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
            top = df.iloc[0]
            print(
                "  event:", top.get("event_name"),
                "| #1:", top.get("player_name"),
                "value", top.get("value"),
                "rf1", top.get("rf1"),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
