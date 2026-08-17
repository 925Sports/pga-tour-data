#!/usr/bin/env python3
"""
Fetch live DraftKings GOLF salaries and write data/dk_salaries.csv
Bypasses the old Google Sheet DRAFTTABLE (which was returning 403).
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.draftkings.com/lobby#/GOLF",
    "Origin": "https://www.draftkings.com",
}

OUTPUT_FIELDS = [
    "player_name",
    "salary",
    "tournament",
    "slate_type",
    "player_image",
    "player_id",
    "draftable_id",
    "game_start",
    "updated_at",
]


def norm_name(name: str) -> str:
    if not name:
        return ""
    s = str(name).strip()
    # "Young, Cameron" → "Cameron Young"
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2:
            s = f"{parts[1]} {parts[0]}"
    return re.sub(r"\s+", " ", s).strip()


def format_datetime(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%m/%d/%Y %I:%M %p")
    except Exception:
        return iso_str


def main() -> None:
    print("Fetching DraftKings GOLF contests…")
    contest_url = "https://www.draftkings.com/lobby/getcontests?sport=GOLF"

    try:
        r = requests.get(contest_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"Failed to fetch contests: {e}")
        return

    contests = data.get("Contests", [])
    if not contests:
        print("No contests found")
        return

    # Group contests by draft group
    draft_groups: dict[str, dict] = {}
    for c in contests:
        name = (c.get("n") or c.get("Name") or "").lower()
        if "madden" in name or "best ball" in name:
            continue

        dg = str(c.get("dg") or c.get("DraftGroupId") or "")
        cid = str(c.get("id") or c.get("ContestId") or "")
        cname = c.get("n") or c.get("Name") or ""
        if not dg or not cid:
            continue

        slate_type = "Classic"
        if "showdown" in name:
            slate_type = "Showdown Captain Mode"
        elif "turbo" in name:
            slate_type = "Turbo"
        elif "tiers" in name or "nosebleed" in name:
            slate_type = "Tiers"
        elif "snake" in name:
            slate_type = "Snake"
        elif "single stat" in name:
            slate_type = "Single Stat"
        elif "late" in name:
            slate_type = "Late"
        elif "early" in name:
            slate_type = "Early"

        if dg not in draft_groups:
            draft_groups[dg] = {
                "slate_type": slate_type,
                "contest_ids": [],
                "contest_names": [],
                "sample_name": cname,
            }
        draft_groups[dg]["contest_ids"].append(cid)
        draft_groups[dg]["contest_names"].append(cname)
        # Prefer Classic / main PGA TOUR naming
        if "classic" in name or "pga tour" in name and "single" not in name:
            draft_groups[dg]["slate_type"] = "Classic"

    print(f"Found {len(draft_groups)} draft groups")

    # Prefer Classic → Tiers → everything else
    def group_priority(item):
        dg_id, g = item
        st = g["slate_type"].lower()
        if st == "classic":
            return (0, -len(g["contest_ids"]))
        if "tiers" in st:
            return (1, -len(g["contest_ids"]))
        return (2, -len(g["contest_ids"]))

    ordered = sorted(draft_groups.items(), key=group_priority)

    rows = []
    seen_players = set()

    for dg_id, group in ordered:
        print(f"  Fetching draftables for DG {dg_id} ({group['slate_type']})…")
        url = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{dg_id}/draftables?format=json"

        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                print(f"    Failed {dg_id}: {r.status_code}")
                continue
            salary_data = r.json()
        except Exception as e:
            print(f"    Error {dg_id}: {e}")
            continue

        draftables = salary_data.get("draftables", [])
        if not draftables:
            continue

        # Build competition lookup
        comps = {}
        for p in draftables:
            comp = p.get("competition") or {}
            cid = str(comp.get("competitionId") or "")
            if cid and cid not in comps:
                comps[cid] = {
                    "name": comp.get("name") or "",
                    "startTime": comp.get("startTime") or "",
                }

        for p in draftables:
            player_id = str(p.get("playerId") or "")
            draftable_id = str(p.get("draftableId") or "")
            name = p.get("displayName") or "Unknown"
            salary = p.get("salary") or 0
            image = p.get("playerImage50") or p.get("imageUrl") or ""
            pos = p.get("position") or ""

            if salary <= 0 or not player_id or not draftable_id or name == "Unknown":
                continue

            # Only keep G (golfer) or CPT rows for main output
            if pos and pos.upper() not in ("G", "CPT", ""):
                continue

            key = norm_name(name).lower()
            # Prefer Classic slate; skip duplicates from lower-priority groups
            if key in seen_players and group["slate_type"] != "Classic":
                continue
            seen_players.add(key)

            comp = p.get("competition") or {}
            comp_id = str(comp.get("competitionId") or "")
            tournament = comps.get(comp_id, {}).get("name", "") or (comp.get("name") or "")
            start = comps.get(comp_id, {}).get("startTime", "") or (comp.get("startTime") or "")

            rows.append({
                "player_name": norm_name(name),
                "salary": int(salary),
                "tournament": tournament,
                "slate_type": group["slate_type"],
                "player_image": image,
                "player_id": player_id,
                "draftable_id": draftable_id,
                "game_start": format_datetime(start),
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

        # Once we have a good Classic slate we can stop if we want only one main field
        if group["slate_type"] == "Classic" and len(rows) > 40:
            print(f"    Got solid Classic field ({len(rows)} players) — stopping early")
            break

    if not rows:
        print("No player rows generated")
        return

    # Sort by salary descending
    rows.sort(key=lambda x: x["salary"], reverse=True)

    out_path = DATA_DIR / "dk_salaries.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows → {out_path}")
    if rows:
        print(f"Top salary: {rows[0]['player_name']} ${rows[0]['salary']:,}")
        print(f"Tournament: {rows[0]['tournament']}")


if __name__ == "__main__":
    main()
