#!/usr/bin/env python3
"""
Generate lightweight Cheat Sheet + Course History CSVs from field logs + upcoming field.
Also ranks KEY STAT 1-5 (S1-S5) from PRE TOURNAMENT PREVIEW labels using season-long stats.

Outputs:
  data/cheat_sheet.csv
  data/course_history_summary.csv

RF / value_rank:
  Last 7 tournament starts (event-level). value = sum of event SG Total
  (mean of that week's round SG; CUT weeks use 2-round mean). value_rank
  ranks the field by value descending (higher SG = #1).
"""
from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict

import pandas as pd
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from field_utils import (  # noqa: E402
    load_field_for_scope, match_preview_row, parse_scopes, scoped_path,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

COURSE_HINTS = [
    (re.compile(r"rocket", re.I), re.compile(r"detroit", re.I), "Detroit Golf Club"),
    (re.compile(r"wyndham", re.I), re.compile(r"sedgefield", re.I), "Sedgefield Country Club"),
    (re.compile(r"memorial", re.I), re.compile(r"muirfield", re.I), "Muirfield Village"),
    (re.compile(r"travelers", re.I), re.compile(r"river highland|tpc river", re.I), "TPC River Highlands"),
    (re.compile(r"john deere", re.I), re.compile(r"deere", re.I), "TPC Deere Run"),
    (re.compile(r"arnold palmer", re.I), re.compile(r"bay hill", re.I), "Bay Hill"),
    (re.compile(r"genesis", re.I), re.compile(r"riviera", re.I), "Riviera Country Club"),
    (re.compile(r"players", re.I), re.compile(r"sawgrass", re.I), "TPC Sawgrass"),
    (re.compile(r"3m open", re.I), re.compile(r"tpc twin", re.I), "TPC Twin Cities"),
    (re.compile(r"fedex st.? jude|st\.? jude", re.I), re.compile(r"tpc southwind", re.I), "TPC Southwind"),
    (re.compile(r"bmw", re.I), re.compile(r"caves valley|congr", re.I), "Caves Valley"),
    (re.compile(r"tour championship", re.I), re.compile(r"east lake", re.I), "East Lake"),
]


def name_key(n: str) -> str:
    s = str(n or "").strip().lower()
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) == 2:
            s = parts[1] + " " + parts[0]
    return re.sub(r"[^a-z0-9]", "", s)


def parse_finish(fin: str):
    fin_u = str(fin or "").strip().upper()
    if not fin_u or fin_u in {"—", "NAN", "NONE"}:
        return "—", None, False
    if "CUT" in fin_u or fin_u == "MC":
        return "CUT", 100, True
    if re.search(r"\bWD\b|WITHDRAW", fin_u):
        return "WD", 101, False
    if re.search(r"\bDQ\b|DISQUAL", fin_u):
        return "DQ", 102, False
    m = re.search(r"T?(\d+)", fin_u)
    if m:
        n = int(m.group(1))
        return str(n), n, False
    return fin_u, None, False


def load_logs(field_logs_path: Path | None = None) -> pd.DataFrame:
    frames = []
    primary = field_logs_path if field_logs_path and field_logs_path.exists() else DATA / "field_player_logs.csv"
    candidates = [
        primary,
        DATA / "field_player_logs.csv",
        DATA / "field_player_logs_upcoming.csv",
        *sorted(DATA.glob("pga_tour_player_logs_*_PGA.csv")),
        *sorted(DATA.glob("pga_tour_player_logs_*_OTHER.csv")),
    ]
    seen = set()
    for path in candidates:
        if not path.exists() or path.name in seen:
            continue
        seen.add(path.name)
        print(f"Loading {path.name}…")
        try:
            df = pd.read_csv(path, low_memory=False)
            df["source_file"] = path.name
            frames.append(df)
        except Exception as e:
            print(f" skip {path.name}: {e}")
    if not frames:
        raise SystemExit("No log CSVs found in data/")
    logs = pd.concat(frames, ignore_index=True)
    print(f"Total log rows: {len(logs)}")
    return logs


def load_field(scope: str = "current") -> pd.DataFrame:
    """Prefer scoped recent_form; else pick field from upcoming_field via field_utils.

    Never fall back to the other scope's recent_form (that re-wrote Rocket as
    'upcoming' when no next event exists on the sheet).
    """
    rf = scoped_path(DATA, "recent_form.csv", scope)
    if rf.exists():
        df = pd.read_csv(rf, low_memory=False)
        print(f"Field from {rf.name}: {len(df)} rows (scope={scope})")
        return df
    # Direct from sheet for this scope only
    uf = DATA / "upcoming_field.csv"
    if uf.exists():
        df = load_field_for_scope(uf, scope)
        if df is not None and len(df):
            print(f"Field from upcoming_field.csv via field_utils scope={scope}: {len(df)} rows")
            return df
    # Legacy fallback ONLY for current scope (never for upcoming)
    if (scope or "current").strip().lower() in ("current", "this", "this_week", ""):
        for name in ("recent_form.csv", "upcoming_field.csv", "field.csv"):
            path = DATA / name
            if path.exists():
                df = pd.read_csv(path, low_memory=False)
                print(f"Field from {name}: {len(df)} rows (legacy current)")
                return df
        raise SystemExit("No field file (recent_form.csv / upcoming_field.csv) found")
    print(f"No field for scope={scope} — empty")
    return pd.DataFrame()


def load_preview(event_name: str = "", course_name: str = "") -> dict:
    """Match PRE TOURNAMENT PREVIEW row to the active event (not hard-coded Rocket)."""
    p = DATA / "pre_tournament_preview.csv"
    if not p.exists():
        print("No pre_tournament_preview.csv — S1-S5 labels will be empty")
        return {}
    df = pd.read_csv(p, low_memory=False)
    if df.empty:
        return {}
    d = match_preview_row(df, event_name, course_name)
    print("Preview tournament:", d.get("TOURNAMENT"), "| course:", d.get("COURSE NAME"))
    for i in range(1, 6):
        print(f"  KEY STAT {i}:", d.get(f"KEY STAT {i}") or d.get(f"Key Stat {i}"))
    return d


def load_season_stats() -> tuple[pd.DataFrame, pd.DataFrame]:
    pga = pd.DataFrame()
    dp = pd.DataFrame()
    pp = DATA / "pga_season_stats.csv"
    dp_p = DATA / "dp_world_season_stats.csv"
    if pp.exists():
        pga = pd.read_csv(pp, low_memory=False)
        print(f"PGA season stats: {len(pga)} rows, cols={list(pga.columns)[:8]}...")
    if dp_p.exists():
        dp = pd.read_csv(dp_p, low_memory=False)
        print(f"DP season stats: {len(dp)} rows")
    return pga, dp


def map_key_stat_columns(label: str) -> list[str]:
    """Map KEY STAT label → preferred season-stat columns. Rank cols preferred when present."""
    L = re.sub(r"\s+", " ", str(label or "").strip().upper())
    if not L:
        return []
    if re.search(r"BALL STRIKING|\bBS\b", L):
        return ["Ball_Striking_Rank", "Ball_Striking", "BS"]
    if re.search(r"TOTAL DRIVING|\bTD\b", L):
        return ["Total_Driving_Rank", "Total_Driving", "TD"]
    if re.search(r"APPROACH|SG AP\b|STROKES GAINED APPROACH", L):
        return ["SG_APP"]
    if re.search(r"OFF.?TEE|SG OT\b|STROKES GAINED OFF", L):
        return ["SG_OTT"]
    if re.search(r"AROUND|SG ARG", L):
        return ["SG_ARG"]
    if re.search(r"PUTT", L):
        return ["SG_PUTT"]
    if re.search(r"TEE TO GREEN|T2G", L):
        return ["SG_Tee_to_Green"]
    if re.search(r"SG TOTAL|STROKES GAINED TOTAL|SG TTL", L):
        return ["SG_Total"]
    if re.search(r"BIRDIE OR BETTER|BOB", L) and "RATIO" not in L:
        return ["Birdie_or_Better_Percentage", "Birdie_or_Better_Percentag", "Birdie_or_Better", "BOB"]
    if re.search(r"BIRDIE TO BOGEY|B2B", L):
        return ["B2B", "Birdie_to_Bogey"]
    if re.search(r"PAR 5", L):
        return ["Par_5", "PAR_5", "Par5"]
    if re.search(r"PAR 4", L):
        return ["Par_4", "PAR_4", "Par4"]
    if re.search(r"GIR|GREENS GAINED|\bGG\b", L):
        return ["GIR", "GG"]
    if re.search(r"FAIRWAY|DRIVING ACC|ACCURACY", L):
        return ["Driving_Accuracy", "ACCURACY"]
    if re.search(r"DRIVING DIST|DISTANCE|POWER", L):
        return ["Driving_Distance", "POWER"]
    if re.search(r"SCORING AVG|SCORING AVERAGE", L):
        return ["Scoring_Average"]
    if re.search(r"BIRDIE AVERAGE", L):
        return ["Birdie_Average"]
    if re.search(r"STROKES GAINED DIFFERENTIAL|SG DIF", L):
        return ["SG_Total"]  # value ranked; differential handled loosely
    slug = re.sub(r"[^A-Za-z0-9]+", "_", L).strip("_")
    return [slug, L.replace(" ", "_")]


def season_value_for_player(pga: pd.DataFrame, dp: pd.DataFrame, pkey: str, cols: list[str]):
    """Return numeric season value for player (PGA preferred, else DP)."""
    def from_df(df: pd.DataFrame):
        if df is None or df.empty:
            return None
        # name column
        name_col = None
        for c in ("player", "player_name", "Player", "Name", "name"):
            if c in df.columns:
                name_col = c
                break
        if not name_col:
            return None
        keys = df[name_col].map(name_key)
        hits = df.loc[keys == pkey]
        if hits.empty:
            return None
        row = hits.iloc[0]
        colmap = {str(c).lower().replace(" ", "_"): c for c in df.columns}
        for want in cols:
            # exact
            if want in row.index and pd.notna(row[want]):
                try:
                    return float(re.sub(r"[^0-9.\-+]", "", str(row[want])))
                except Exception:
                    pass
            mapped = colmap.get(str(want).lower().replace(" ", "_"))
            if mapped is not None and pd.notna(row[mapped]):
                try:
                    return float(re.sub(r"[^0-9.\-+]", "", str(row[mapped])))
                except Exception:
                    pass
            # fuzzy contains
            stem = str(want).lower().replace("_rank", "").replace(" ", "_")
            for lk, orig in colmap.items():
                if lk.endswith("_rank"):
                    continue
                if stem and (stem in lk or lk in stem):
                    try:
                        return float(re.sub(r"[^0-9.\-+]", "", str(row[orig])))
                    except Exception:
                        continue
        return None

    v = from_df(pga)
    if v is not None:
        return v
    return from_df(dp)


def rank_field_on_stat(field: pd.DataFrame, pga: pd.DataFrame, dp: pd.DataFrame, label: str) -> dict:
    """Return {name_key: rank} for the field on this KEY STAT label. Rank 1 = best."""
    cols = map_key_stat_columns(label)
    if not cols:
        return {}
    L = str(label or "").upper()
    using_rank_col = any(str(c).lower().endswith("_rank") for c in cols)
    # Official rank cols + scoring avg: lower better. Raw SG/BOB: higher better.
    lower_better = using_rank_col or (
        bool(re.search(r"SCORING AVG|SCORING AVERAGE|RANK", L))
        and not re.search(r"GAINED|BIRDIE|BOB|B2B|GIR|ACCURACY|POWER", L)
    )
    rows = []
    for _, p in field.iterrows():
        pname = str(p.get("name_adjusted") or p.get("player_name") or "")
        pkey = name_key(pname)
        if not pkey:
            continue
        val = season_value_for_player(pga, dp, pkey, cols)
        if val is None:
            continue
        rows.append((pkey, val))
    if not rows:
        print(f"  No values for key stat '{label}' cols={cols}")
        return {}
    rows.sort(key=lambda x: x[1], reverse=not lower_better)
    out = {}
    for i, (pk, _) in enumerate(rows, start=1):
        out[pk] = i
    print(f"  Ranked {len(out)} players on '{label}'")
    return out


def resolve_course(logs: pd.DataFrame, event_name: str) -> str:
    event_name = str(event_name or "")
    if "course_name" not in logs.columns:
        return ""
    for ev_re, course_re, canonical in COURSE_HINTS:
        if ev_re.search(event_name):
            courses = logs["course_name"].dropna().astype(str)
            hit = courses[courses.str.contains(course_re)]
            if len(hit):
                return hit.value_counts().index[0]
            return canonical
    if event_name and "event_name" in logs.columns:
        tokens = [
            t
            for t in re.split(r"[^a-z0-9]+", event_name.lower())
            if len(t) > 3 and t not in {"classic", "open", "championship"}
        ]
        mask = pd.Series(False, index=logs.index)
        en = logs["event_name"].astype(str).str.lower()
        for t in tokens[:3]:
            mask = mask | en.str.contains(t, na=False)
        sub = logs.loc[mask, "course_name"].dropna().astype(str)
        if len(sub):
            return sub.value_counts().index[0]
    return str(logs["course_name"].dropna().astype(str).value_counts().index[0])


def event_level(player_logs: pd.DataFrame) -> pd.DataFrame:
    if player_logs.empty:
        return player_logs
    df = player_logs.copy()
    if "event_name" not in df.columns:
        df["event_name"] = ""
    df["_ename"] = (
        df["event_name"].fillna("").astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    )
    df["_ename_norm"] = (
        df["_ename"]
        .str.lower()
        .str.replace(r"rocket mortgage classic", "rocket classic", regex=False)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    year = pd.Series([None] * len(df), index=df.index, dtype="object")
    if "year" in df.columns:
        year = pd.to_numeric(df["year"], errors="coerce")
    if "season" in df.columns:
        year = year.fillna(pd.to_numeric(df["season"], errors="coerce"))
    dt = pd.Series(pd.NaT, index=df.index)
    for col in ("event_completed", "date", "Date", "outright_event_completed"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            dt = dt.fillna(parsed)
    if year is not None:
        year_dt = pd.to_datetime(
            year.apply(lambda y: f"{int(y)}-07-01" if pd.notna(y) else None),
            errors="coerce",
        )
        dt = dt.fillna(year_dt)
    df["_dt"] = dt
    df["_day"] = df["_dt"].dt.strftime("%Y-%m-%d").fillna("")
    df["_year"] = df["_dt"].dt.year
    if year is not None:
        df["_year"] = df["_year"].fillna(year)
    if "event_id" in df.columns:
        eid = df["event_id"].astype(str).str.strip()
        df["_eid"] = eid.where(
            df["event_id"].notna() & ~eid.isin(["", "nan", "None", "NaN"]),
            other="",
        )
    else:
        df["_eid"] = ""

    def make_key(r):
        if r["_eid"]:
            return f"id:{r['_eid']}"
        day = r["_day"]
        yr = r["_year"]
        name = r["_ename_norm"] or r["_ename"]
        if day:
            return f"nm:{name}|{day}"
        if pd.notna(yr):
            return f"nm:{name}|y{int(yr)}"
        return f"nm:{name}|unk"

    df["_gkey"] = df.apply(make_key, axis=1)
    rows = []
    for _, g in df.groupby("_gkey", dropna=False):
        fin_series = g["fin_text"] if "fin_text" in g.columns else pd.Series([""] * len(g), index=g.index)
        with_fin = g[fin_series.notna() & (fin_series.astype(str).str.strip() != "")]
        first = with_fin.iloc[0] if len(with_fin) else g.iloc[0]
        fin = first["fin_text"] if "fin_text" in g.columns else ""
        label, num, is_cut = parse_finish(fin)
        day = first["_day"] if first["_day"] else ""
        yr = first["_year"] if pd.notna(first["_year"]) else None
        course = (
            str(first["course_name"])
            if "course_name" in g.columns and pd.notna(first.get("course_name", None))
            else ""
        )
        sg = pd.to_numeric(g["sg_total"], errors="coerce").mean() if "sg_total" in g.columns else None
        rows.append(
            {
                "event_name": first["_ename"],
                "event_name_norm": first["_ename_norm"],
                "event_completed": day or (f"{int(yr)}-07-01" if yr is not None and pd.notna(yr) else ""),
                "year": int(yr) if yr is not None and pd.notna(yr) else None,
                "course_name": course,
                "fin_label": label,
                "fin_num": num,
                "is_cut": is_cut,
                "sg_total": sg,
                "_dt": first["_dt"],
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_dedupe"] = (
        out["event_name_norm"].astype(str)
        + "|"
        + out["event_completed"].astype(str)
        + "|"
        + out["year"].astype(str)
    )
    out = out.sort_values(["_dt", "fin_num"], ascending=[False, True], na_position="last")
    out = out.drop_duplicates(subset=["_dedupe"], keep="first")
    out = out.sort_values("_dt", ascending=False, na_position="last")
    return out.drop(columns=["_dedupe"], errors="ignore")


def course_match_mask(course_series: pd.Series, course: str) -> pd.Series:
    """Match logs to the target course. Prefer tight name match to avoid wrong venues."""
    if not course:
        return pd.Series(False, index=course_series.index)
    cn = course_series.fillna("").astype(str).str.lower().str.strip()
    target = course.lower().strip()
    # Exact / strong contains
    mask = cn.eq(target) | cn.str.contains(re.escape(target), na=False, regex=True)
    # Canonical short forms
    if "detroit" in target:
        mask = cn.str.contains(r"detroit\s*golf", na=False, regex=True) | cn.eq("detroit golf club")
    elif "muirfield" in target:
        mask = cn.str.contains("muirfield", na=False)
    elif "river highland" in target or "tpc river" in target:
        mask = cn.str.contains(r"river\s*highland", na=False, regex=True)
    else:
        tokens = [t for t in re.split(r"[^a-z0-9]+", target) if len(t) > 3]
        if tokens:
            tok_mask = pd.Series(True, index=cn.index)
            for t in tokens[:3]:
                tok_mask = tok_mask & cn.str.contains(t, na=False)
            mask = mask | tok_mask
    return mask


def course_event_level(player_course_logs: pd.DataFrame) -> pd.DataFrame:
    """One finish per YEAR at this course (most recent year first).

    Groups by year (not by calendar day) so multi-round logs with mixed
    fin_text / event_completed values don't produce fake CUT rows.
    Within a year, prefers the best real finish over CUT/WD.
    """
    if player_course_logs is None or player_course_logs.empty:
        return pd.DataFrame(
            columns=[
                "event_name",
                "event_completed",
                "year",
                "course_name",
                "fin_label",
                "fin_num",
                "is_cut",
                "sg_total",
                "_dt",
            ]
        )
    df = player_course_logs.copy()
    dt = pd.Series(pd.NaT, index=df.index)
    for col in ("event_completed", "date", "Date", "outright_event_completed"):
        if col in df.columns:
            dt = dt.fillna(pd.to_datetime(df[col], errors="coerce"))
    year = pd.Series([pd.NA] * len(df), index=df.index, dtype="Float64")
    if "year" in df.columns:
        year = year.fillna(pd.to_numeric(df["year"], errors="coerce"))
    if "season" in df.columns:
        year = year.fillna(pd.to_numeric(df["season"], errors="coerce"))
    year = year.fillna(dt.dt.year)
    year_anchor = year.apply(lambda y: f"{int(y)}-07-01" if pd.notna(y) else None)
    dt = dt.fillna(pd.to_datetime(year_anchor, errors="coerce"))
    df["_dt"] = dt
    df["_year"] = year

    rows = []
    # Group by year — one Rocket Classic (etc.) per season at this course
    for yr, g in df.groupby(df["_year"], dropna=False):
        # Evaluate every fin_text in the year; pick best real finish
        candidates = []
        fin_series = g["fin_text"] if "fin_text" in g.columns else pd.Series([""] * len(g), index=g.index)
        for idx, raw in fin_series.items():
            label, num, is_cut = parse_finish(raw)
            if label == "—" and num is None:
                continue
            candidates.append((label, num, is_cut, idx))

        if not candidates:
            first = g.iloc[0]
            label, num, is_cut = "—", None, False
            pick_idx = first.name
        else:
            # Prefer made-cut finishes. Use the most common finish label among
            # made-cut rows (guards against stray round-level CUT noise).
            made = [c for c in candidates if not c[2] and c[1] is not None and c[1] < 100]
            if made:
                from collections import Counter
                label_counts = Counter(c[0] for c in made)
                best_label = label_counts.most_common(1)[0][0]
                made_best = [c for c in made if c[0] == best_label]
                made_best.sort(key=lambda c: c[1])
                label, num, is_cut, pick_idx = made_best[0]
            else:
                # all cuts/wd — only mark CUT if that's truly the only outcome
                label, num, is_cut, pick_idx = candidates[0]

        first = g.loc[pick_idx] if pick_idx in g.index else g.iloc[0]
        yr_int = int(yr) if pd.notna(yr) else None
        rows.append(
            {
                "event_name": first["event_name"] if "event_name" in g.columns else "",
                "event_completed": (
                    first["_dt"].strftime("%Y-%m-%d")
                    if pd.notna(first["_dt"])
                    else (f"{yr_int}-01-01" if yr_int else "")
                ),
                "year": yr_int,
                "course_name": first["course_name"] if "course_name" in g.columns else "",
                "fin_label": label,
                "fin_num": num,
                "is_cut": is_cut,
                "sg_total": pd.to_numeric(g["sg_total"], errors="coerce").mean()
                if "sg_total" in g.columns
                else None,
                "_dt": first["_dt"],
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Most recent year first; one row per year
    out = out.sort_values(["year", "_dt"], ascending=[False, False], na_position="last")
    out = out.drop_duplicates(subset=["year"], keep="first")
    return out.sort_values(["year", "_dt"], ascending=[False, False], na_position="last")


def course_anchor_years(course_logs: pd.DataFrame, n: int = 4) -> list[int]:
    """Most recent N years this course hosted a counted PGA event (desc).

    H1 = most recent completed edition at the course (e.g. 2025 during 2026 season),
    not the player's personal last start year.
    """
    if course_logs is None or course_logs.empty:
        # Sensible default for a mid-2026 season tool
        return list(range(2025, 2025 - n, -1))
    years = set()
    for col in ("year", "season"):
        if col in course_logs.columns:
            for y in pd.to_numeric(course_logs[col], errors="coerce").dropna().unique():
                yi = int(y)
                if 2000 <= yi <= 2100:
                    years.add(yi)
    if "event_completed" in course_logs.columns:
        dt = pd.to_datetime(course_logs["event_completed"], errors="coerce")
        for y in dt.dt.year.dropna().unique():
            yi = int(y)
            if 2000 <= yi <= 2100:
                years.add(yi)
    if not years:
        return list(range(2025, 2025 - n, -1))
    ordered = sorted(years, reverse=True)
    # Drop pure-future noise if any; keep most recent completed
    return ordered[:n]


def finish_by_year(cev: pd.DataFrame) -> dict:
    """Map year -> (fin_label, fin_num, is_cut) from course_event_level output."""
    out = {}
    if cev is None or cev.empty:
        return out
    for er in cev.itertuples(index=False):
        yr = getattr(er, "year", None)
        if yr is None or (isinstance(yr, float) and pd.isna(yr)):
            continue
        yi = int(yr)
        # first write wins (cev is sorted newest first); don't overwrite better with worse
        if yi in out:
            prev = out[yi]
            # prefer non-cut over cut
            if prev[2] and not er.is_cut:
                out[yi] = (er.fin_label, er.fin_num, er.is_cut)
            continue
        out[yi] = (er.fin_label, er.fin_num, bool(getattr(er, "is_cut", False)))
    return out



def build_cheat_for_scope(scope: str, logs: pd.DataFrame, pga: pd.DataFrame, dp: pd.DataFrame) -> None:
    field = load_field(scope)
    if field is None or field.empty:
        print(f"No field for scope={scope}")
        return

    if "name_adjusted" not in field.columns:
        field["name_adjusted"] = field.get("player_name", "")
    if "player_name" not in field.columns:
        field["player_name"] = field.get("name_adjusted", "")

    event_name = str(field.iloc[0].get("event_name") or field.iloc[0].get("event") or "")
    course_from_field = str(field.iloc[0].get("course_name") or "").strip()
    preview = load_preview(event_name, course_from_field)
    preview_course = str(preview.get("COURSE NAME") or preview.get("Course Name") or "").strip()
    preview_tourn = str(preview.get("TOURNAMENT") or preview.get("Tournament") or "").strip()

    # Course name source of truth = LIVE FIELD (e.g. TPC Southwind for FedEx).
    # Never let a stale pre_tournament_preview row (e.g. SEDGEFIELD / Wyndham)
    # override the field when the tournament names don't match.
    def _names_close(a: str, b: str) -> bool:
        al = re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()
        bl = re.sub(r"[^a-z0-9]+", " ", (b or "").lower()).strip()
        if not al or not bl:
            return False
        if al == bl or al in bl or bl in al:
            return True
        at, bt = set(al.split()), set(bl.split())
        stop = {"the", "championship", "classic", "open", "presented", "by", "golf", "club", "country", "tpc"}
        at, bt = at - stop, bt - stop
        return bool(at & bt) and len(at & bt) >= min(2, len(at), len(bt))

    preview_ok = bool(preview_course) and (
        _names_close(preview_tourn, event_name) or _names_close(preview_course, course_from_field)
    )
    if course_from_field:
        course = course_from_field
        if preview_course and not preview_ok:
            print(
                f"Ignoring stale preview course {preview_course!r} "
                f"(preview event {preview_tourn!r} vs field {event_name!r})"
            )
    elif preview_ok:
        course = preview_course
    else:
        course = resolve_course(logs, event_name)

    # Canonical name when hints know this event (keeps CH matching stable)
    for ev_re, course_re, canonical in COURSE_HINTS:
        if ev_re.search(event_name or ""):
            if not course or course_re.search(course) or not course_from_field:
                # Prefer field string if it already matches; else canonical
                if course_from_field and course_re.search(course_from_field):
                    course = course_from_field
                elif not course_from_field:
                    course = canonical
            break

    print(f"Event: {event_name}")
    print(f"Course: {course} (field={course_from_field!r}, preview={preview_course!r})")

    # Prefer scoped field logs when present
    fl = scoped_path(DATA, "field_player_logs.csv", scope)
    if fl.exists():
        try:
            scoped_logs = pd.read_csv(fl, low_memory=False)
            if len(scoped_logs):
                print(f"Using scoped logs {fl.name}: {len(scoped_logs)} rows")
                logs = scoped_logs
        except Exception as e:
            print("scoped logs load failed:", e)

    # KEY STAT labels → rank maps
    key_labels = []
    key_rank_maps = []
    for i in range(1, 6):
        lab = str(preview.get(f"KEY STAT {i}") or preview.get(f"Key Stat {i}") or "").strip()
        key_labels.append(lab)
        key_rank_maps.append(rank_field_on_stat(field, pga, dp, lab) if lab else {})

    logs = logs.copy()
    logs["_pkey"] = logs["player_name"].map(name_key) if "player_name" in logs.columns else ""
    if "name_adjusted" in logs.columns:
        alt = logs["name_adjusted"].map(name_key)
        logs["_pkey"] = logs["_pkey"].where(logs["_pkey"].astype(bool), alt)

    if course and "course_name" in logs.columns:
        course_logs = logs[course_match_mask(logs["course_name"], course)]
    else:
        course_logs = logs.iloc[0:0]
    print(f"Course log rows: {len(course_logs)} for '{course}'")
    anchor_years = course_anchor_years(course_logs, 4)
    while len(anchor_years) < 4:
        anchor_years.append(anchor_years[-1] - 1 if anchor_years else 2025)
    print(f"H1-H4 years (course editions): {anchor_years}")

    cheat_rows = []
    ch_rows = []
    for _, p in field.iterrows():
        pname = str(p.get("name_adjusted") or p.get("player_name") or "")
        pkey = name_key(pname)
        if not pkey:
            continue
        plogs = logs[logs["_pkey"] == pkey]
        ev = event_level(plogs)
        # Sort most recent first (event_level should already, but be safe)
        if len(ev) and "_dt" in ev.columns:
            ev = ev.sort_values("_dt", ascending=False, na_position="last").reset_index(drop=True)

        rf = []
        rf_sgs = []
        for i, er in enumerate(ev.itertuples(index=False)):
            if i >= 7:
                break
            lab = er.fin_label
            sg = getattr(er, "sg_total", None)
            if sg is not None and pd.notna(sg):
                try:
                    v = float(sg)
                    rf.append(f"{lab} ({v:+.2f})" if v != 0 else f"{lab} (0.00)")
                    rf_sgs.append(v)
                except Exception:
                    rf.append(lab)
            else:
                rf.append(lab)
        while len(rf) < 7:
            rf.append("—")

        # Combined event SG over last 7 starts (CUT weeks included at their 2-round mean)
        if rf_sgs:
            value = round(float(sum(rf_sgs)), 2)
            value_avg = round(float(sum(rf_sgs) / len(rf_sgs)), 2)
        else:
            # Fall back to field/recent_form if logs missing SG
            try:
                value = float(p.get("value")) if p.get("value") is not None and str(p.get("value")) != "" else None
            except Exception:
                value = None
            value_avg = None

        cut_streak = 0
        for er in ev.itertuples(index=False):
            lab = str(er.fin_label or "").upper()
            if lab in {"CUT", "MC", "WD", "DQ", "—", ""}:
                break
            cut_streak += 1

        plogs_course = course_logs[course_logs["_pkey"] == pkey] if len(course_logs) else logs.iloc[0:0]
        cev = course_event_level(plogs_course)
        by_year = finish_by_year(cev)

        h = []
        h_years = []
        finishes_num = []
        for yr in anchor_years[:4]:
            h_years.append(yr)
            if yr in by_year:
                label, num, is_cut = by_year[yr]
                h.append(label if label else "—")
                if num is not None and num < 100:
                    finishes_num.append(int(num))
                elif is_cut:
                    finishes_num.append(100)
            else:
                h.append("—")
        while len(h) < 4:
            h.append("—")
            h_years.append(None)
        avg_fin = sum(finishes_num) / len(finishes_num) if finishes_num else None

        s_ranks = [m.get(pkey) for m in key_rank_maps]

        cheat_rows.append(
            {
                "player_name": p.get("player_name") or pname,
                "name_adjusted": pname,
                "event_name": event_name,
                "course_name": course,
                "h1": h[0],
                "h2": h[1],
                "h3": h[2],
                "h4": h[3],
                "h1_year": h_years[0],
                "h2_year": h_years[1],
                "h3_year": h_years[2],
                "h4_year": h_years[3],
                "rf1": rf[0],
                "rf2": rf[1],
                "rf3": rf[2],
                "rf4": rf[3],
                "rf5": rf[4],
                "rf6": rf[5],
                "rf7": rf[6],
                "cut_streak": cut_streak,
                "avg_finish_course": avg_fin,
                "starts_at_course": len(cev),
                "value": value if value is not None else p.get("value"),
                "value_avg": value_avg,
                "value_rank": None,  # filled after full field ranked by value
                "salary": p.get("salary", ""),
                "s1": s_ranks[0],
                "s2": s_ranks[1],
                "s3": s_ranks[2],
                "s4": s_ranks[3],
                "s5": s_ranks[4],
                "key_stat_1": key_labels[0],
                "key_stat_2": key_labels[1],
                "key_stat_3": key_labels[2],
                "key_stat_4": key_labels[3],
                "key_stat_5": key_labels[4],
            }
        )
        ch_rows.append(
            {
                "player_name": pname,
                "course_name": course,
                "event_name": event_name,
                "h1": h[0],
                "h2": h[1],
                "h3": h[2],
                "h4": h[3],
                "avg_finish": avg_fin,
                "starts": len(cev),
                "last_sg": cev.iloc[0]["sg_total"] if len(cev) else None,
            }
        )

    cheat = pd.DataFrame(cheat_rows)
    ch = pd.DataFrame(ch_rows)
    if "avg_finish_course" in cheat.columns and len(cheat):
        cheat["ch_rank"] = cheat["avg_finish_course"].rank(method="min")

    # RF rank = combined event SG Total last 7 starts (higher better → rank 1)
    if len(cheat) and "value" in cheat.columns:
        cheat["value"] = pd.to_numeric(cheat["value"], errors="coerce")
        cheat["value_rank"] = cheat["value"].rank(ascending=False, method="min").astype("Int64")
        cheat = cheat.sort_values("value_rank", na_position="last")

    out1 = scoped_path(DATA, "cheat_sheet.csv", scope)
    out2 = scoped_path(DATA, "course_history_summary.csv", scope)
    cheat.to_csv(out1, index=False)
    ch.to_csv(out2, index=False)
    print(f"Wrote {out1} ({len(cheat)} rows)")
    print(f"Wrote {out2} ({len(ch)} rows)")
    if len(cheat):
        print("Sample S1-S5:", cheat[["name_adjusted", "s1", "s2", "s3", "s4", "s5"]].head(3).to_dict("records"))


def main(argv=None):
    scopes = parse_scopes(argv)
    print("Loading logs once…")
    logs = load_logs()
    pga, dp = load_season_stats()
    for scope in scopes:
        print(f"\n=== Cheat sheet scope={scope} ===")
        build_cheat_for_scope(scope, logs, pga, dp)


if __name__ == "__main__":
    main()
