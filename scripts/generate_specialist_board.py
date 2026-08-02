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

    tokens = [t for t in re.split(r"[^a-z0-9]+", cl) if len(t) >= 4]
    mask = s.str.contains(re.escape(cl[:20]), na=False)
    for t in tokens[:4]:
        mask = mask | s.str.contains(re.escape(t), na=False)
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


def _series_col(df: pd.DataFrame, col: str) -> pd.Series | None:
    """Return a 1-D Series for col even if snake_cols created duplicates."""
    if col not in df.columns:
        return None
    obj = df[col]
    # Duplicate column labels → DataFrame; take first column
    if isinstance(obj, pd.DataFrame):
        if obj.shape[1] == 0:
            return None
        obj = obj.iloc[:, 0]
    return obj.astype(str)


def player_sub(logs: pd.DataFrame, pname: str) -> pd.DataFrame:
    """Exact / name_key match only — no last-name wildcard (avoids wrong careers)."""
    if logs is None or not isinstance(logs, pd.DataFrame) or logs.empty:
        return pd.DataFrame() if logs is None else logs.iloc[0:0]

    target = name_key(pname)
    if not target:
        return logs.iloc[0:0]

    # 1) player_name via name_key
    pn = _series_col(logs, "player_name")
    if pn is not None:
        keys = pn.map(name_key)
        mask = keys.to_numpy().ravel() == target
        if getattr(mask, "any", lambda: False)():
            return logs.loc[mask]

    # 2) name_adjusted via name_key (if present)
    na = _series_col(logs, "name_adjusted")
    if na is not None:
        keys2 = na.map(name_key)
        mask2 = keys2.to_numpy().ravel() == target
        if getattr(mask2, "any", lambda: False)():
            return logs.loc[mask2]

    # 3) exact case-insensitive equality on player_name only
    if pn is not None:
        mask3 = pn.str.strip().str.lower().to_numpy().ravel() == str(pname).strip().lower()
        if getattr(mask3, "any", lambda: False)():
            return logs.loc[mask3]

    return logs.iloc[0:0]


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
        if "event_name" in sub.columns:
            work = sub.copy()
            date_col = None
            for c in ("event_completed", "date", "Date"):
                if c in work.columns:
                    date_col = c
                    break
            if date_col:
                work["_dt"] = pd.to_datetime(work[date_col], errors="coerce")
                work = work.sort_values("_dt", ascending=False)
            seen = set()
            events = []
            for _, r in work.iterrows():
                ev = str(r.get("event_name") or "")
                if ev in seen:
                    continue
                seen.add(ev)
                # fin_text may be duplicated cols — use scalar safely
                raw_fin = r.get(fin_col)
                if isinstance(raw_fin, (pd.Series, pd.DataFrame)):
                    raw_fin = raw_fin.iloc[0] if len(raw_fin) else None
                label, num, is_cut = parse_finish(raw_fin)
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
            col = _series_col(sub, fin_col)
            vals = col.dropna().tolist() if col is not None else []
            for v in vals:
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
            if df.columns.duplicated().any():
                df = df.loc[:, ~df.columns.duplicated()].copy()
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
        series = _series_col(field, col)
        if series is None:
            continue
        for v in series.dropna().astype(str).str.strip().tolist():
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

    # Drop duplicate column names (keep first) — prevents multidimensional .loc errors
    if logs.columns.duplicated().any():
        dups = logs.columns[logs.columns.duplicated()].unique().tolist()
        print("WARNING: dropping duplicate columns:", dups)
        logs = logs.loc[:, ~logs.columns.duplicated()].copy()

    if "sg_total" in logs.columns:
        logs["sg_total"] = pd.to_numeric(logs["sg_total"], errors="coerce")

    # Course filter
    if course and "course_name" in logs.columns:
        mask = course_mask(logs["course_name"], course)
        # ensure 1-D boolean Series
        if isinstance(mask, pd.DataFrame):
            mask = mask.iloc[:, 0]
        n = int(mask.sum())
        print(f"course filter rows: {n} / {len(logs)}")
        if n > 0:
            course_logs = logs.loc[mask.values if hasattr(mask, "values") else mask].copy()
        else:
            print("WARNING: course filter matched 0 rows — board will be empty-ish")
            course_logs = logs.iloc[0:0].copy()
    else:
        print("WARNING: no course resolved — not using full career dump; board empty")
        course_logs = logs.iloc[0:0].copy()

    # Deduplicate course_logs columns too (safety)
    if course_logs.columns.duplicated().any():
        course_logs = course_logs.loc[:, ~course_logs.columns.duplicated()].copy()

    rows = []
    for i, pname in enumerate(players):
        sub = player_sub(course_logs, pname)
        rows.append(summarize_player(sub, pname, course))
        if (i + 1) % 50 == 0:
            print(f"  processed {i + 1}/{len(players)} players…")

    out = pd.DataFrame(rows)

    # SPEC rank: best (highest) sg_total_avg among players with SG
    ranked = out[out["sg_total_avg"].notna()].sort_values(
        "sg_total_avg", ascending=False
    ).copy()
    ranked["spec_rank"] = range(1, len(ranked) + 1)
    rank_map = dict(zip(ranked["player_name"], ranked["spec_rank"]))
    out["spec_rank"] = out["player_name"].map(rank_map)

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
