import requests
import pandas as pd
import json
import os
import time
from datetime import datetime

API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"

STATS = [
    ("02675", "SG_Total"),
    ("101", "Driving_Distance"),
    ("120", "Scoring_Average"),
]

def fetch_stat(stat_id: str, event_id: str) -> pd.DataFrame:
    year = int(event_id[1:5])

    # Trying the most common correct structure
    payload = {
        "operationName": "StatDetails",
        "variables": {
            "tourCode": "R",
            "statId": str(stat_id),
            "year": year,
            "eventQuery": {
                "id": event_id          # ← changed from eventId to id
            }
        },
        "query": """query StatDetails($tourCode: TourCode!, $statId: String!, $year: Int, $eventQuery: StatDetailEventQuery) {
          statDetails(tourCode: $tourCode, statId: $statId, year: $year, eventQuery: $eventQuery) {
            rows {
              ... on StatDetailsPlayer {
                __typename
                playerId
                playerName
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
        "Origin": "https://www.pgatour.com",
        "Referer": "https://www.pgatour.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    r = requests.post(
        "https://orchestrator.pgatour.com/graphql",
        json=payload,
        headers=headers,
        timeout=30
    )

    print(f"      Status code: {r.status_code}")
    
    data = r.json()
    
    if "errors" in data:
        print(f"      GraphQL Errors: {data['errors']}")
        return pd.DataFrame()
        
    if not data.get("data") or not data["data"].get("statDetails"):
        print(f"      No statDetails returned")
        return pd.DataFrame()

    rows = data["data"]["statDetails"]["rows"]
    print(f"      Number of rows: {len(rows)}")
    
    result = []
    for item in rows:
        if item.get("__typename") != "StatDetailsPlayer":
            continue
        result.append({
            "player_id": item.get("playerId"),
            "player": item.get("playerName"),
            "rank": item.get("rank"),
            "value": item["stats"][0]["statValue"] if item.get("stats") else None
        })
    return pd.DataFrame(result)


def process_tournament(tournament_key: str):
    with open("data/tournaments/_index.json") as f:
        index = json.load(f)

    tournament = index[tournament_key]
    print(f"\nProcessing: {tournament['name']}\n")

    year = "2025"
    event_id = tournament["years"][year]
    
    print(f"→ Testing {year} ({event_id})")
    
    for sid, name in STATS:
        print(f"   Trying {name}...")
        df = fetch_stat(sid, event_id)
        if not df.empty:
            print(f"   ✓ Success! {len(df)} players")
            print(df.head(3))
        else:
            print(f"   ✗ Failed")
        time.sleep(1)


if __name__ == "__main__":
    process_tournament("rocket_classic")
