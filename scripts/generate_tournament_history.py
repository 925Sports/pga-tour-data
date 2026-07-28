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

    payload = {
        "operationName": "StatDetails",
        "variables": {
            "tourCode": "R",
            "statId": str(stat_id),
            "year": year,
            "eventQuery": {
                "eventId": event_id
            }
        },
        "query": """query StatDetails($tourCode: TourCode!, $statId: String!, $year: Int, $eventQuery: StatDetailEventQuery) {
          statDetails(tourCode: $tourCode, statId: $statId, year: $year, eventQuery: $eventQuery) {
            tourCode
            year
            statId
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
    
    try:
        data = r.json()
        print(f"      Response keys: {list(data.keys())}")
        
        if "errors" in data:
            print(f"      GraphQL Errors: {data['errors']}")
            return pd.DataFrame()
            
        if not data.get("data") or not data["data"].get("statDetails"):
            print(f"      No statDetails in response")
            print(f"      Full response: {str(data)[:500]}")
            return pd.DataFrame()

        rows = data["data"]["statDetails"]["rows"]
        print(f"      Number of rows returned: {len(rows)}")
        
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

    except Exception as e:
        print(f"      Error parsing response: {e}")
        print(f"      Raw response: {r.text[:500]}")
        return pd.DataFrame()


def process_tournament(tournament_key: str):
    with open("data/tournaments/_index.json") as f:
        index = json.load(f)

    tournament = index[tournament_key]
    print(f"\nProcessing: {tournament['name']}\n")

    # Only test the most recent year first
    year = "2025"
    event_id = tournament["years"][year]
    
    print(f"→ Testing {year} ({event_id})")
    
    for sid, name in STATS:
        print(f"   Trying {name}...")
        df = fetch_stat(sid, event_id)
        if not df.empty:
            print(f"   ✓ Success! {len(df)} players")
            print(df.head())
        else:
            print(f"   ✗ Failed")
        time.sleep(1)


if __name__ == "__main__":
    process_tournament("rocket_classic")
