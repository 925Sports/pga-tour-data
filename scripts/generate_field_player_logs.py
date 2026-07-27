import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "field_player_logs.csv"

YEARS = list(range(2017, 2027))
TOUR_TYPES = ["PGA", "OTHER"]

def load_field():
    field = pd.read_csv(DATA_DIR / "upcoming_field.csv")
    pga = field[field["tour"].astype(str).str.lower() == "pga"].copy()

    date_col = None
    for col in ["date_start", "Date", "date", "event_date", "start_date", "event_completed"]:
        if col in pga.columns:
            date_col = col
            break

    if date_col:
        pga[date_col] = pd.to_datetime(pga[date_col], errors="coerce")
        earliest = pga[date_col].min()
        current = pga[pga[date_col] == earliest]
    else:
        current = pga

    return current

def load_all_logs():
    frames = []
    for year in YEARS:
        for t in TOUR_TYPES:
            path = DATA_DIR / f"pga_tour_player_logs_{year}_{t}.csv"
            if path.exists():
                print(f"Loading {path.name}...")
                df = pd.read_csv(path, low_memory=False)
                frames.append(df)
            else:
                print(f"Skipping missing file: {path.name}")
    if not frames:
        raise FileNotFoundError("No historical log files found")
    return pd.concat(frames, ignore_index=True)

def normalize_name(name):
    if pd.isna(name):
        return ""
    return str(name).lower().replace(" ", "").replace(".", "").replace("-", "")

def main():
    print("Loading current field...")
    field = load_field()
    print(f"Players in current field: {len(field)}")

    field_names = set()
    name_map = {}

    for _, row in field.iterrows():
        for col in ["name_adjusted", "player_name"]:
            val = row.get(col)
            if pd.notna(val) and str(val).strip():
                field_names.add(str(val).strip())
                name_map[normalize_name(val)] = str(val).strip()

    print("Loading historical logs...")
    logs = load_all_logs()
    print(f"Total historical rows: {len(logs)}")

    logs["_norm_name"] = logs["player_name"].apply(normalize_name)

    matched = logs[logs["_norm_name"].isin(name_map.keys())].copy()
    print(f"Matched starts for current field players: {len(matched)}")

    if "event_completed" in matched.columns:
        matched["event_completed"] = pd.to_datetime(matched["event_completed"], errors="coerce")
        matched = matched.sort_values(["_norm_name", "event_completed"], ascending=[True, False])

    matched = matched.drop(columns=["_norm_name"])
    matched["name_adjusted"] = matched["player_name"]
    matched["updated_at"] = datetime.utcnow().isoformat() + "Z"

    matched.to_csv(OUTPUT_FILE, index=False)
    print(f"Successfully wrote {len(matched)} rows to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
