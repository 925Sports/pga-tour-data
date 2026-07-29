import requests
import pandas as pd
from datetime import datetime
import os
import time
import sys
import glob

API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"
YEAR = 2026

# Tour configuration
TOURS = {
    "pga": {
        "code": "R",
        "name": "PGA Tour",
        "live_path": "data/pga_season_stats.csv",
        "snapshot_dir": "data/snapshots/pga"
    },
    "kft": {
        "code": "S",
        "name": "Korn Ferry Tour",
        "live_path": "data/korn_ferry_season_stats.csv",
        "snapshot_dir": "data/snapshots/kft"
    },
    "dp": {
        "code": "H",
        "name": "DP World Tour",
        "live_path": "data/dp_world_season_stats.csv",
        "snapshot_dir": "data/snapshots/dp"
    }
}

STATS = [
    # Strokes Gained
    ("02675", "SG_Total"),
    ("02674", "SG_Tee_to_Green"),
    ("02567", "SG_OTT"),
    ("02568", "SG_APP"),
    ("02569", "SG_ARG"),
    ("02564", "SG_PUTT"),

    # Composites
    ("158", "Ball_Striking"),
    ("129", "Total_Driving"),

    # Driving
    ("101", "Driving_Distance"),
    ("102", "Driving_Accuracy"),
    ("103", "GIR"),
    ("02438", "Good_Drive_Percentage"),

    # Scoring
    ("120", "Scoring_Average"),
    ("156", "Birdie_Average"),
    ("155", "Eagle_Average"),
    ("352", "Birdie_or_Better_Percentage"),
    ("02414", "Bogey_Avoidance"),
    ("02415", "Bounce_Back"),
    ("02417", "Stroke_Differential_Field_Average"),
    ("219", "Final_Round_Performance"),

    # Round Scoring
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

    # Short Game / Putting
    ("130", "Scrambling"),
    ("111", "Sand_Save_Percentage"),
    ("119", "Putts_Per_Round"),
    ("426", "Three_Putt_Avoidance"),

    # Par Scoring
    ("02418", "Par_3_Scoring"),
    ("02419", "Par_4_Scoring"),
    ("02420", "Par_5_Scoring"),
]


def fetch_stat(stat_id: str, tour_code: str) -> pd.DataFrame:
    payload = {
        "operationName": "StatDetails",
        "variables": {
            "tourCode": tour_code,
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

    data = r.json()
    if not data.get("data") or not data["data"].get("statDetails"):
        return pd.DataFrame()

    rows = []
    for item in data["data"]["statDetails"]["rows"]:
        if item.get("__typename") != "StatDetailsPlayer":
            continue
        rows.append({
            "player_id": str(item["playerId"]),  # force string early
            "player": item["playerName"],
            "country": item.get("country", ""),
            "rank": item["rank"],
            "value": item["stats"][0]["statValue"] if item.get("stats") else None
        })
    return pd.DataFrame(rows)


def get_latest_snapshot(snapshot_dir: str):
    files = sorted(glob.glob(f"{snapshot_dir}/*_*.csv"))
    return files[-1] if files else None


def data_has_updated(new_df: pd.DataFrame, snapshot_dir: str) -> bool:
    latest = get_latest_snapshot(snapshot_dir)
    if latest is None:
        return True

    try:
        old_df = pd.read_csv(latest)

        if "SG_Total" not in new_df.columns or "SG_Total" not in old_df.columns:
            return True

        # Force both sides to string to avoid type mismatch
        new_df = new_df.copy()
        old_df = old_df.copy()
        new_df["player_id"] = new_df["player_id"].astype(str)
        old_df["player_id"] = old_df["player_id"].astype(str)

        new_vals = new_df.set_index("player_id")["SG_Total"]
        old_vals = old_df.set_index("player_id")["SG_Total"]

        common = new_vals.index.intersection(old_vals.index)
        if len(common) == 0:
            return True

        changed = (new_vals.loc[common] != old_vals.loc[common]).sum()
        change_pct = changed / len(common)
        print(f"Data change check: {changed} players changed ({change_pct:.1%})")
        return change_pct > 0.03

    except Exception as e:
        print(f"Could not compare snapshots: {e}")
        return True


def process_tour(tour_key: str):
    if tour_key not in TOURS:
        raise ValueError(f"Unknown tour: {tour_key}. Use: pga, kft, or dp")

    config = TOURS[tour_key]
    tour_code = config["code"]
    tour_name = config["name"]
    live_path = config["live_path"]
    snapshot_dir = config["snapshot_dir"]

    print(f"\n{'='*50}")
    print(f"Processing: {tour_name} ({tour_code})")
    print(f"{'='*50}\n")

    os.makedirs(os.path.dirname(live_path), exist_ok=True)
    os.makedirs(snapshot_dir, exist_ok=True)

    # Quick check with SG Total
    print("Checking if new data is available...")
    test_df = fetch_stat("02675", tour_code)
    if test_df.empty:
        print(f"No data returned for {tour_name}. This tour may not support these stats.")
        return

    test_df = test_df.rename(columns={"rank": "SG_Total_Rank", "value": "SG_Total"})

    if not data_has_updated(test_df, snapshot_dir):
        print("Data has not updated since last snapshot. Skipping.")
        return

    print("New data detected! Running full scrape...\n")

    dfs = []
    success_count = 0

    for sid, name in STATS:
        try:
            df = fetch_stat(sid, tour_code)
            if df.empty:
                print(f"✗ {name}")
                continue

            df = df.rename(columns={
                "rank": f"{name}_Rank",
                "value": name
            })
            dfs.append(df[["player_id", "player", "country", name, f"{name}_Rank"]])
            print(f"✓ {name}: {len(df)} players")
            success_count += 1
            time.sleep(0.6)

        except Exception as e:
            print(f"✗ {name}: {e}")

    if not dfs:
        print("No stats were successfully fetched.")
        return

    print(f"\nSuccessfully pulled {success_count}/{len(STATS)} stats")

    merged = dfs[0]
    for df in dfs[1:]:
        merged = merged.merge(
            df.drop(columns=["player", "country"]),
            on="player_id",
            how="outer"
        )

    if "SG_OTT" in merged.columns and "SG_APP" in merged.columns:
        merged["SG_Ball_Striking_Calc"] = merged["SG_OTT"] + merged["SG_APP"]

    today = datetime.utcnow().strftime("%Y-%m-%d")
    merged["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    merged["tour"] = tour_name

    if "SG_Total_Rank" in merged.columns:
        merged = merged.sort_values("SG_Total_Rank", na_position="last")

    # Save live file
    merged.to_csv(live_path, index=False)
    print(f"\nSaved live file → {live_path}")

    # Save snapshot
    snapshot_path = f"{snapshot_dir}/{tour_key}_season_stats_{today}.csv"
    merged.to_csv(snapshot_path, index=False)
    print(f"Saved snapshot → {snapshot_path}")
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/generate_tour_stats.py [pga|kft|dp]")
        sys.exit(1)

    tour = sys.argv[1].lower()
    process_tour(tour)
