#!/usr/bin/env python3
"""
Shared PGA field selection for 925Sports/pga-tour-data generators.

Supports BOTH:
  - current  → this week's event (in progress, or next if between weeks)
  - upcoming → the following PGA Tour event on the sheet (tour=upcoming_pga or next after current)

The Google field sheet often tags:
  tour=pga           → week that already started / just finished
  tour=upcoming_pga  → next tournament field

Old bug: filter tour=="pga" only + take earliest date_start → stuck on finished Rocket
while Wyndham sat as upcoming_pga.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd

PGA_TOURS = {"pga", "upcoming_pga", "pgat", "pga_tour", "pga tour"}
SKIP_TOUR_RE = re.compile(r"kft|korn|liv|opp|euro|dp\b|asian|champions|senior", re.I)


def today_ts() -> pd.Timestamp:
    return pd.Timestamp(datetime.utcnow().date())


def _norm_tour(s) -> str:
    return str(s or "").strip().lower()


def is_pga_tour(tour: str) -> bool:
    t = _norm_tour(tour)
    if t in PGA_TOURS:
        return True
    if SKIP_TOUR_RE.search(t):
        return False
    # bare "pga" variants
    return t.startswith("pga") or t.endswith("_pga") or t == "main"


def output_suffix(scope: str) -> str:
    """File suffix: current → '' (primary hub paths); upcoming → '_upcoming'."""
    s = (scope or "current").strip().lower()
    if s in ("upcoming", "next", "upcoming_pga"):
        return "_upcoming"
    return ""


def scoped_path(data_dir: Path, basename: str, scope: str) -> Path:
    """
    recent_form.csv / recent_form_upcoming.csv
    field_player_logs.csv / field_player_logs_upcoming.csv
    """
    stem, ext = basename, ""
    if "." in basename:
        stem, ext = basename.rsplit(".", 1)
        ext = "." + ext
    return Path(data_dir) / f"{stem}{output_suffix(scope)}{ext}"


def parse_scopes(argv: Optional[Sequence[str]] = None) -> List[str]:
    """
    CLI / env:
      --scope current|upcoming|both
      EVENT_SCOPE=current|upcoming|both
    Default: both (generate this week + next week every run).
    """
    argv = list(argv if argv is not None else sys.argv[1:])
    env = (os.environ.get("EVENT_SCOPE") or os.environ.get("FIELD_SCOPE") or "").strip().lower()
    scope = env or "both"
    for i, a in enumerate(argv):
        if a in ("--scope", "--event-scope", "-s") and i + 1 < len(argv):
            scope = argv[i + 1].strip().lower()
        elif a.startswith("--scope="):
            scope = a.split("=", 1)[1].strip().lower()
        elif a in ("--current",):
            scope = "current"
        elif a in ("--upcoming", "--next"):
            scope = "upcoming"
        elif a in ("--both", "--all"):
            scope = "both"
    if scope in ("both", "all", "current+upcoming", "current_and_upcoming"):
        return ["current", "upcoming"]
    if scope in ("upcoming", "next", "upcoming_pga"):
        return ["upcoming"]
    return ["current"]


def _attach_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ds = next((c for c in ["date_start", "Date", "date", "event_date", "start_date"] if c in out.columns), None)
    de = next((c for c in ["date_end", "end_date", "Date End"] if c in out.columns), None)
    out["_ds"] = pd.to_datetime(out[ds], errors="coerce") if ds else pd.NaT
    if de:
        out["_de"] = pd.to_datetime(out[de], errors="coerce")
    else:
        out["_de"] = out["_ds"] + pd.Timedelta(days=3)
    return out


def list_pga_events(field_path: Path | str) -> List[dict]:
    """
    Unique PGA events on the sheet, sorted by date_start ascending.
    Each item: {event_name, course_name, date_start, date_end, tour, n_players, frame}
    """
    path = Path(field_path)
    if not path.exists():
        return []
    field = pd.read_csv(path, low_memory=False)
    if field.empty:
        return []
    tour = field["tour"].map(_norm_tour) if "tour" in field.columns else pd.Series([""] * len(field))
    pga = field[tour.map(is_pga_tour)].copy()
    if pga.empty:
        pga = field[~tour.map(lambda t: bool(SKIP_TOUR_RE.search(str(t))))].copy()
    if pga.empty:
        return []
    pga = _attach_dates(pga)
    if "event_name" not in pga.columns:
        pga["event_name"] = pga.get("tournament", "Unknown")
    events = []
    for ev, g in pga.groupby(pga["event_name"].astype(str), sort=False):
        g = g.copy()
        events.append({
            "event_name": str(ev),
            "course_name": str(g["course_name"].dropna().iloc[0]) if "course_name" in g.columns and g["course_name"].notna().any() else "",
            "date_start": g["_ds"].min(),
            "date_end": g["_de"].max(),
            "tour": str(g["tour"].iloc[0]) if "tour" in g.columns else "",
            "n_players": len(g),
            "frame": g.drop(columns=[c for c in ["_ds", "_de"] if c in g.columns], errors="ignore"),
        })
    events.sort(key=lambda e: (
        e["date_start"].value if pd.notna(e["date_start"]) else 10**18,
        e["event_name"],
    ))
    return events


def _pick_current(events: List[dict], today: Optional[pd.Timestamp] = None) -> Optional[dict]:
    """In-progress event, else soonest upcoming, else most recent past."""
    if not events:
        return None
    today = today or today_ts()
    in_prog = [
        e for e in events
        if pd.notna(e["date_start"]) and pd.notna(e["date_end"])
        and e["date_start"] <= today <= e["date_end"]
    ]
    if in_prog:
        # if two overlap, prefer the one that started most recently
        in_prog.sort(key=lambda e: e["date_start"], reverse=True)
        return in_prog[0]
    upcoming = [
        e for e in events
        if pd.notna(e["date_end"]) and e["date_end"] >= today
    ]
    if upcoming:
        upcoming.sort(key=lambda e: e["date_start"] if pd.notna(e["date_start"]) else pd.Timestamp.max)
        return upcoming[0]
    # past only
    past = [e for e in events if pd.notna(e["date_end"])]
    past.sort(key=lambda e: e["date_end"], reverse=True)
    return past[0] if past else events[-1]


def _pick_upcoming(events: List[dict], today: Optional[pd.Timestamp] = None) -> Optional[dict]:
    """
    The NEXT event after current.
    Prefer tour=upcoming_pga when present and still live/future;
    else first event starting after current's start (or after today).
    """
    if not events:
        return None
    today = today or today_ts()
    current = _pick_current(events, today)
    # Explicit upcoming_pga rows first (sheet convention)
    tagged = [
        e for e in events
        if "upcoming" in _norm_tour(e.get("tour"))
        and (pd.isna(e["date_end"]) or e["date_end"] >= today)
    ]
    if tagged:
        tagged.sort(key=lambda e: e["date_start"] if pd.notna(e["date_start"]) else pd.Timestamp.max)
        # don't return the same event as current
        for e in tagged:
            if current is None or e["event_name"] != current["event_name"]:
                return e
    if current is None:
        return None
    after = [
        e for e in events
        if e["event_name"] != current["event_name"]
        and pd.notna(e["date_start"])
        and (
            e["date_start"] > (current["date_start"] if pd.notna(current["date_start"]) else today)
            or e["date_start"] > today
        )
    ]
    after.sort(key=lambda e: e["date_start"])
    if after:
        return after[0]
    # No distinct later event on the sheet (common mid-week when only one future field exists)
    print("[field_utils] no distinct upcoming event after current")
    return None


def load_field_for_scope(
    field_path: Path | str,
    scope: str = "current",
    today: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Return player rows for current or upcoming PGA event."""
    events = list_pga_events(field_path)
    if not events:
        return pd.DataFrame()
    today = today or today_ts()
    scope = (scope or "current").strip().lower()
    if scope in ("upcoming", "next", "upcoming_pga"):
        pick = _pick_upcoming(events, today)
        if pick is None:
            print("WARNING: no distinct upcoming PGA event on sheet — write skipped by generators")
            return pd.DataFrame()
    else:
        pick = _pick_current(events, today)
    if pick is None:
        return pd.DataFrame()
    print(
        f"[field_utils] scope={scope} → {pick['event_name']} "
        f"@ {pick['course_name']} | {pick['date_start']} → {pick['date_end']} "
        f"| tour={pick['tour']} | n={pick['n_players']}"
    )
    return pick["frame"].copy()


def load_current_pga_field(field_path: Path | str, **kw) -> pd.DataFrame:
    return load_field_for_scope(field_path, "current", **kw)


def load_upcoming_pga_field(field_path: Path | str, **kw) -> pd.DataFrame:
    return load_field_for_scope(field_path, "upcoming", **kw)


def match_preview_row(preview_df: pd.DataFrame, event_name: str, course_name: str = "") -> dict:
    """Pick pre_tournament_preview row matching event/course (not hard-coded Rocket)."""
    if preview_df is None or preview_df.empty:
        return {}
    today = today_ts()
    event_name = str(event_name or "")
    course_name = str(course_name or "")
    best = None
    best_score = -10**9
    for _, r in preview_df.iterrows():
        t = str(r.get("TOURNAMENT") or r.get("Tournament") or "")
        c = str(r.get("COURSE NAME") or r.get("Course Name") or "")
        fd = str(r.get("TOURNAMENT FINISHING DATE") or r.get("Tournament Finishing Date") or "")
        fd = fd.replace("22026", "2026")  # known typo
        fd_ts = pd.to_datetime(fd, errors="coerce")
        score = 0
        if event_name and re.search(re.escape(event_name[:12]), t, re.I):
            score += 100
        # token overlap event
        for tok in re.findall(r"[A-Za-z]{4,}", event_name):
            if re.search(re.escape(tok), t, re.I):
                score += 20
        if course_name:
            for tok in re.findall(r"[A-Za-z]{4,}", course_name):
                if re.search(re.escape(tok), c, re.I):
                    score += 25
        if pd.notna(fd_ts):
            if fd_ts.normalize() >= today:
                score += 10
            else:
                score -= 5
        if score > best_score:
            best_score = score
            best = r
    if best is None:
        best = preview_df.iloc[-1]
    return {str(k).strip(): ("" if pd.isna(v) else v) for k, v in best.items()}


if __name__ == "__main__":
    # quick self-test
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/upcoming_field.csv")
    print("scopes", parse_scopes(["--both"]))
    for sc in ("current", "upcoming"):
        df = load_field_for_scope(path, sc)
        if len(df):
            print(sc, df.iloc[0].get("event_name"), len(df))
        else:
            print(sc, "EMPTY")
