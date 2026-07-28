import requests
import pandas as pd
from datetime import datetime
import os

API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"
YEAR = 2026
OUTPUT_PATH = "data/pga_season_stats.csv"

STATS = [
    ("02675", "SG_Total"),
    ("02567", "SG_OTT"),
    ("02568", "SG_APP"),
    ("02569", "SG_ARG"),
    ("02564", "SG_PUTT"),
    ("101",   "Driving_Distance"),
    ("102",   "Driving_Accuracy"),
    ("103",   "GIR"),
    ("130",   "Scrambling"),
    ("120",   "Scoring_Average"),
]

def fetch_stat(stat_id: str) -> pd.DataFrame:
    payload = {
        "operationName": "StatDetails",
        "variables": {
            "tourCode": "R",
            "statId": str(stat_id),
            "year": YEAR,
            "eventQuery": None
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

    rows = []
    for item in r.json()["data"]["statDetails"]["rows"]:
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


def main():
    print(f"Starting PGA season stats scrape for {YEAR}...")
    dfs = []

    for sid, name in STATS:
        try:
            df = fetch_stat(sid)
            df = df.rename(columns={
                "rank": f"{name}_Rank",
                "value": name
            })
            dfs.append(df[["player_id", "player", "country", name, f"{name}_Rank"]])
            print(f"✓ {name}: {len(df)} players")
        except Exception as e:
            print(f"✗ {name}: {e}")

    if not dfs:
        raise SystemExit("No stats were successfully fetched.")

    # Merge all stats on player_id
    merged = dfs[0]
    for df in dfs[1:]:
        merged = merged.merge(
            df.drop(columns=["player", "country"]),
            on="player_id",
            how="outer"
        )

    merged["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    merged = merged.sort_values("SG_Total_Rank", na_position="last")

    os.makedirs("data", exist_ok=True)
    merged.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved {len(merged)} players → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
