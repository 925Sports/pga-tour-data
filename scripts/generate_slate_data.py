"""
Pull weekly slate data from Google Sheets and write cleaned CSVs for the Fantasy Golf Hub.
Sources (published CSV):
  - Draftable (DK salaries / images)
  - Betting odds
  - Combined props (PP + UD)  — with fallback merge from raw PP/UD sheets
  - Fantasy points (if present)
  - Raw Underdog + PrizePicks dumps
"""
from __future__ import annotations

import csv
import re
from io import StringIO
from pathlib import Path
from urllib.request import urlopen, Request
from datetime import datetime

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

URLS = {
    "draftable": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=0&single=true&output=csv",
    "betting": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=749430247&single=true&output=csv",
    # Combined sheet is a QUERY join in Sheets — can lag or go empty between rounds.
    "combined": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=2034553651&single=true&output=csv",
    "underdog": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=1306744503&single=true&output=csv",
    "prizepicks": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=849548751&single=true&output=csv",
    "fantasy_points": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTYFnwmt5yHygXP28HHRrg47ARQgV0IKm3wnaxoIz8MnGJH_mcIj6HUY96tbl1j7vD2r8JRDgR1wVxD/pub?gid=654148847&single=true&output=csv",
}

PROPS_COMBINED_FIELDS = [
    "player_name", "stat_type", "prizepicks_line", "underdog_line",
    "average_line", "higher_price_ud", "lower_price_ud", "headshot",
    "date", "updated_at",
]


def fetch_csv(url: str) -> list[dict]:
    req = Request(url, headers={"User-Agent": "925Sports-Hub/1.0"})
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    # Strip BOM if present
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    reader = csv.DictReader(StringIO(raw))
    return list(reader)


def norm_name(name: str) -> str:
    if not name:
        return ""
    s = str(name).strip()
    # "Young, Cameron" -> "Cameron Young"
    if "," in s and not s.lower().startswith("de "):
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            s = f"{parts[1]} {parts[0]}"
    s = re.sub(r"\s+", " ", s)
    return s


def norm_key(name: str) -> str:
    s = norm_name(name).lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def norm_stat(stat: str) -> str:
    """Normalize stat labels so PP and UD keys join cleanly."""
    s = re.sub(r"\s+", " ", str(stat or "").strip())
    aliases = {
        "birdies or better": "Birdies Or Better",
        "birdie or better": "Birdies Or Better",
        "birdies+": "Birdies Or Better",
        "bogeys or worse": "Bogeys or Worse",
        "bogey or worse": "Bogeys or Worse",
        "fairways hit": "Fairways Hit",
        "fairway hit": "Fairways Hit",
        "greens in regulation": "Greens In Regulation",
        "gir": "Greens In Regulation",
        "pars": "Pars",
        "round strokes": "Round Strokes",
        "strokes": "Strokes",
        "tourney finishing position": "Tourney Finishing Position",
        "tournament finishing position": "Tourney Finishing Position",
        "r3 leaderboard position": "R3 Leaderboard Position",
        "r4 leaderboard position": "R4 Leaderboard Position",
        "r2 leaderboard position": "R2 Leaderboard Position",
        "r1 leaderboard position": "R1 Leaderboard Position",
        "birdies or better matchup": "Birdies or Better Matchup",
    }
    key = s.lower()
    return aliases.get(key, s)


def write_rows(path: Path, rows: list[dict], fieldnames: list[str]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  wrote {len(rows)} → {path}")


def process_draftable(rows: list[dict]):
    def slate_rank(r):
        s = str(r.get("Slate Type") or "").lower()
        if s == "classic" or "classic" in s:
            return 0
        if "main" in s:
            return 1
        return 2

    sorted_rows = sorted(rows, key=slate_rank)
    out = []
    seen = set()
    for r in sorted_rows:
        name = r.get("Player Name") or ""
        if not name.strip():
            continue
        key = norm_key(name)
        if key in seen:
            continue
        seen.add(key)
        try:
            sal = int(float(str(r.get("Salary") or "0").replace(",", "")))
        except Exception:
            sal = None
        out.append({
            "player_name": norm_name(name),
            "salary": sal or "",
            "tournament": r.get("Tournament") or r.get("Game") or "",
            "slate_type": r.get("Slate Type") or "",
            "player_image": r.get("Player Image") or "",
            "player_id": r.get("Player ID") or "",
            "draftable_id": r.get("Draftable ID") or "",
            "game_start": r.get("Game Start Time") or "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
    write_rows(
        DATA_DIR / "dk_salaries.csv",
        out,
        ["player_name", "salary", "tournament", "slate_type", "player_image",
         "player_id", "draftable_id", "game_start", "updated_at"],
    )


def process_betting(rows: list[dict]):
    out = []
    for r in rows:
        tour = (r.get("tour") or "").upper()
        market = (r.get("market") or "").lower().strip()
        name = r.get("player_name") or ""
        if not name:
            continue
        out.append({
            "tour": tour,
            "market": market,
            "player_name": norm_name(name),
            "player_name_raw": name,
            "average_odds": r.get("average_odds") or "",
            "implied_probability": r.get("implied_probability") or "",
            "true_implied_probability": r.get("true_implied_probability") or "",
            "odds_draftkings": r.get("odds.draftkings") or "",
            "odds_fanduel": r.get("odds.fanduel") or "",
            "odds_bet365": r.get("odds.bet365") or "",
            "odds_datagolf_baseline": r.get("odds.datagolf.baseline") or "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
    write_rows(
        DATA_DIR / "betting_odds.csv",
        out,
        ["tour", "market", "player_name", "player_name_raw", "average_odds",
         "implied_probability", "true_implied_probability",
         "odds_draftkings", "odds_fanduel", "odds_bet365",
         "odds_datagolf_baseline", "updated_at"],
    )


def process_combined(rows: list[dict]) -> int:
    """Write props_combined from the Sheets QUERY tab. Returns row count."""
    out = []
    for r in rows:
        name = (
            r.get("Player Name")
            or r.get("player_name")
            or r.get("Player")
            or ""
        )
        if not str(name).strip():
            continue
        out.append({
            "player_name": norm_name(name),
            "stat_type": norm_stat(r.get("Stat Type") or r.get("stat_type") or ""),
            "prizepicks_line": r.get("PrizePicks Line") or r.get("prizepicks_line") or "",
            "underdog_line": r.get("Underdog Line") or r.get("underdog_line") or "",
            "average_line": r.get("Average Line") or r.get("average_line") or "",
            "higher_price_ud": r.get("Higher Price (Underdog)") or r.get("higher_price_ud") or "",
            "lower_price_ud": r.get("Lower Price (Underdog)") or r.get("lower_price_ud") or "",
            "headshot": (
                r.get("Combined Headshot URL")
                or r.get("Player Image URL (Underdog)")
                or r.get("Headshot URL (PrizePicks)")
                or ""
            ),
            "date": r.get("Combined Date") or r.get("date") or "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
    write_rows(DATA_DIR / "props_combined.csv", out, PROPS_COMBINED_FIELDS)
    return len(out)


def _num_str(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.upper() in ("N/A", "NA", "NONE", "-"):
        return ""
    try:
        f = float(s.replace(",", ""))
        if f != f:  # NaN
            return ""
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return str(f)
    except Exception:
        return s


def build_props_combined_from_books() -> int:
    """
    Fallback: outer-join PrizePicks + Underdog raw dumps into props_combined.csv.
    Used when the Sheets combined QUERY tab is empty or thin.
    """
    pp_path = DATA_DIR / "prizepicks.csv"
    ud_path = DATA_DIR / "underdog.csv"
    if not pp_path.exists() and not ud_path.exists():
        print("  fallback merge: no prizepicks.csv / underdog.csv on disk")
        return 0

    merged: dict[tuple[str, str], dict] = {}

    def ensure(player: str, stat: str) -> dict:
        pk, sk = norm_key(player), norm_stat(stat).lower()
        key = (pk, sk)
        if key not in merged:
            merged[key] = {
                "player_name": norm_name(player),
                "stat_type": norm_stat(stat),
                "prizepicks_line": "",
                "underdog_line": "",
                "average_line": "",
                "higher_price_ud": "",
                "lower_price_ud": "",
                "headshot": "",
                "date": "",
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
        return merged[key]

    if pp_path.exists():
        with pp_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                player = r.get("Player Name") or r.get("player_name") or ""
                stat = r.get("Stat Type") or r.get("stat_type") or ""
                if not player.strip() or not str(stat).strip():
                    continue
                row = ensure(player, stat)
                line = _num_str(r.get("Line Score") or r.get("line_score") or r.get("Line"))
                if line:
                    row["prizepicks_line"] = line
                hs = r.get("Headshot URL") or r.get("Headshot URL (PrizePicks)") or ""
                if hs and not row["headshot"]:
                    row["headshot"] = hs
                dt = r.get("Date") or ""
                if dt and not row["date"]:
                    row["date"] = dt

    if ud_path.exists():
        with ud_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                player = r.get("Player Name") or r.get("player_name") or ""
                stat = r.get("Stat Description") or r.get("Stat Type") or r.get("stat_type") or ""
                if not player.strip() or not str(stat).strip():
                    continue
                row = ensure(player, stat)
                line = _num_str(r.get("Stat Value") or r.get("Line") or r.get("underdog_line"))
                if line:
                    row["underdog_line"] = line
                hi = r.get("Higher Price") or r.get("higher_price_ud") or ""
                lo = r.get("Lower Price") or r.get("lower_price_ud") or ""
                if hi and str(hi).upper() not in ("N/A", "NA"):
                    row["higher_price_ud"] = str(hi).strip()
                if lo and str(lo).upper() not in ("N/A", "NA"):
                    row["lower_price_ud"] = str(lo).strip()
                hs = r.get("Player Image URL") or r.get("Player Image URL (Underdog)") or ""
                if hs and not row["headshot"]:
                    row["headshot"] = hs
                dt = r.get("Match Date") or r.get("Date") or ""
                if dt and not row["date"]:
                    row["date"] = dt

    out = []
    for row in merged.values():
        pp = row["prizepicks_line"]
        ud = row["underdog_line"]
        try:
            if pp != "" and ud != "":
                row["average_line"] = str(round((float(pp) + float(ud)) / 2, 2))
            elif pp != "":
                row["average_line"] = pp
            elif ud != "":
                row["average_line"] = ud
        except Exception:
            row["average_line"] = pp or ud
        out.append(row)

    out.sort(key=lambda r: (r["player_name"].lower(), r["stat_type"].lower()))
    write_rows(DATA_DIR / "props_combined.csv", out, PROPS_COMBINED_FIELDS)
    print(f"  fallback merge built {len(out)} props from PP+UD raw dumps")
    return len(out)


def process_fantasy_points(rows: list[dict]):
    if not rows:
        print("  fantasy_points empty — skip")
        return
    fields = list(rows[0].keys())
    cleaned = []
    for r in rows:
        row = dict(r)
        for cand in ["Player Name", "player_name", "Name", "PLAYER"]:
            if cand in row and row[cand]:
                row["player_name"] = norm_name(row[cand])
                break
        row["updated_at"] = datetime.utcnow().isoformat() + "Z"
        cleaned.append(row)
    if "player_name" not in fields:
        fields = ["player_name"] + fields
    if "updated_at" not in fields:
        fields.append("updated_at")
    write_rows(DATA_DIR / "fantasy_points.csv", cleaned, fields)


def main():
    print("Fetching slate sources…")
    combined_rows_from_sheet = 0

    # Process underdog + prizepicks BEFORE combined so fallback can use fresh dumps
    order = ["draftable", "betting", "underdog", "prizepicks", "combined", "fantasy_points"]
    for key in order:
        url = URLS.get(key)
        if not url:
            continue
        print(f"\n[{key}]")
        try:
            rows = fetch_csv(url)
            print(f"  fetched {len(rows)} rows")
        except Exception as e:
            print(f"  ERROR: {e}")
            rows = []

        if key == "draftable":
            if rows:
                process_draftable(rows)
        elif key == "betting":
            if rows:
                process_betting(rows)
        elif key == "combined":
            if rows:
                combined_rows_from_sheet = process_combined(rows)
            else:
                print("  combined sheet empty or fetch failed")
                combined_rows_from_sheet = 0
        elif key == "fantasy_points":
            process_fantasy_points(rows)
        else:
            if not rows:
                print(f"  {key} empty — leave previous file if any")
                continue
            fields = list(rows[0].keys())
            write_rows(DATA_DIR / f"{key}.csv", rows, fields)

    pp_n = ud_n = 0
    try:
        if (DATA_DIR / "prizepicks.csv").exists():
            with (DATA_DIR / "prizepicks.csv").open(encoding="utf-8") as f:
                pp_n = max(0, sum(1 for _ in f) - 1)
        if (DATA_DIR / "underdog.csv").exists():
            with (DATA_DIR / "underdog.csv").open(encoding="utf-8") as f:
                ud_n = max(0, sum(1 for _ in f) - 1)
    except Exception:
        pass

    books_n = max(pp_n, ud_n)
    # Rebuild when combined is empty OR dramatically thinner than book feeds
    need_fallback = combined_rows_from_sheet == 0 or (
        books_n >= 30 and combined_rows_from_sheet < max(15, int(books_n * 0.25))
    )
    if need_fallback:
        print(
            f"\n[combined fallback] sheet rows={combined_rows_from_sheet} "
            f"pp={pp_n} ud={ud_n} → merging from book dumps"
        )
        build_props_combined_from_books()
    else:
        print(f"\n[combined] keeping sheet output ({combined_rows_from_sheet} rows)")

    print("\nDone.")


if __name__ == "__main__":
    main()
