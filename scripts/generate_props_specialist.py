#!/usr/bin/env python3
"""Precompute Specialist Prop projections for the current / upcoming field.

Same course-characteristic matching as Specialist (DESIGNER, STYLE, GREENS, …)
but averages PROP metrics instead of SG Total / SG Putting:

  birdies_plus, strokes, bogeys, pars, fw_pct, gir_pct

Reads (priority):
  data/cheat_sheet.csv | recent_form.csv | upcoming_field.csv  (field + course)
  data/field_player_logs.csv                                 (round history + traits)
  data/pre_tournament_preview.csv                            (course fallback)

Writes:
  data/props_specialist.csv
  data/props_specialist_upcoming.csv   (when --scope both|upcoming)

Also writes a companion traits snapshot:
  data/props_specialist_traits.csv
  data/props_specialist_traits_upcoming.csv

Columns (player board):
  player_name, course_name, event_name,
  birdies_plus, strokes, bogeys, pars, fw_pct, gir_pct,
  n_rounds, n_starts, window, trait_sig,
  by_designer, by_style, by_greens,   # optional single-trait avgs for birdies+
  birdies_plus_rank, strokes_rank

Usage:
  python scripts/generate_props_specialist.py --scope both
  python scripts/generate_props_specialist.py --scope current --window 24
  EVENT_SCOPE=both python scripts/generate_props_specialist.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from field_utils import load_field_for_scope, parse_scopes, scoped_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# ── Trait columns (must exist on field_player_logs after snake_cols) ─────────
TRAIT_CANDIDATES = [
    "designer",
    "style",
    "greens",
    "greens_2",
    "fairways",
    "length",
    "par",
    "scoring",
    "weather",
    "type",
    "fairway_width",
    "green_width",
    "green_speeds",
    "green_speeds_1",
    "rough_length",
    "water_hazards",
    "rough_amount",
    "bunkers",
    "location",
    "redesigned",
    "drainage",
    "rain",
]

# Human labels for trait_sig (match hub display names when possible)
TRAIT_LABELS = {
    "designer": "DESIGNER",
    "style": "STYLE",
    "greens": "GREENS",
    "greens_2": "GREENS (2)",
    "fairways": "FAIRWAYS",
    "length": "LENGTH",
    "par": "PAR",
    "scoring": "SCORING",
    "weather": "WEATHER",
    "type": "TYPE",
    "fairway_width": "FAIRWAY WIDTH",
    "green_width": "GREEN WIDTH",
    "green_speeds": "GREEN SPEEDS",
    "green_speeds_1": "GREEN SPEEDS.1",
    "rough_length": "ROUGH LENGTH",
    "water_hazards": "WATER HAZARDS",
    "rough_amount": "ROUGH AMOUNT",
    "bunkers": "BUNKERS",
    "location": "LOCATION",
    "redesigned": "Redesigned",
    "drainage": "DRAINAGE",
    "rain": "RAIN",
}

GREEN_TRAIT_KEYS = {
    "greens",
    "greens_2",
    "green_width",
    "green_speeds",
    "green_speeds_1",
}

DEFAULT_WINDOW = 24  # most recent trait-matched rounds per player


def snake_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")
        for c in df.columns
    ]
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()].copy()
    return df


def name_key(n: str) -> str:
    s = str(n or "").strip().lower()
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) == 2:
            s = parts[1] + " " + parts[0]
    return re.sub(r"[^a-z0-9]", "", s)


def _series_col(df: pd.DataFrame, col: str) -> pd.Series | None:
    if col not in df.columns:
        return None
    obj = df[col]
    if isinstance(obj, pd.DataFrame):
        if obj.shape[1] == 0:
            return None
        obj = obj.iloc[:, 0]
    return obj


def resolve_course(field: pd.DataFrame) -> str:
    if "course_name" in field.columns and field["course_name"].notna().any():
        c = str(field["course_name"].dropna().astype(str).iloc[0]).strip()
        if c and c.lower() not in {"nan", "none", ""}:
            return c

    for base in ("cheat_sheet.csv", "pre_tournament_preview.csv"):
        path = DATA / base
        if not path.exists():
            continue
        try:
            ch = snake_cols(pd.read_csv(path, low_memory=False, nrows=30))
            for col in ch.columns:
                if "course" in col:
                    vals = ch[col].dropna().astype(str)
                    if len(vals):
                        c = str(vals.iloc[0]).strip()
                        if c and c.lower() not in {"nan", "none", ""}:
                            return c
        except Exception as e:
            print(f"course resolve from {base} failed:", e)

    event = ""
    if "event_name" in field.columns and field["event_name"].notna().any():
        event = str(field["event_name"].dropna().astype(str).iloc[0])
    hints = [
        (r"rocket", "DETROIT GOLF CLUB"),
        (r"wyndham", "SEDGEFIELD COUNTRY CLUB"),
        (r"sedgefield", "SEDGEFIELD COUNTRY CLUB"),
        (r"memorial", "Muirfield Village"),
        (r"travelers", "TPC River Highlands"),
        (r"john deere", "TPC Deere Run"),
        (r"players", "TPC Sawgrass"),
        (r"genesis", "Riviera Country Club"),
        (r"arnold palmer", "Bay Hill"),
        (r"fedex st", "East Lake"),
        (r"bmw", "Cog Hill"),
        (r"tour championship", "East Lake"),
    ]
    for pat, course in hints:
        if re.search(pat, event, re.I):
            return course
    return ""


def resolve_event(field: pd.DataFrame) -> str:
    for col in ("event_name", "tournament", "event"):
        if col in field.columns and field[col].notna().any():
            return str(field[col].dropna().astype(str).iloc[0]).strip()
    return ""


def course_mask(series: pd.Series, course: str) -> pd.Series:
    s = series.astype(str).str.lower()
    cl = (course or "").lower().strip()
    if not cl:
        return pd.Series([False] * len(series), index=series.index)
    tokens = [t for t in re.split(r"[^a-z0-9]+", cl) if len(t) >= 4]
    mask = s.str.contains(re.escape(cl[:24]), na=False, regex=True)
    for t in tokens[:5]:
        mask = mask | s.str.contains(re.escape(t), na=False, regex=True)
    if "detroit" in cl or "rocket" in cl:
        mask = mask | s.str.contains("detroit", na=False)
    if "sedgefield" in cl or "wyndham" in cl:
        mask = mask | s.str.contains("sedgefield", na=False)
    return mask


def load_field(scope: str = "current") -> pd.DataFrame:
    for base in ("cheat_sheet.csv", "recent_form.csv"):
        path = scoped_path(DATA, base, scope)
        if path.exists():
            df = snake_cols(pd.read_csv(path, low_memory=False))
            print(f"Field from {path.name}: {len(df)} rows (scope={scope})")
            return df
    uf = DATA / "upcoming_field.csv"
    if uf.exists():
        raw = load_field_for_scope(uf, scope)
        if raw is not None and len(raw):
            df = snake_cols(raw)
            print(f"Field from upcoming_field scope={scope}: {len(df)} rows")
            return df
    if (scope or "current").strip().lower() in ("current", "this", "this_week", ""):
        for name in ("cheat_sheet.csv", "recent_form.csv", "upcoming_field.csv"):
            path = DATA / name
            if path.exists():
                df = snake_cols(pd.read_csv(path, low_memory=False))
                print(f"Field from {name}: {len(df)} rows (legacy current)")
                return df
    print(f"No field for scope={scope}")
    return pd.DataFrame()


def field_player_names(field: pd.DataFrame) -> list[str]:
    players: list[str] = []
    seen: set[str] = set()
    for col in ("name_adjusted", "player_name"):
        series = _series_col(field, col)
        if series is None:
            continue
        for v in series.dropna().astype(str).str.strip().tolist():
            k = name_key(v)
            if not k or k in seen:
                continue
            seen.add(k)
            players.append(v)
    return players


def detect_course_traits(logs: pd.DataFrame, course: str) -> dict[str, dict]:
    """Mode of each trait column among rounds at this course."""
    traits: dict[str, dict] = {}
    if logs is None or logs.empty or not course or "course_name" not in logs.columns:
        return traits

    mask = course_mask(logs["course_name"], course)
    if isinstance(mask, pd.DataFrame):
        mask = mask.iloc[:, 0]
    sub = logs.loc[mask.values if hasattr(mask, "values") else mask]
    if sub.empty:
        # fuzzy fallback: any token
        cl = course.lower()
        tokens = [t for t in re.split(r"[^a-z0-9]+", cl) if len(t) >= 4]
        if tokens:
            s = logs["course_name"].astype(str).str.lower()
            m = pd.Series([True] * len(logs), index=logs.index)
            for t in tokens[:3]:
                m = m & s.str.contains(re.escape(t), na=False)
            sub = logs.loc[m]
    if sub.empty:
        print(f"WARNING: no log rows for course={course!r} — traits empty")
        return traits

    print(f"Trait detection sample: {len(sub)} rounds at {course!r}")
    for col in TRAIT_CANDIDATES:
        if col not in sub.columns:
            continue
        series = _series_col(sub, col)
        if series is None:
            continue
        vals = (
            series.dropna()
            .astype(str)
            .str.strip()
            .replace({"": None, "nan": None, "None": None, "NaN": None})
            .dropna()
        )
        vals = vals[~vals.str.lower().isin({"nan", "none", "na", "null", ""})]
        if vals.empty:
            continue
        mode = vals.value_counts().index[0]
        count = int(vals.value_counts().iloc[0])
        traits[col] = {
            "key": col,
            "label": TRAIT_LABELS.get(col, col.upper()),
            "value": str(mode).strip(),
            "count": count,
            "green": col in GREEN_TRAIT_KEYS or "green" in col,
        }
    return traits


def trait_sig(traits: dict[str, dict]) -> str:
    parts = []
    for col, t in traits.items():
        parts.append(f"{t['label']}:{t['value']}")
    return " | ".join(parts)


def ensure_metric_cols(logs: pd.DataFrame) -> pd.DataFrame:
    """Add derived birdies_plus, normalize fw/gir to 0-100."""
    df = logs
    # birdies + eagles
    b = pd.to_numeric(df["birdies"], errors="coerce") if "birdies" in df.columns else None
    e = (
        pd.to_numeric(df["eagles_or_better"], errors="coerce")
        if "eagles_or_better" in df.columns
        else None
    )
    if b is not None or e is not None:
        bb = (b.fillna(0) if b is not None else 0) + (e.fillna(0) if e is not None else 0)
        # if both NaN originally, leave NaN
        if b is not None and e is not None:
            both_nan = b.isna() & e.isna()
            bb = bb.where(~both_nan, other=pd.NA)
        elif b is not None:
            bb = b
        else:
            bb = e
        df = df.copy()
        df["birdies_plus"] = pd.to_numeric(bb, errors="coerce")
    else:
        df = df.copy()
        df["birdies_plus"] = pd.NA

    # bogeys alias
    if "bogeys" in df.columns and "bogies" not in df.columns:
        df["bogies"] = pd.to_numeric(df["bogeys"], errors="coerce")
    elif "bogies" in df.columns:
        df["bogies"] = pd.to_numeric(df["bogies"], errors="coerce")

    for col in ("round_score", "pars", "driving_acc", "gir", "sg_total", "sg_putt"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # FW / GIR 0-1 → 0-100
    for col in ("driving_acc", "gir"):
        if col in df.columns:
            s = df[col]
            # only scale values that look like fractions
            mask = s.notna() & (s <= 1.5) & (s >= 0)
            df.loc[mask, col] = s.loc[mask] * 100.0

    return df


def rounds_match_any_trait(df: pd.DataFrame, traits: dict[str, dict]) -> pd.Series:
    """True if round matches ANY course trait value."""
    if not traits:
        return pd.Series([False] * len(df), index=df.index)
    mask = pd.Series([False] * len(df), index=df.index)
    for col, t in traits.items():
        if col not in df.columns:
            continue
        series = _series_col(df, col)
        if series is None:
            continue
        m = series.astype(str).str.strip() == str(t["value"]).strip()
        mask = mask | m.fillna(False)
    return mask


def player_sub(logs: pd.DataFrame, pname: str) -> pd.DataFrame:
    target = name_key(pname)
    if not target or logs is None or logs.empty:
        return logs.iloc[0:0] if logs is not None else pd.DataFrame()

    pn = _series_col(logs, "player_name")
    if pn is not None:
        keys = pn.astype(str).map(name_key)
        mask = keys.to_numpy().ravel() == target
        if mask.any():
            return logs.loc[mask]

    na = _series_col(logs, "name_adjusted")
    if na is not None:
        keys2 = na.astype(str).map(name_key)
        mask2 = keys2.to_numpy().ravel() == target
        if mask2.any():
            return logs.loc[mask2]

    if pn is not None:
        mask3 = pn.astype(str).str.strip().str.lower().to_numpy().ravel() == str(pname).strip().lower()
        if mask3.any():
            return logs.loc[mask3]
    return logs.iloc[0:0]


def sort_recent(sub: pd.DataFrame) -> pd.DataFrame:
    work = sub.copy()
    date_col = next(
        (c for c in ("event_completed", "date", "Date") if c in work.columns), None
    )
    if date_col:
        work["_dt"] = pd.to_datetime(work[date_col], errors="coerce")
    else:
        work["_dt"] = pd.NaT
    rnd = pd.to_numeric(work["round_num"], errors="coerce") if "round_num" in work.columns else 0
    work["_rnd"] = rnd
    return work.sort_values(["_dt", "_rnd"], ascending=[False, False])


def mean_clean(series: pd.Series, trim: bool = False) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    if trim and len(s) >= 12:
        lo = s.quantile(0.05)
        hi = s.quantile(0.95)
        s = s[(s >= lo) & (s <= hi)]
    if s.empty:
        return None
    return round(float(s.mean()), 4)


def summarize_player_props(
    sub_matched: pd.DataFrame,
    pname: str,
    course: str,
    event: str,
    traits: dict[str, dict],
    window: int | None,
) -> dict:
    empty = {
        "player_name": pname,
        "course_name": course,
        "event_name": event,
        "birdies_plus": None,
        "strokes": None,
        "bogeys": None,
        "pars": None,
        "fw_pct": None,
        "gir_pct": None,
        "n_rounds": 0,
        "n_starts": 0,
        "window": window if window is not None else "all",
        "trait_sig": trait_sig(traits),
        "by_designer": None,
        "by_style": None,
        "by_greens": None,
        "birdies_plus_rank": None,
        "strokes_rank": None,
    }
    if sub_matched is None or sub_matched.empty:
        return empty

    work = sort_recent(sub_matched)
    if window and window > 0:
        work = work.head(int(window))

    n_rounds = int(len(work))
    if "event_name" in work.columns:
        n_starts = int(work["event_name"].nunique())
    else:
        n_starts = n_rounds

    out = dict(empty)
    out["n_rounds"] = n_rounds
    out["n_starts"] = n_starts
    out["birdies_plus"] = mean_clean(work["birdies_plus"]) if "birdies_plus" in work.columns else None
    out["strokes"] = mean_clean(work["round_score"]) if "round_score" in work.columns else None
    out["bogeys"] = mean_clean(work["bogies"]) if "bogies" in work.columns else None
    out["pars"] = mean_clean(work["pars"]) if "pars" in work.columns else None
    out["fw_pct"] = mean_clean(work["driving_acc"]) if "driving_acc" in work.columns else None
    out["gir_pct"] = mean_clean(work["gir"]) if "gir" in work.columns else None

    # Single-trait birdies+ for debugging / UI chips
    for col, out_key in (
        ("designer", "by_designer"),
        ("style", "by_style"),
        ("greens", "by_greens"),
    ):
        if col not in traits or col not in sub_matched.columns:
            continue
        tval = traits[col]["value"]
        series = _series_col(sub_matched, col)
        if series is None:
            continue
        m = series.astype(str).str.strip() == str(tval).strip()
        part = sort_recent(sub_matched.loc[m])
        if window and window > 0:
            part = part.head(int(window))
        if "birdies_plus" in part.columns and len(part):
            out[out_key] = mean_clean(part["birdies_plus"])

    return out


def field_trait_baselines(matched_logs: pd.DataFrame, window_note: str) -> dict:
    """Field-wide averages on trait-matched rounds (for panel / QA)."""
    if matched_logs is None or matched_logs.empty:
        return {}
    # sample cap for speed
    sample = matched_logs
    if len(sample) > 8000:
        sample = sample.sample(8000, random_state=42)
    return {
        "birdies_plus": mean_clean(sample["birdies_plus"], trim=True) if "birdies_plus" in sample.columns else None,
        "strokes": mean_clean(sample["round_score"], trim=True) if "round_score" in sample.columns else None,
        "bogeys": mean_clean(sample["bogies"], trim=True) if "bogies" in sample.columns else None,
        "pars": mean_clean(sample["pars"], trim=True) if "pars" in sample.columns else None,
        "fw_pct": mean_clean(sample["driving_acc"], trim=True) if "driving_acc" in sample.columns else None,
        "gir_pct": mean_clean(sample["gir"], trim=True) if "gir" in sample.columns else None,
        "n_rounds_sample": int(len(sample)),
        "window": window_note,
    }


def build_props_specialist_for_scope(scope: str, window: int | None = DEFAULT_WINDOW) -> int:
    field = load_field(scope)
    if field.empty:
        print(f"No field for scope={scope}")
        return 0

    course = resolve_course(field)
    event = resolve_event(field)
    players = field_player_names(field)
    print(f"scope={scope} course={course!r} event={event!r} players={len(players)}")

    out_cols = [
        "player_name", "course_name", "event_name",
        "birdies_plus", "strokes", "bogeys", "pars", "fw_pct", "gir_pct",
        "n_rounds", "n_starts", "window", "trait_sig",
        "by_designer", "by_style", "by_greens",
        "birdies_plus_rank", "strokes_rank",
    ]

    logs_path = scoped_path(DATA, "field_player_logs.csv", scope)
    if not logs_path.exists():
        logs_path = DATA / "field_player_logs.csv"
    if not logs_path.exists():
        print("No field_player_logs.csv — writing empty props_specialist")
        out_path = scoped_path(DATA, "props_specialist.csv", scope)
        pd.DataFrame(columns=out_cols).to_csv(out_path, index=False)
        traits_path = scoped_path(DATA, "props_specialist_traits.csv", scope)
        pd.DataFrame(columns=["key", "label", "value", "count", "green", "course_name", "event_name"]).to_csv(
            traits_path, index=False
        )
        return 0

    print(f"Loading logs from {logs_path.name}…")
    logs = snake_cols(pd.read_csv(logs_path, low_memory=False))
    logs = ensure_metric_cols(logs)

    traits = detect_course_traits(logs, course)
    if not traits:
        print("WARNING: no traits detected — matching will be empty")
    else:
        print("Traits:")
        for t in traits.values():
            print(f"  {t['label']}: {t['value']} (n={t['count']}, green={t['green']})")

    # Trait-matched universe (any trait)
    tmask = rounds_match_any_trait(logs, traits)
    matched_all = logs.loc[tmask].copy()
    print(f"Trait-matched rounds (all players): {len(matched_all)} / {len(logs)}")

    # Write traits file
    traits_rows = []
    for t in traits.values():
        traits_rows.append({
            "key": t["key"],
            "label": t["label"],
            "value": t["value"],
            "count": t["count"],
            "green": t["green"],
            "course_name": course,
            "event_name": event,
        })
    # Field baseline row as JSON-friendly extras
    baselines = field_trait_baselines(matched_all, str(window if window is not None else "all"))
    traits_path = scoped_path(DATA, "props_specialist_traits.csv", scope)
    traits_df = pd.DataFrame(traits_rows)
    traits_df.to_csv(traits_path, index=False)
    # sidecar baselines json next to traits
    base_path = scoped_path(DATA, "props_specialist_baselines.json", scope)
    # scoped_path only works cleanly for .csv style — handle json manually
    suffix = "_upcoming" if (scope or "").lower() in ("upcoming", "next", "upcoming_pga") else ""
    base_path = DATA / f"props_specialist_baselines{suffix}.json"
    base_path.write_text(json.dumps({
        "course_name": course,
        "event_name": event,
        "trait_sig": trait_sig(traits),
        "traits": traits_rows,
        "field_avgs": baselines,
        "window": window if window is not None else "all",
    }, indent=2))
    print(f"Wrote {traits_path.name} + {base_path.name}")

    rows = []
    for i, pname in enumerate(players):
        # Prefer player's trait-matched rounds from full logs (not course-only)
        p_all = player_sub(logs, pname)
        if p_all.empty:
            rows.append(summarize_player_props(p_all, pname, course, event, traits, window))
        else:
            pmask = rounds_match_any_trait(p_all, traits)
            p_matched = p_all.loc[pmask]
            rows.append(summarize_player_props(p_matched, pname, course, event, traits, window))
        if (i + 1) % 40 == 0:
            print(f"  processed {i + 1}/{len(players)}…")

    out = pd.DataFrame(rows)

    # Ranks (higher birdies+ better; lower strokes better)
    def rank_desc(col: str, out_col: str):
        ranked = out[out[col].notna()].sort_values(col, ascending=False).copy()
        ranked[out_col] = range(1, len(ranked) + 1)
        out[out_col] = out["player_name"].map(dict(zip(ranked["player_name"], ranked[out_col])))

    def rank_asc(col: str, out_col: str):
        ranked = out[out[col].notna()].sort_values(col, ascending=True).copy()
        ranked[out_col] = range(1, len(ranked) + 1)
        out[out_col] = out["player_name"].map(dict(zip(ranked["player_name"], ranked[out_col])))

    rank_desc("birdies_plus", "birdies_plus_rank")
    rank_asc("strokes", "strokes_rank")

    out = out[out_cols]
    out_path = scoped_path(DATA, "props_specialist.csv", scope)
    out.to_csv(out_path, index=False)

    with_bp = int(out["birdies_plus"].notna().sum())
    with_st = int(out["strokes"].notna().sum())
    print(
        f"Wrote {out_path} ({len(out)} players, "
        f"{with_bp} with birdies+, {with_st} with strokes, course={course!r})"
    )
    if with_bp:
        top = out.nsmallest(8, "birdies_plus_rank")[
            ["birdies_plus_rank", "player_name", "birdies_plus", "strokes", "bogeys", "n_rounds"]
        ]
        print("Top Birdies+ (trait-matched):\n", top.to_string(index=False))
    return 0


def parse_window(argv: list[str] | None) -> int | None:
    argv = list(argv or sys.argv[1:])
    for i, a in enumerate(argv):
        if a in ("--window", "-w") and i + 1 < len(argv):
            v = argv[i + 1].strip().lower()
            if v in ("all", "none", "0"):
                return None
            return int(v)
        if a.startswith("--window="):
            v = a.split("=", 1)[1].strip().lower()
            if v in ("all", "none", "0"):
                return None
            return int(v)
    return DEFAULT_WINDOW


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    scopes = parse_scopes(argv)
    window = parse_window(argv)
    print(f"Props Specialist · scopes={scopes} · window={window}")
    rc = 0
    for scope in scopes:
        print(f"\n=== Props Specialist scope={scope} ===")
        try:
            build_props_specialist_for_scope(scope, window=window)
        except Exception as e:
            print("ERROR", scope, e)
            import traceback
            traceback.print_exc()
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
