#!/usr/bin/env python3
"""
Fetch live DraftKings GOLF data and write:
  - data/drafttable.csv   (full rich columns for Google Sheets)
  - data/dk_salaries.csv  (simplified for the Fantasy Golf Hub)
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

# Full rich columns (matches original DRAFTTABLE)
DRAFTTABLE_FIELDS = [
    "Player Name - Slate Type",
    "Contest IDs",
    "Player ID",
    "Draftable ID",
    "Player Name",
    "First Name",
    "Last Name",
    "Salary",
    "Position",
    "Team",
    "Game",
    "Game Start Time",
    "Player Image",
    "Tournament",
    "Slate Type",
    "Game Type",
    "Date",
    "Role",
    "Contest Names",
    "Contest IDs (Full)",
    "Slate Header",
    "Make the Cut",
]

# Simplified columns for the hub
DK_SALARIES_FIELDS = [
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
    if "," in s and not s.lower().startswith("de "):
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
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


def format_date_only(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%-m/%-d")
    except Exception:
        return ""


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

    # Group by draft group
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

        if "classic" in name or ("pga tour" in name and "single" not in name and "tiers" not in name):
            slate_type = "Classic"

        if dg not in draft_groups:
            draft_groups[dg] = {
                "slate_type": slate_type,
                "contest_ids": [],
                "contest_names": [],
            }
        draft_groups[dg]["contest_ids"].append(cid)
        draft_groups[dg]["contest_names"].append(cname)
        draft_groups[dg]["slate_type"] = slate_type

    print(f"Found {len(draft_groups)} draft groups")

    def group_priority(item):
        _, g = item
        st = g["slate_type"].lower()
        if st == "classic":
            return (0, -len(g["contest_ids"]))
        if "tiers" in st:
            return (1, -len(g["contest_ids"]))
        return (2, -len(g["contest_ids"]))

    ordered = sorted(draft_groups.items(), key=group_priority)

    rich_rows = []          # for drafttable.csv
    simple_rows = []        # for dk_salaries.csv
    seen_simple = set()

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

        # Competition lookup
        comps = {}
        start_times = []
        for p in draftables:
            comp = p.get("competition") or {}
            cid = str(comp.get("competitionId") or "")
            if cid and cid not in comps:
                comps[cid] = {
                    "name": comp.get("name") or "",
                    "startTime": comp.get("startTime") or "",
                }
                if comp.get("startTime"):
                    try:
                        start_times.append(datetime.fromisoformat(comp["startTime"].replace("Z", "+00:00")))
                    except Exception:
                        pass

        # Slate header
        slate_header = group["slate_type"]
        if start_times:
            min_start = min(start_times)
            date_part = min_start.astimezone(ZoneInfo("America/New_York")).strftime("%-m/%-d")
            time_part = min_start.astimezone(ZoneInfo("America/New_York")).strftime("%-I:%M%p")
            matchup = list(comps.values())[0]["name"] if comps else ""
            slate_header = f"{date_part} {time_part} ({matchup})"

        # Collect player versions
        player_versions = defaultdict(list)
        for p in draftables:
            player_id = str(p.get("playerId") or "")
            draftable_id = str(p.get("draftableId") or "")
            name = p.get("displayName") or "Unknown"
            first = p.get("firstName") or ""
            last = p.get("lastName") or ""
            salary = p.get("salary") or 0
            pos = p.get("position") or ""
            team = p.get("teamAbbreviation") or p.get("team") or ""
            image = p.get("playerImage50") or p.get("imageUrl") or ""

            comp = p.get("competition") or {}
            comp_id = str(comp.get("competitionId") or "")
            game = comps.get(comp_id, {}).get("name", "")
            start = comps.get(comp_id, {}).get("startTime", "")

            if salary <= 0 or not player_id or not draftable_id or name == "Unknown":
                continue

            player_versions[player_id].append({
                "draftable_id": draftable_id,
                "name": name,
                "first": first,
                "last": last,
                "salary": salary,
                "pos": pos,
                "team": team,
                "image": image,
                "game": game,
                "start": start,
                "date": format_date_only(start),
            })

        is_showdown = "Showdown" in group["slate_type"]

        for player_id, versions in player_versions.items():
            versions.sort(key=lambda x: x["salary"], reverse=True)

            if is_showdown and len(versions) >= 2:
                # Captain + Flex
                for role, v in [("Captain", versions[0]), ("Flex", versions[1])]:
                    pos = "CPT" if role == "Captain" else v["pos"]
                    rich_rows.append({
                        "Player Name - Slate Type": f"{v['name']} - {group['slate_type']} ({role})",
                        "Contest IDs": ";".join(group["contest_ids"][:20]),
                        "Player ID": player_id,
                        "Draftable ID": v["draftable_id"],
                        "Player Name": v["name"],
                        "First Name": v["first"],
                        "Last Name": v["last"],
                        "Salary": v["salary"],
                        "Position": pos,
                        "Team": v["team"],
                        "Game": v["game"],
                        "Game Start Time": format_datetime(v["start"]),
                        "Player Image": v["image"],
                        "Tournament": v["game"],
                        "Slate Type": group["slate_type"],
                        "Game Type": "PGA",
                        "Date": v["date"],
                        "Role": role,
                        "Contest Names": ";".join(group["contest_names"][:10]),
                        "Contest IDs (Full)": ";".join(group["contest_ids"][:20]),
                        "Slate Header": slate_header,
                        "Make the Cut": "",
                    })
            else:
                # Classic / standard – take highest salary version
                v = versions[0]
                rich_rows.append({
                    "Player Name - Slate Type": f"{v['name']} - {group['slate_type']}",
                    "Contest IDs": ";".join(group["contest_ids"][:20]),
                    "Player ID": player_id,
                    "Draftable ID": v["draftable_id"],
                    "Player Name": v["name"],
                    "First Name": v["first"],
                    "Last Name": v["last"],
                    "Salary": v["salary"],
                    "Position": v["pos"],
                    "Team": v["team"],
                    "Game": v["game"],
                    "Game Start Time": format_datetime(v["start"]),
                    "Player Image": v["image"],
                    "Tournament": v["game"],
                    "Slate Type": group["slate_type"],
                    "Game Type": "PGA",
                    "Date": v["date"],
                    "Role": "Standard",
                    "Contest Names": ";".join(group["contest_names"][:10]),
                    "Contest IDs (Full)": ";".join(group["contest_ids"][:20]),
                    "Slate Header": slate_header,
                    "Make the Cut": "",
                })

                # Simplified version (prefer Classic)
                key = norm_name(v["name"]).lower()
                if key not in seen_simple or group["slate_type"] == "Classic":
                    seen_simple.add(key)
                    simple_rows.append({
                        "player_name": norm_name(v["name"]),
                        "salary": int(v["salary"]),
                        "tournament": v["game"],
                        "slate_type": group["slate_type"],
                        "player_image": v["image"],
                        "player_id": player_id,
                        "draftable_id": v["draftable_id"],
                        "game_start": format_datetime(v["start"]),
                        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

        # Stop after a solid Classic field
        if group["slate_type"] == "Classic" and len(simple_rows) > 40:
            print(f"    Got solid Classic field — stopping early")
            break

    if not rich_rows:
        print("No player rows generated")
        return

    # Sort both by salary descending
    rich_rows.sort(key=lambda x: int(x["Salary"]) if str(x["Salary"]).isdigit() else 0, reverse=True)
    simple_rows.sort(key=lambda x: x["salary"], reverse=True)

    # Write rich drafttable.csv
    drafttable_path = DATA_DIR / "drafttable.csv"
    with drafttable_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DRAFTTABLE_FIELDS)
        writer.writeheader()
        writer.writerows(rich_rows)
    print(f"Wrote {len(rich_rows)} rows → {drafttable_path}")

    # Write simplified dk_salaries.csv
    salaries_path = DATA_DIR / "dk_salaries.csv"
    with salaries_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DK_SALARIES_FIELDS)
        writer.writeheader()
        writer.writerows(simple_rows)
    print(f"Wrote {len(simple_rows)} rows → {salaries_path}")

    if simple_rows:
        print(f"Top salary: {simple_rows[0]['player_name']} ${simple_rows[0]['salary']:,}")
        print(f"Tournament: {simple_rows[0]['tournament']}")


if __name__ == "__main__":
    main()
