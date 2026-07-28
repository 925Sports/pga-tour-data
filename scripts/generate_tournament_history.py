import requests
import pandas as pd
import json
import os
import time
from datetime import datetime

API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"

# Key stats we want for tournament history
STATS = [
    ("02675", "SG_Total"),
    ("02674", "SG_Tee_to_Green"),
    ("02567", "SG_OTT"),
    ("02568", "SG_APP"),
    ("02569", "SG_ARG"),
    ("02564", "SG_PUTT"),
    ("101", "Driving_Distance"),
    ("102", "Driving_Accuracy"),
    ("103", "GIR"),
    ("130", "Scrambling"),
    ("120", "Scoring_Average"),
    ("156", "Birdie_Average"),
    ("119", "Putts_Per_Round"),
]

def fetch_stat(stat_id: str, event_id: str) -> pd.DataFrame:
    """Pull one stat for a specific tournament"""
    payload = {
        "operationName": "StatDetails",
        "variables": {
            "tourCode": "R",
            "statId": str(stat_id),
            "year": int(event_id[1:5]),          # extract year from R2025xxx
            "eventQuery": {
                "eventId": event_id
            }
        },
        "query": """query StatDetails($tourCode: TourCode!, $statId: String!, $year: Int, $eventQuery: StatDetailEventQuery) {
          statDetails(tourCode: $tourCode, statId: $statId, year: $year, eventQuery: $eventQuery) {
            rows {
              ... on StatDetailsPlayer {
                __typename
                playerId
                playerName
                country
                rank
                stats { statName statValue }
              }
            }
          }
        }"""
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "x-pgat-platform": "web",
        "x-amz-user-agent": "aws-amplify/3.0.7",
        "Origin": "https://www.pgatour.com",
        "Referer": "https://www.pgatour.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    r = requests.post(
        "https://orchestrator.pgatour.com/graphql",
        json=payload,
        headers=headers,
        timeout=30
    )
    r.raise_for_status()

    data = r.json()
    rows = []
    
    if not data.get("data") or not data["data"].get("statDetails"):
        return pd.DataFrame()

    for item in data["data"]["statDetails"]["rows"]:
        if item.get("__typename") != "StatDetailsPlayer":
            continue
        rows.append({
            "player_id": item["playerId"],
            "player": item["playerName"],
            "country": item.get("country", ""),
            "rank": item["rank"],
            "value": item["stats"][0]["statValue"] if item.get("stats") else None
        })
    return pd.DataFrame(rows)


def process_tournament(tournament_key: str):
    # Load the index
    with open("data/tournaments/_index.json") as f:
        index = json.load(f)

    if tournament_key not in index:
        raise ValueError(f"Tournament '{tournament_key}' not found in _index.json")

    tournament = index[tournament_key]
    print(f"\nProcessing: {tournament['name']}")
    print(f"Course: {tournament['course']}\n")

    output_dir = f"data/tournaments/{tournament_key}"
    os.makedirs(output_dir, exist_ok=True)

    for year, event_id in tournament["years"].items():
        print(f"→ {year} ({event_id})")
        dfs = []

        for sid, name in STATS:
            try:
                df = fetch_stat(sid, event_id)
                if df.empty:
                    print(f"   ✗ {name}: no data")
                    continue

                df = df.rename(columns={
                    "rank": f"{name}_Rank",
                    "value": name
                })
                dfs.append(df[["player_id", "player", "country", name, f"{name}_Rank"]])
                print(f"   ✓ {name}: {len(df)} players")
                time.sleep(0.7)
            except Exception as e:
                print(f"   ✗ {name}: {e}")

        if not dfs:
            print(f"   No data for {year}, skipping...\n")
            continue

        # Merge all stats
        merged = dfs[0]
        for df in dfs[1:]:
            merged = merged.merge(
                df.drop(columns=["player", "country"]),
                on="player_id",
                how="outer"
            )

        merged["year"] = year
        merged["event_id"] = event_id
        merged["tournament"] = tournament["name"]
        merged["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # Sort by SG Total if available
        if "SG_Total_Rank" in merged.columns:
            merged = merged.sort_values("SG_Total_Rank", na_position="last")

        output_path = f"{output_dir}/{year}.csv"
        merged.to_csv(output_path, index=False)
        print(f"   Saved → {output_path} ({len(merged)} players)\n")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scripts/generate_tournament_history.py rocket_classic")
        sys.exit(1)

    tournament_key = sys.argv[1]
    process_tournament(tournament_key)
    print("Done.")
