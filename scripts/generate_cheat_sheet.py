#!/usr/bin/env python3
"""
Generate lightweight Cheat Sheet + Course History CSVs from field logs + upcoming field.
Run in GitHub Actions so the browser tool does not parse 70k+ log rows.

Outputs:
  data/cheat_sheet.csv
  data/course_history_summary.csv
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from collections import defaultdict

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# Event → course hints for renamed tournaments
COURSE_HINTS = [
    (re.compile(r"rocket", re.I), re.compile(r"detroit", re.I), "Detroit Golf Club"),
    (re.compile(r"memorial", re.I), re.compile(r"muirfield", re.I), "Muirfield Village"),
    (re.compile(r"travelers", re.I), re.compile(r"river highland|tpc river", re.I), "TPC River Highlands"),
    (re.compile(r"john deere", re.I), re.compile(r"deere", re.I), "TPC Deere Run"),
    (re.compile(r"arnold palmer", re.I), re.compile(r"bay hill", re.I), "Bay Hill"),
    (re.compile(r"genesis", re.I), re.compile(r"riviera", re.I), "Riviera Country Club"),
    (re.compile(r"players", re.I), re.compile(r"sawgrass", re.I), "TPC Sawgrass"),
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


def load_logs() -> pd.DataFrame:
    frames = []
    # Prefer the combined field logs if present
    candidates = [
        DATA / "field_player_logs.csv",
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
            print(f"  skip {path.name}: {e}")
    if not frames:
        raise SystemExit("No log CSVs found in data/")
    logs = pd.concat(frames, ignore_index=True)
    print(f"Total log rows: {len(logs)}")
    return logs


def load_field() -> pd.DataFrame:
    for name in ("recent_form.csv", "upcoming_field.csv", "field.csv"):
        p = DATA / name
        if p.exists():
            df = pd.read_csv(p, low_memory=False)
            print(f"Field from {name}: {len(df)} rows")
            return df
    raise SystemExit("No field file (recent_form.csv) found")


def resolve_course(logs: pd.DataFrame, event_name: str) -> str:
    event_name = str(event_name or "")
    if "course_name" not in logs.columns:
        return ""
    # Hint match
    for ev_re, course_re, canonical in COURSE_HINTS:
        if ev_re.search(event_name):
            courses = logs["course_name"].dropna().astype(str)
            hit = courses[courses.str.contains(course_re)]
            if len(hit):
                return hit.value_counts().index[0]
            return canonical
    # Event name overlap
    if event_name and "event_name" in logs.columns:
        tokens = [t for t in re.split(r"[^a-z0-9]+", event_name.lower()) if len(t) > 3 and t not in {"classic", "open", "championship"}]
        mask = pd.Series(False, index=logs.index)
        en = logs["event_name"].astype(str).str.lower()
        for t in tokens[:3]:
            mask = mask | en.str.contains(t, na=False)
        sub = logs.loc[mask, "course_name"].dropna().astype(str)
        if len(sub):
            return sub.value_counts().index[0]
    # Fallback most common course among recent years
    return str(logs["course_name"].dropna().astype(str).value_counts().index[0])


def event_level(player_logs: pd.DataFrame) -> pd.DataFrame:
    """Collapse rounds → one row per tournament start for a player.

    Grouping rules:
    - Prefer event_id when it is present and non-empty
    - Else event_name + calendar date
    - Else event_name + year (so multi-year course history does not collapse)
    Never rely on blank event_id alone (NaNs split every round into its own group).
    """
    if player_logs.empty:
        return player_logs

    df = player_logs.copy()

    if "event_name" not in df.columns:
        df["event_name"] = ""
    df["_ename"] = (
        df["event_name"].fillna("").astype(str).str.strip()
        .str.replace(r"\s+", " ", regex=True)
    )
    # Normalize renamed events so "Rocket Classic" and "Rocket Mortgage Classic" share history
    df["_ename_norm"] = (
        df["_ename"].str.lower()
        .str.replace(r"rocket mortgage classic", "rocket classic", regex=False)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    # Year
    year = pd.Series([None] * len(df), index=df.index, dtype="object")
    if "year" in df.columns:
        year = pd.to_numeric(df["year"], errors="coerce")
    if "season" in df.columns:
        year = year.fillna(pd.to_numeric(df["season"], errors="coerce"))

    # Date → calendar day
    dt = pd.Series(pd.NaT, index=df.index)
    for col in ("event_completed", "date", "Date", "outright_event_completed"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            dt = dt.fillna(parsed)
    # If still no date, synthesize from year mid-season so years stay distinct
    if year is not None:
        year_dt = pd.to_datetime(
            year.apply(lambda y: f"{int(y)}-07-01" if pd.notna(y) else None),
            errors="coerce",
        )
        dt = dt.fillna(year_dt)

    df["_dt"] = dt
    df["_day"] = df["_dt"].dt.strftime("%Y-%m-%d").fillna("")
    df["_year"] = df["_dt"].dt.year
    # fill year from column if dt failed
    if year is not None:
        df["_year"] = df["_year"].fillna(year)

    # event_id only when real
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
        course = str(first["course_name"]) if "course_name" in g.columns and pd.notna(first.get("course_name", None)) else ""
        sg = pd.to_numeric(g["sg_total"], errors="coerce").mean() if "sg_total" in g.columns else None

        rows.append({
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
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # De-dupe only exact same key components (name + day/year) — already unique via _gkey,
    # but guard against identical rows
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
    """Fuzzy match course names (Detroit Golf Club, etc.)."""
    if not course:
        return pd.Series(False, index=course_series.index)
    cn = course_series.fillna("").astype(str).str.lower().str.strip()
    target = course.lower().strip()
    # token overlap: require main tokens
    tokens = [t for t in re.split(r"[^a-z0-9]+", target) if len(t) > 3]
    mask = cn.eq(target) | cn.str.contains(re.escape(target[: min(12, len(target))]), na=False)
    if tokens:
        tok_mask = pd.Series(True, index=cn.index)
        for t in tokens[:3]:
            tok_mask = tok_mask & cn.str.contains(t, na=False)
        mask = mask | tok_mask
    # Detroit special-case
    if "detroit" in target:
        mask = mask | cn.str.contains("detroit", na=False)
    return mask



def main():
    logs = load_logs()
    field = load_field()

    # Normalize player name columns
    if "name_adjusted" not in field.columns:
        field["name_adjusted"] = field.get("player_name", "")
    if "player_name" not in field.columns:
        field["player_name"] = field.get("name_adjusted", "")

    event_name = str(field.iloc[0].get("event_name") or field.iloc[0].get("event") or "")
    course = resolve_course(logs, event_name)
    print(f"Event: {event_name}")
    print(f"Course: {course}")

    logs = logs.copy()
    logs["_pkey"] = logs["player_name"].map(name_key) if "player_name" in logs.columns else ""
    if "name_adjusted" in logs.columns:
        alt = logs["name_adjusted"].map(name_key)
        logs["_pkey"] = logs["_pkey"].where(logs["_pkey"].astype(bool), alt)

    # Course-filtered logs (fuzzy name match — keeps multi-year history)
    if course and "course_name" in logs.columns:
        course_logs = logs[course_match_mask(logs["course_name"], course)]
    else:
        course_logs = logs.iloc[0:0]
    print(f"Course log rows: {len(course_logs)} for '{course}'")

    cheat_rows = []
    ch_rows = []

    for _, p in field.iterrows():
        pname = str(p.get("name_adjusted") or p.get("player_name") or "")
        pkey = name_key(pname)
        if not pkey:
            continue
        plogs = logs[logs["_pkey"] == pkey]
        # Recent form: last 7 events
        ev = event_level(plogs)
        rf = []
        for i, er in enumerate(ev.itertuples(index=False)):
            if i >= 7:
                break
            rf.append(er.fin_label)
        while len(rf) < 7:
            rf.append("—")

        # Made-cut streak: consecutive made cuts from most recent event backward
        cut_streak = 0
        for er in ev.itertuples(index=False):
            lab = str(er.fin_label or "").upper()
            if lab in {"CUT", "MC", "WD", "DQ", "—", ""}:
                break
            cut_streak += 1

        # Course history last 4 at this course
        plogs_course = course_logs[course_logs["_pkey"] == pkey] if len(course_logs) else logs.iloc[0:0]
        cev = event_level(plogs_course)
        # Keep up to last 4 starts at this course (already sorted newest first)
        h = []
        finishes_num = []
        for i, er in enumerate(cev.itertuples(index=False)):
            if i < 4:
                h.append(er.fin_label)
            if er.fin_num is not None and er.fin_num < 100:
                finishes_num.append(er.fin_num)
            elif er.is_cut:
                finishes_num.append(100)
        while len(h) < 4:
            h.append("—")
        avg_fin = sum(finishes_num) / len(finishes_num) if finishes_num else None

        # Carry value_rank from recent_form if present
        value_rank = p.get("value_rank")
        value = p.get("value")

        cheat_rows.append({
            "player_name": p.get("player_name") or pname,
            "name_adjusted": pname,
            "event_name": event_name,
            "course_name": course,
            "h1": h[0], "h2": h[1], "h3": h[2], "h4": h[3],
            "rf1": rf[0], "rf2": rf[1], "rf3": rf[2], "rf4": rf[3],
            "rf5": rf[4], "rf6": rf[5], "rf7": rf[6],
            "cut_streak": cut_streak,
            "avg_finish_course": avg_fin,
            "starts_at_course": len(cev),
            "value": value,
            "value_rank": value_rank,
            "salary": p.get("salary", ""),
        })

        ch_rows.append({
            "player_name": pname,
            "course_name": course,
            "event_name": event_name,
            "h1": h[0], "h2": h[1], "h3": h[2], "h4": h[4-1],
            "avg_finish": avg_fin,
            "starts": len(cev),
            "last_sg": cev.iloc[0]["sg_total"] if len(cev) else None,
        })

    cheat = pd.DataFrame(cheat_rows)
    ch = pd.DataFrame(ch_rows)

    # Rank by course avg finish among those with history
    if "avg_finish_course" in cheat.columns:
        ranked = cheat["avg_finish_course"].rank(method="min")
        cheat["ch_rank"] = ranked

    out1 = DATA / "cheat_sheet.csv"
    out2 = DATA / "course_history_summary.csv"
    cheat.to_csv(out1, index=False)
    ch.to_csv(out2, index=False)
    print(f"Wrote {out1} ({len(cheat)} rows)")
    print(f"Wrote {out2} ({len(ch)} rows)")


if __name__ == "__main__":
    main()
