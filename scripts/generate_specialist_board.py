#!/usr/bin/env python3
"""Precompute specialist / course-fit summary for the current field + course.

Reads (in priority order for field/course):
  data/cheat_sheet.csv   (preferred — has course_name)
  data/recent_form.csv
  data/pre_tournament_preview.csv  (course fallback)
  data/field_player_logs.csv

Writes:
  data/specialist_board.csv

Columns:
  player_name, course_name, starts, rounds, sg_total_avg, avg_finish,
  best, cut_pct, last3, spec_rank
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


def name_key(n: str) -> str:
    s = str(n or "").strip().lower()
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) == 2:
            s = parts[1] + " " + parts[0]
    return re.sub(r"[^a-z0-9]", "", s)


def resolve_course(field: pd.DataFrame) -> str:
    """Prefer field.course_name, else cheat_sheet, else preview, else event hint."""
    if "course_name" in field.columns and field["course_name"].notna().any():
        c = str(field["course_name"].dropna().astype(str).iloc[0]).strip()
        if c and c.lower() not in {"nan", "none", ""}:
            return c

    cheat_path = DATA / "cheat_sheet.csv"
    if cheat_path.exists():
        try:
            ch = snake_cols(pd.read_csv(cheat_path, low_memory=False, nrows=20))
            if "course_name" in ch.columns and ch["course_name"].notna().any():
                c = str(ch["course_name"].dropna().astype(str).iloc[0]).strip()
                if c and c.lower() not in {"nan", "none", ""}:
                    return c
        except Exception as e:
            print("cheat_sheet course read failed:", e)

    preview_path = DATA / "pre_tournament_preview.csv"
    if preview_path.exists():
        try:
            pv = pd.read_csv(preview_path, low_memory=False, nrows=5)
            for col in pv.columns:
                if re.search(r"course", str(col), re.I):
                    vals = pv[col].dropna().astype(str)
                    if len(vals):
                        c = str(vals.iloc[0]).strip()
                        if c and c.lower() not in {"nan", "none", ""}:
                            return c
        except Exception as e:
            print("preview course read failed:", e)

    # Event-name hints (extend as needed)
    event = ""
    if "event_name" in field.columns and field["event_name"].notna().any():
        event = str(field["event_name"].dropna().astype(str).iloc[0])
    hints = [
        (r"rocket", "DETROIT GOLF CLUB"),
        (r"memorial", "Muirfield Village"),
        (r"travelers", "TPC River Highlands"),
        (r"john deere", "TPC Deere Run"),
        (r"players", "TPC Sawgrass"),
        (r"genesis", "Riviera Country Club"),
        (r"arnold palmer", "Bay Hill"),
    ]
    for pat, course in hints:
        if re.search(pat, event, re.I):
            return course
    return ""


def course_mask(series: pd.Series, course: str) -> pd.Series:
    """Loose but controlled course match."""
    s = series.astype(str).str.lower()
    cl = (course or "").lower().strip()
    if not cl:
        return pd.Series([False] * len(series), index=series.index)

    # token pieces: "detroit golf club" → try full + distinctive tokens
    tokens = [t for t in re.split(r"[^a-z0-9]+", cl) if len(t) >= 4]
    mask = s.str.contains(re.escape(cl[:20]), na=False)
    for t in tokens[:4]:
        mask = mask | s.str.contains(re.escape(t), na=False)
    # known aliases
    if "detroit" in cl or "rocket" in cl:
        mask = mask | s.str.contains("detroit", na=False)
    return mask


def parse_finish(fin) -> tuple[str | None, float | None, bool]:
    u = str(fin or "").strip().upper()
    if not u or u in {"NAN", "NONE", "—", "-"}:
        return None, None, False
    if "CUT" in u or u == "MC":
        return "CUT", 100.0, True
    if re.search(r"\bWD\b|WITHDRAW", u):
        return "WD", None, False
    if re.search(r"\bDQ\b", u):
        return "DQ", None, False
    m = re.search(r"T?(\d+)", u)
    if m:
        n = int(m.group(1))
        return str(n), float(n), False
    return u, None, False


def player_sub(logs: pd.DataFrame, pname: str) -> pd.DataFrame:
    """Exact / name_key match only — no last-name wildcard (avoids wrong careers)."""
    if logs.empty or "player_name" not in logs.columns:
        return logs.iloc[0:0]

    target = name_key(pname)
    keys = logs["player_name"].map(name_key)
    sub = logs.loc[keys == target]
    if not sub.empty:
        return sub

    # also try name_adjusted if present
    if "name_adjusted" in logs.columns:
        keys2 = logs["name_adjusted"].map(name_key)
        sub = logs.loc[keys2 == target]
        if not sub.empty:
            return sub

    # last resort: exact case-insensitive string equality only
    sub = logs[
        logs["player_name"].astype(str).str.strip().str.lower() == str(pname).strip().lower()
    ]
    return sub


def summarize_player(sub: pd.DataFrame, pname: str, course: str) -> dict:
    if sub is None or sub.empty:
        return {
            "player_name": pname,
            "course_name": course,
            "starts": 0,
            "rounds": 0,
            "sg_total_avg": None,
            "avg_finish": None,
            "best": None,
            "cut_pct": None,
            "last3": "",
            "spec_rank": None,
        }

    # Starts = unique events when possible
    if "event_name" in sub.columns:
        starts = int(sub["event_name"].nunique())
    else:
        starts = int(len(sub))

    rounds = int(len(sub))
    sg = None
    if "sg_total" in sub.columns and sub["sg_total"].notna().any():
        sg = round(float(sub["sg_total"].mean()), 3)

    # Finishes: prefer one row per event (latest round or event-level fin_text)
    fin_col = None
    for c in ("fin_text", "finish", "pos", "position"):
        if c in sub.columns:
            fin_col = c
            break

    finishes_num = []
    best = None
    cuts = 0
    made = 0
    last_labels = []

    if fin_col:
        # group by event if possible
        if "event_name" in sub.columns:
            # take first non-null finish per event; order by date if present
            work = sub.copy()
            date_col = None
            for c in ("event_completed", "date", "Date"):
                if c in work.columns:
                    date_col = c
                    break
            if date_col:
                work["_dt"] = pd.to_datetime(work[date_col], errors="coerce")
                work = work.sort_values("_dt", ascending=False)
            events = []
            seen = set()
            for _, r in work.iterrows():
                ev = str(r.get("event_name") or "")
                if ev in seen:
                    continue
                seen.add(ev)
                label, num, is_cut = parse_finish(r.get(fin_col))
                if label:
                    events.append((label, num, is_cut))
            for label, num, is_cut in events:
                last_labels.append(label)
                if is_cut:
                    cuts += 1
                    made += 1
                elif num is not None:
                    finishes_num.append(num)
                    made += 1
                    if best is None or num < best:
                        best = int(num)
        else:
            for v in sub[fin_col].dropna().astype(str).tolist():
                label, num, is_cut = parse_finish(v)
                if not label:
                    continue
                last_labels.append(label)
                if is_cut:
                    cuts += 1
                    made += 1
                elif num is not None:
                    finishes_num.append(num)
                    made += 1
                    if best is None or num < best:
                        best = int(num)

    avg_finish = round(sum(finishes_num) / len(finishes_num), 2) if finishes_num else None
    cut_pct = round(100.0 * cuts / made, 1) if made else None
    last3 = "|".join(last_labels[:3])

    return {
        "player_name": pname,
        "course_name": course,
        "starts": starts,
        "rounds": rounds,
        "sg_total_avg": sg,
        "avg_finish": avg_finish,
        "best": best,
        "cut_pct": cut_pct,
        "last3": last3,
        "spec_rank": None,  # filled after ranking
    }


def load_field() -> pd.DataFrame:
    # Prefer cheat_sheet (has course + field). Fall back to recent_form.
    for name in ("cheat_sheet.csv", "recent_form.csv", "upcoming_field.csv"):
        p = DATA / name
        if p.exists():
            df = snake_cols(pd.read_csv(p, low_memory=False))
            print(f"Field from {name}: {len(df)} rows")
            return df
    return pd.DataFrame()


def main() -> int:
    field = load_field()
    if field.empty:
        print("No field file")
        return 0

    course = resolve_course(field)
    print("course:", course or "(none — will use empty sample)")

    players: list[str] = []
    seen = set()
    for col in ("name_adjusted", "player_name"):
        if col not in field.columns:
            continue
        for v in field[col].dropna().astype(str).str.strip().tolist():
            k = name_key(v)
            if not k or k in seen:
                continue
            seen.add(k)
            players.append(v)

    out_cols = [
        "player_name", "course_name", "starts", "rounds",
        "sg_total_avg", "avg_finish", "best", "cut_pct", "last3", "spec_rank",
    ]

    logs_path = DATA / "field_player_logs.csv"
    if not logs_path.exists():
        print("No field_player_logs.csv — writing empty board")
        pd.DataFrame(columns=out_cols).to_csv(DATA / "specialist_board.csv", index=False)
        return 0

    print("Loading logs…")
    logs = snake_cols(pd.read_csv(logs_path, low_memory=False))
    if "sg_total" in logs.columns:
        logs["sg_total"] = pd.to_numeric(logs["sg_total"], errors="coerce")

    # Course filter
    if course and "course_name" in logs.columns:
        mask = course_mask(logs["course_name"], course)
        n = int(mask.sum())
        print(f"course filter rows: {n} / {len(logs)}")
        if n > 0:
            course_logs = logs.loc[mask].copy()
        else:
            print("WARNING: course filter matched 0 rows — board will be empty-ish")
            course_logs = logs.iloc[0:0].copy()
    else:
        print("WARNING: no course resolved — not using full career dump; board empty")
        course_logs = logs.iloc[0:0].copy()

    rows = []
    for pname in players:
        sub = player_sub(course_logs, pname)
        rows.append(summarize_player(sub, pname, course))

    out = pd.DataFrame(rows)

    # SPEC rank: best (highest) sg_total_avg among players with SG, else null
    ranked = out[out["sg_total_avg"].notna()].sort_values(
        "sg_total_avg", ascending=False
    ).copy()
    ranked["spec_rank"] = range(1, len(ranked) + 1)
    rank_map = dict(zip(ranked["player_name"], ranked["spec_rank"]))
    out["spec_rank"] = out["player_name"].map(rank_map)

    # Stable column order
    out = out[out_cols]

    out_path = DATA / "specialist_board.csv"
    out.to_csv(out_path, index=False)
    with_sg = int(out["sg_total_avg"].notna().sum())
    print(f"Wrote {out_path} ({len(out)} players, {with_sg} with SG, course={course!r})")
    if with_sg:
        top = out.nsmallest(5, "spec_rank")[
            ["spec_rank", "player_name", "starts", "sg_total_avg", "avg_finish"]
        ]
        print("Top SPEC:\n", top.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
