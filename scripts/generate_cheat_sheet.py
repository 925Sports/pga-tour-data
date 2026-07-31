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
    """Collapse rounds → one row per event for a player.

    Important: do NOT group by event_id when it is blank/NaN — pandas treats
    each NaN as its own group, which duplicates every round as a separate
    "event" (e.g. RF 1,1,30,30 instead of 1,30,...).
    Group by event_name + calendar date only.
    """
    if player_logs.empty:
        return player_logs

    df = player_logs.copy()

    # Normalize event name
    if "event_name" not in df.columns:
        df["event_name"] = ""
    df["_ename"] = df["event_name"].fillna("").astype(str).str.strip()

    # Prefer a real date column; strip to calendar day so round times don't split events
    date_raw = None
    for col in ("event_completed", "date", "Date", "outright_event_completed"):
        if col in df.columns:
            date_raw = col
            break
    if date_raw is not None:
        dt = pd.to_datetime(df[date_raw], errors="coerce")
    else:
        dt = pd.Series(pd.NaT, index=df.index)
    # Fallback year if no usable date
    if "year" in df.columns:
        year_fallback = pd.to_datetime(df["year"].astype(str) + "-06-15", errors="coerce")
        dt = dt.fillna(year_fallback)
    df["_dt"] = dt
    df["_day"] = df["_dt"].dt.strftime("%Y-%m-%d").fillna("")

    # Optional: stable event key from id only when present and non-empty
    if "event_id" in df.columns:
        eid = df["event_id"]
        df["_eid"] = eid.where(eid.notna() & (eid.astype(str).str.strip() != ""), other="")
    else:
        df["_eid"] = ""

    # Group key: use event_id when available, else event_name + day
    df["_gkey"] = df.apply(
        lambda r: f"id:{r['_eid']}" if r["_eid"] not in ("", "nan", "None") else f"nm:{r['_ename']}|{r['_day']}",
        axis=1,
    )

    rows = []
    for _, g in df.groupby("_gkey", dropna=False):
        # Prefer a row that has fin_text populated
        fin_series = g["fin_text"] if "fin_text" in g.columns else pd.Series([""] * len(g))
        with_fin = g[fin_series.notna() & (fin_series.astype(str).str.strip() != "")]
        first = with_fin.iloc[0] if len(with_fin) else g.iloc[0]
        fin = first.get("fin_text", "") if hasattr(first, "get") else first["fin_text"] if "fin_text" in g.columns else ""
        label, num, is_cut = parse_finish(fin)
        date = first["_day"] if "_day" in g.columns else ""
        if not date and pd.notna(first.get("_dt") if hasattr(first, "get") else first["_dt"]):
            date = str(first["_dt"])[:10]
        course = ""
        if "course_name" in g.columns:
            course = str(first["course_name"] if "course_name" in g.columns else "")
        sg = None
        if "sg_total" in g.columns:
            sg = pd.to_numeric(g["sg_total"], errors="coerce").mean()
        rows.append({
            "event_name": first["_ename"] if "_ename" in g.columns else first.get("event_name", ""),
            "event_completed": date,
            "course_name": course,
            "fin_label": label,
            "fin_num": num,
            "is_cut": is_cut,
            "sg_total": sg,
            "_dt": first["_dt"] if "_dt" in g.columns else pd.NaT,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # Final de-dupe: same event_name + day → keep one (prefer non-CUT if mixed, else first)
    out["_dedupe"] = out["event_name"].astype(str).str.lower().str.strip() + "|" + out["event_completed"].astype(str)
    out = out.sort_values(["_dt", "fin_num"], ascending=[False, True], na_position="last")
    out = out.drop_duplicates(subset=["_dedupe"], keep="first")
    out = out.sort_values("_dt", ascending=False)
    return out.drop(columns=["_dedupe"], errors="ignore")


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

    # Course-filtered logs
    course_l = course.lower()
    if course and "course_name" in logs.columns:
        cn = logs["course_name"].astype(str).str.lower()
        course_logs = logs[cn.str.contains(re.escape(course_l[:12]), na=False) | cn.eq(course_l)]
    else:
        course_logs = logs.iloc[0:0]
    print(f"Course log rows: {len(course_logs)}")

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
        cev = event_level(course_logs[course_logs["_pkey"] == pkey] if len(course_logs) else plogs.iloc[0:0])
        # Prefer same-course events only
        if "course_name" in (cev.columns if len(cev) else []):
            pass
        h = []
        finishes_num = []
        for i, er in enumerate(cev.itertuples(index=False)):
            if i < 4:
                h.append(er.fin_label)
            if er.fin_num is not None:
                finishes_num.append(er.fin_num)
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
