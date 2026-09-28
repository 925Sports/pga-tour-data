#!/usr/bin/env python3
"""
Fetch live DraftKings GOLF data and write:
  - data/drafttable.csv   (full rich columns — Classic / Showdown / Late Showdown)
  - data/dk_salaries.csv  (simplified hub file — MAIN CLASSIC SLATE ONLY)

DraftKings now returns 403 on
  api.draftkings.com/draftgroups/v1/draftgroups/{id}/draftables
Salaries come from
  https://www.draftkings.com/lineup/getavailableplayers?draftGroupId={id}
Contest ids still come from
  https://www.draftkings.com/lobby/getcontests?sport=GOLF
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
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%m/%d/%Y %I:%M %p")
    except Exception:
        return str(iso_str)


def format_date_only(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%-m/%-d")
    except Exception:
        return ""


def clean_image(url: str) -> str:
    img = str(url or "").strip()
    if not img.startswith("http"):
        return ""
    if "/countries/" in img:
        return ""
    return img


def slate_label(group: dict) -> str:
    suffix = str(group.get("suffix") or "").strip()
    label = suffix.strip("() ").strip()
    return label or "PGA TOUR"


def classify_contest(name: str) -> str:
    n = (name or "").lower()
    if "madden" in n or "best ball" in n:
        return "SKIP"
    if "showdown" in n and "late" in n:
        return "Late Showdown"
    if "showdown" in n:
        return "Showdown"
    if "turbo" in n:
        return "Turbo"
    if "tiers" in n or "nosebleed" in n:
        return "Tiers"
    if "snake" in n:
        return "Snake"
    if "single stat" in n:
        return "Single Stat"
    if "lpga" in n:
        return "LPGA"
    if "dp world" in n or "european" in n:
        return "DP World"
    if "late" in n:
        return "Late"
    if "early" in n:
        return "Early"
    return "Classic"


def classify_group(contest_names: list[str], suffix: str = "") -> str:
    suf = (suffix or "").lower()
    if "showdown" in suf and "late" in suf:
        return "Late Showdown"
    if "showdown" in suf:
        return "Showdown"
    if "snake" in suf:
        return "Snake"
    if "tier" in suf:
        return "Tiers"
    if "birdie" in suf or "single stat" in suf:
        return "Single Stat"
    if "lpga" in suf:
        return "LPGA"
    if "dp world" in suf or "european" in suf:
        return "DP World"
    if "pga" in suf:
        return "Classic"
    counts: dict[str, int] = defaultdict(int)
    for name in contest_names:
        st = classify_contest(name)
        if st != "SKIP":
            counts[st] += 1
    if not counts:
        return "Classic"
    if counts.get("Late Showdown", 0) > 0 and counts["Late Showdown"] >= counts.get("Classic", 0):
        return "Late Showdown"
    if counts.get("Showdown", 0) > 0 and counts["Showdown"] >= counts.get("Classic", 0):
        return "Showdown"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def fetch_pool(dg_id: str):
    old = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{dg_id}/draftables?format=json"
    new = f"https://www.draftkings.com/lineup/getavailableplayers?draftGroupId={dg_id}"
    try:
        r = requests.get(old, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            data = r.json()
            draftables = data.get("draftables") or []
            if draftables:
                print(f"    draftables {dg_id}: {len(draftables)}")
                return "draftables", draftables
        else:
            print(f"    draftables {dg_id}: {r.status_code}")
    except Exception as e:
        print(f"    draftables {dg_id} error: {e}")

    try:
        r = requests.get(new, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            print(f"    available players {dg_id}: {r.status_code}")
            return "available", []
        data = r.json()
        players = data.get("playerList") or []
        print(f"    available players {dg_id}: {len(players)}")
        return "available", players
    except Exception as e:
        print(f"    available players {dg_id} error: {e}")
        return "available", []


def players_from_draftables(draftables: list) -> tuple[list[dict], dict]:
    comps = {}
    versions = defaultdict(list)
    for p in draftables:
        comp = p.get("competition") or {}
        cid = str(comp.get("competitionId") or "")
        if cid and cid not in comps:
            comps[cid] = {"name": comp.get("name") or "", "startTime": comp.get("startTime") or ""}
        salary = int(p.get("salary") or 0)
        player_id = str(p.get("playerId") or "")
        draftable_id = str(p.get("draftableId") or "")
        name = p.get("displayName") or ""
        if salary < 1000 or not player_id or not name:
            continue
        versions[player_id].append({
            "draftable_id": draftable_id or player_id,
            "name": name,
            "first": p.get("firstName") or "",
            "last": p.get("lastName") or "",
            "salary": salary,
            "pos": p.get("position") or "G",
            "team": p.get("teamAbbreviation") or "",
            "image": clean_image(p.get("playerImage50") or p.get("imageUrl") or ""),
            "game": comps.get(cid, {}).get("name", ""),
            "start": comps.get(cid, {}).get("startTime", ""),
        })
    return versions, comps


def players_from_available(players: list, tournament: str, start_iso: str) -> dict:
    versions = defaultdict(list)
    for p in players:
        if p.get("IsDisabledFromDrafting"):
            continue
        salary = int(p.get("s") or 0)
        if salary < 1000:
            continue
        first = p.get("fn") or ""
        last = p.get("ln") or ""
        name = norm_name(f"{first} {last}")
        player_id = str(p.get("pid") or "")
        draftable_id = str(p.get("did") or "")
        if draftable_id in ("", "0"):
            draftable_id = str(p.get("pdkid") or player_id)
        if not player_id or not name:
            continue
        versions[player_id].append({
            "draftable_id": draftable_id,
            "name": name,
            "first": first,
            "last": last,
            "salary": salary,
            "pos": "G",
            "team": "",
            "image": clean_image(p.get("imgSm") or p.get("imgLg") or ""),
            "game": tournament,
            "start": start_iso,
        })
    return versions


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

    contests = data.get("Contests") or []
    if not contests:
        print("No contests found")
        return

    draft_groups: dict[str, dict] = {}
    for c in contests:
        cname = c.get("n") or c.get("Name") or ""
        if classify_contest(cname) == "SKIP":
            continue
        dg = str(c.get("dg") or c.get("DraftGroupId") or "")
        cid = str(c.get("id") or c.get("ContestId") or "")
        if not dg or not cid:
            continue
        if dg not in draft_groups:
            draft_groups[dg] = {"contest_ids": [], "contest_names": [], "suffix": "", "start": ""}
        draft_groups[dg]["contest_ids"].append(cid)
        draft_groups[dg]["contest_names"].append(cname)

    for g in data.get("DraftGroups") or []:
        dg = str(g.get("DraftGroupId") or "")
        if dg in draft_groups:
            draft_groups[dg]["suffix"] = g.get("ContestStartTimeSuffix") or ""
            draft_groups[dg]["start"] = g.get("StartDate") or ""

    for dg_id, group in draft_groups.items():
        group["slate_type"] = classify_group(group["contest_names"], group.get("suffix") or "")

    print(f"Found {len(draft_groups)} draft groups")
    for dg_id, g in draft_groups.items():
        print(f"  DG {dg_id}: {g['slate_type']} {g.get('suffix') or ''} ({len(g['contest_ids'])} contests)")

    def group_priority(item):
        _, g = item
        st = g["slate_type"].lower()
        if st == "classic":
            return (0, -len(g["contest_ids"]))
        if st == "showdown":
            return (1, -len(g["contest_ids"]))
        if st == "late showdown":
            return (2, -len(g["contest_ids"]))
        if "tiers" in st:
            return (3, -len(g["contest_ids"]))
        return (4, -len(g["contest_ids"]))

    ordered = sorted(draft_groups.items(), key=group_priority)
    rich_rows = []
    simple_rows = []
    seen_simple = set()

    for dg_id, group in ordered:
        slate_type = group["slate_type"]
        if slate_type in ("Tiers", "Snake", "Single Stat", "LPGA", "DP World", "Turbo"):
            print(f"  Skip DG {dg_id} ({slate_type})")
            continue
        print(f"  Fetching players for DG {dg_id} ({slate_type})…")
        source, raw_players = fetch_pool(dg_id)
        if not raw_players:
            continue

        tournament = slate_label(group)
        start_iso = group.get("start") or ""
        if source == "draftables":
            versions, comps = players_from_draftables(raw_players)
            if comps:
                named = next((c["name"] for c in comps.values() if c.get("name")), "")
                if named:
                    tournament = named
                start_iso = next((c["startTime"] for c in comps.values() if c.get("startTime")), start_iso)
        else:
            versions = players_from_available(raw_players, tournament, start_iso)

        if not versions:
            print(f"    No salaried players in DG {dg_id}")
            continue

        start_times = []
        if start_iso:
            try:
                start_times.append(datetime.fromisoformat(str(start_iso).replace("Z", "+00:00")))
            except Exception:
                pass
        slate_header = slate_type
        if start_times:
            min_start = min(start_times)
            date_part = min_start.astimezone(ZoneInfo("America/New_York")).strftime("%-m/%-d")
            time_part = min_start.astimezone(ZoneInfo("America/New_York")).strftime("%-I:%M%p")
            slate_header = f"{date_part} {time_part} ({tournament})"

        is_showdown = "Showdown" in slate_type
        for player_id, vers in versions.items():
            vers.sort(key=lambda x: x["salary"], reverse=True)
            chosen = [("Captain", vers[0]), ("Flex", vers[1])] if is_showdown and len(vers) >= 2 else [("Standard", vers[0])]
            for role, v in chosen:
                pos = "CPT" if role == "Captain" else v["pos"]
                label = f"{v['name']} - {slate_type}" + (f" ({role})" if role != "Standard" else "")
                rich_rows.append({
                    "Player Name - Slate Type": label,
                    "Contest IDs": ";".join(group["contest_ids"][:20]),
                    "Player ID": player_id,
                    "Draftable ID": v["draftable_id"],
                    "Player Name": v["name"],
                    "First Name": v["first"],
                    "Last Name": v["last"],
                    "Salary": v["salary"],
                    "Position": pos,
                    "Team": v["team"],
                    "Game": v["game"] or tournament,
                    "Game Start Time": format_datetime(v["start"] or start_iso),
                    "Player Image": v["image"],
                    "Tournament": v["game"] or tournament,
                    "Slate Type": slate_type,
                    "Game Type": "PGA",
                    "Date": format_date_only(v["start"] or start_iso),
                    "Role": role,
                    "Contest Names": ";".join(group["contest_names"][:10]),
                    "Contest IDs (Full)": ";".join(group["contest_ids"][:20]),
                    "Slate Header": slate_header,
                    "Make the Cut": "",
                })
                if slate_type == "Classic" and role == "Standard":
                    key = (tournament.lower(), norm_name(v["name"]).lower())
                    if key not in seen_simple:
                        seen_simple.add(key)
                        simple_rows.append({
                            "player_name": norm_name(v["name"]),
                            "salary": int(v["salary"]),
                            "tournament": tournament,
                            "slate_type": slate_type,
                            "player_image": v["image"],
                            "player_id": player_id,
                            "draftable_id": v["draftable_id"],
                            "game_start": format_datetime(v["start"] or start_iso),
                            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        })

    if not rich_rows:
        print("No player rows generated — leaving the existing CSVs in place")
        return

    rich_rows.sort(key=lambda x: int(x["Salary"]) if str(x["Salary"]).isdigit() else 0, reverse=True)
    simple_rows.sort(key=lambda x: x["salary"], reverse=True)

    drafttable_path = DATA_DIR / "drafttable.csv"
    with drafttable_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DRAFTTABLE_FIELDS)
        writer.writeheader()
        writer.writerows(rich_rows)
    print(f"Wrote {len(rich_rows)} rows → {drafttable_path}")

    salaries_path = DATA_DIR / "dk_salaries.csv"
    with salaries_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DK_SALARIES_FIELDS)
        writer.writeheader()
        writer.writerows(simple_rows)
    print(f"Wrote {len(simple_rows)} rows → {salaries_path}")
    if simple_rows:
        print(f"Top Classic salary: {simple_rows[0]['player_name']} ${simple_rows[0]['salary']:,}")
        print(f"Tournament label: {simple_rows[0]['tournament']}")


if __name__ == "__main__":
    main()
