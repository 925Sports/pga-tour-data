import requests
import pandas as pd
from datetime import datetime
import os
import time

API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"
YEAR = 2026
OUTPUT_PATH = "data/pga_season_stats.csv"

# Expanded relevant season-long stats
STATS = [
    # ===== STROKES GAINED =====
    ("02675", "SG_Total"),
    ("02674", "SG_Tee_to_Green"),
    ("02567", "SG_OTT"),
    ("02568", "SG_APP"),
    ("02569", "SG_ARG"),
    ("02564", "SG_PUTT"),

    # ===== COMPOSITES =====
    ("158", "Ball_Striking"),          # Total Driving rank + GIR rank
    ("129", "Total_Driving"),          # Driving Distance rank + Accuracy rank

    # ===== DRIVING =====
    ("101", "Driving_Distance"),
    ("102", "Driving_Accuracy"),
    ("103", "GIR"),

    # ===== SCORING =====
    ("120", "Scoring_Average"),
    ("156", "Birdie_Average"),
    ("155", "Eagle_Average"),
    ("02414", "Bogey_Avoidance"),
    ("02415", "Bounce_Back"),
    ("02416", "Birdie_or_Better_Percentage"),
    ("02417", "Birdie_to_Bogey_Ratio"),

    # ===== ROUND SCORING (R1-R4 + Early/Late) =====
    ("248", "Round_1_Scoring"),
    ("249", "Round_1_Scoring_Early"),
    ("250", "Round_1_Scoring_Late"),
    ("251", "Round_2_Scoring"),
    ("252", "Round_2_Scoring_Early"),
    ("253", "Round_2_Scoring_Late"),
    ("254", "Round_3_Scoring"),
    ("255", "Round_3_Scoring_Early"),
    ("256", "Round_3_Scoring_Late"),
    ("257", "Round_4_Scoring"),
    ("258", "Round_4_Scoring_Early"),
    ("259", "Round_4_Scoring_Late"),

    # ===== SHORT GAME / PUTTING =====
    ("130", "Scrambling"),
    ("111", "Sand_Save_Percentage"),
    ("119", "Putts_Per_Round"),
    ("426", "Three_Putt_Avoidance"),

    # ===== PAR SCORING =====
    ("02418", "Par_3_Scoring"),
    ("02419", "Par_4_Scoring"),
    ("02420", "Par_5_Scoring"),
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
    print(f"Requesting {len(STATS)} stats...\n")

    dfs = []
    success_count = 0

    for sid, name in STATS:
        try:
            df = fetch_stat(sid)
            if df.empty:
                print(f"✗ {name} ({sid}): empty response")
                continue

            df = df.rename(columns={
                "rank": f"{name}_Rank",
                "value": name
            })
            dfs.append(df[["player_id", "player", "country", name, f"{name}_Rank"]])
            print(f"✓ {name}: {len(df)} players")
            success_count += 1

            # Small delay to be polite to the API
            time.sleep(0.8)

        except Exception as e:
            print(f"✗ {name} ({sid}): {e}")

    if not dfs:
        raise SystemExit("No stats were successfully fetched.")

    print(f"\nSuccessfully pulled {success_count}/{len(STATS)} stats")

    # Merge all stats on player_id
    merged = dfs[0]
    for df in dfs[1:]:
        merged = merged.merge(
            df.drop(columns=["player", "country"]),
            on="player_id",
            how="outer"
        )

    # Calculate a couple of useful derived columns
    if "SG_OTT" in merged.columns and "SG_APP" in merged.columns:
        merged["SG_Ball_Striking_Calc"] = merged["SG_OTT"] + merged["SG_APP"]

    merged["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Prefer sorting by SG Total when available
    if "SG_Total_Rank" in merged.columns:
        merged = merged.sort_values("SG_Total_Rank", na_position="last")
    else:
        merged = merged.sort_values("player")

    os.makedirs("data", exist_ok=True)
    merged.to_csv(OUTPUT_PATH, index=False)

    print(f"\nSaved {len(merged)} players → {OUTPUT_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
