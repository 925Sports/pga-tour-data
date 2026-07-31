#!/usr/bin/env python3
"""Precompute a light props board for the Fantasy Golf Hub.

Reads:
  data/props_combined.csv
  data/field_player_logs.csv (optional, for SG averages)

Writes:
  data/props_board.csv
"""
from __future__ import annotations

from pathlib import Path
import math
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


def num(s):
    try:
        if s is None or (isinstance(s, float) and math.isnan(s)):
            return None
        v = float(s)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def main() -> int:
    props_path = DATA / "props_combined.csv"
    if not props_path.exists():
        print("No props_combined.csv — skip")
        return 0

    props = snake_cols(pd.read_csv(props_path, low_memory=False))
    print(f"props rows: {len(props)}")
    print("cols:", list(props.columns)[:25])

    name_col = next((c for c in props.columns if c in ("player_name", "name")), None)
    stat_col = next((c for c in props.columns if c in ("stat_type", "stat")), None)
    if not name_col:
        name_col = next((c for c in props.columns if "player" in c and "name" in c), None)
    if not stat_col:
        stat_col = next((c for c in props.columns if "stat" in c), None)
    if not name_col or not stat_col:
        print("Could not find player/stat columns", list(props.columns))
        return 1

    pp_col = next((c for c in props.columns if "prizepicks" in c and "line" in c), None)
    ud_col = next((c for c in props.columns if "underdog" in c and "line" in c), None)
    avg_col = next((c for c in props.columns if c == "average_line"), None)
    hi_col = next((c for c in props.columns if "higher" in c and "price" in c), None)
    lo_col = next((c for c in props.columns if "lower" in c and "price" in c), None)

    sg_by_player = {}
    logs_path = DATA / "field_player_logs.csv"
    if logs_path.exists():
        try:
            logs = snake_cols(pd.read_csv(logs_path, low_memory=False))
            if "player_name" in logs.columns and "sg_total" in logs.columns:
                logs["sg_total"] = pd.to_numeric(logs["sg_total"], errors="coerce")
                g = (
                    logs.dropna(subset=["sg_total"])
                    .groupby(logs["player_name"].astype(str).str.lower())["sg_total"]
                    .mean()
                )
                sg_by_player = g.to_dict()
                print(f"SG players: {len(sg_by_player)}")
        except Exception as e:
            print("logs optional fail", e)

    def book_avg_for_stat(stat: str):
        sub = props[props[stat_col].astype(str).str.lower() == str(stat).lower()]
        lines = []
        for col in (pp_col, ud_col, avg_col):
            if col and col in sub.columns:
                lines.extend([num(x) for x in sub[col].tolist()])
        lines = [x for x in lines if x is not None]
        if len(lines) < 3:
            return None
        s = pd.Series(lines)
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        kept = s[(s >= q1 - 1.5 * iqr) & (s <= q3 + 1.5 * iqr)]
        if len(kept) < 3:
            kept = s
        return float(kept.mean())

    book_avgs = {}
    for st in props[stat_col].dropna().astype(str).unique():
        book_avgs[st] = book_avg_for_stat(st)

    rows = []
    for _, r in props.iterrows():
        player = str(r.get(name_col) or "").strip()
        stat = str(r.get(stat_col) or "").strip()
        if not player or not stat:
            continue
        pp = num(r[pp_col]) if pp_col else None
        ud = num(r[ud_col]) if ud_col else None
        avg = num(r[avg_col]) if avg_col else None
        hi = num(r[hi_col]) if hi_col else None
        lo = num(r[lo_col]) if lo_col else None
        ba = book_avgs.get(stat)
        sg = sg_by_player.get(player.lower())
        proj = None
        if ba is not None and sg is not None and re.search(r"stroke|score", stat, re.I):
            proj = ba - sg
        elif ba is not None:
            proj = ba
        rows.append({
            "player_name": player,
            "stat_type": stat,
            "prizepicks_line": pp,
            "underdog_line": ud,
            "average_line": avg,
            "higher_price_ud": hi,
            "lower_price_ud": lo,
            "book_avg": ba,
            "sg_total_avg": sg,
            "proj": proj,
            "is_matchup": bool(re.search(r"matchup", stat, re.I)),
        })

    out = pd.DataFrame(rows)
    out_path = DATA / "props_board.csv"
    out.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
