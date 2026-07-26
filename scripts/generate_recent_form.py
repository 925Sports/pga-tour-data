import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "recent_form.csv"

# All available years
YEARS = list(range(2017, 2027))  # 2017 through 2026
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
                df["source_file"] = path.name
                frames.append(df)
            else:
                print(f"Skipping missing file: {path.name}")
    if not frames:
        raise FileNotFoundError("No historical log files found")
    logs = pd.concat(frames, ignore_index=True)
    return logs

def normalize_name(name):
    if pd.isna(name):
        return ""
    return str(name).lower().replace(" ", "").replace(".", "").replace("-", "")

def get_player_history(logs, player_name, name_adjusted=None):
    candidates = [player_name, name_adjusted]
    candidates = [c for c in candidates if c and str(c).strip()]

    for cand in candidates:
        if "player_name" in logs.columns:
            mask = logs["player_name"] == cand
            if mask.any():
                return logs[mask]
        if "name_adjusted" in logs.columns:
            mask = logs["name_adjusted"] == cand
            if mask.any():
                return logs[mask]

    # Normalized fallback
    target = normalize_name(player_name or name_adjusted)
    if "player_name" in logs.columns:
        temp = logs.copy()
        temp["_norm"] = temp["player_name"].apply(normalize_name)
        subset = temp[temp["_norm"] == target]
        if len(subset) > 0:
            return subset
    return pd.DataFrame()

def format_finish(row):
    fin = str(row.get("fin_text", "")).upper()
    if "CUT" in fin:
        return "CUT"
    pos = row.get("pos") or row.get("POS") or row.get("fin_text") or ""
    sg = row.get("sg_total")
    if pd.isna(sg):
        return str(pos) if pos else "—"
    try:
        return f"{pos} ({float(sg):.2f})"
    except:
        return str(pos)

def calculate_cut_streak(history):
    streak = 0
    for _, row in history.iterrows():
        fin = str(row.get("fin_text", "")).upper()
        if "CUT" in fin:
            break
        streak += 1
    return streak

def main():
    print("Loading field...")
    field = load_field()
    print(f"Current event players: {len(field)}")

    print("Loading historical logs (2017–2026)...")
    logs = load_all_logs()
    print(f"Total log rows: {len(logs)}")

    date_col = "event_completed"
    if date_col not in logs.columns:
        raise KeyError(f"'{date_col}' column not found in logs")

    print(f"Using date column: {date_col}")
    logs[date_col] = pd.to_datetime(logs[date_col], errors="coerce")
    logs = logs.sort_values(date_col, ascending=False)

    rows = []

    for _, player in field.iterrows():
        history = get_player_history(logs, player.get("player_name"), player.get("name_adjusted"))
        # No limit — use all available starts
        history = history.copy()

        last7 = history.head(7)

        rf = []
        for i in range(7):
            if i < len(last7):
                rf.append(format_finish(last7.iloc[i]))
            else:
                rf.append("—")

        cut_streak = calculate_cut_streak(history)

        finishes = []
        for _, row in history.iterrows():
            fin = str(row.get("fin_text", "")).upper()
            if "CUT" not in fin:
                pos = row.get("pos") or row.get("POS")
                try:
                    finishes.append(float(pos))
                except:
                    pass

        avg5 = round(np.mean(finishes[:5]), 1) if len(finishes) >= 5 else None
        avg10 = round(np.mean(finishes[:10]), 1) if len(finishes) >= 10 else (round(np.mean(finishes), 1) if finishes else None)

        # Weighted Value (based on last 7 starts)
        weights = [3.2, 2.6, 2.1, 1.6, 1.3, 1.0, 0.8]
        weighted_sum = 0
        weight_total = 0
        for i in range(min(7, len(last7))):
            sg = last7.iloc[i].get("sg_total")
            if pd.notna(sg):
                try:
                    weighted_sum += float(sg) * weights[i]
                    weight_total += weights[i]
                except:
                    pass

        value = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0

        made = sum(1 for _, r in history.iterrows() if "CUT" not in str(r.get("fin_text", "")).upper())
        cut_pct = round(made / len(history) * 100, 1) if len(history) > 0 else None

        rows.append({
            "player_name": player.get("player_name"),
            "name_adjusted": player.get("name_adjusted") or player.get("player_name"),
            "salary": player.get("salary"),
            "event_name": player.get("event_name"),
            "date_start": player.get("date_start"),
            "cut_streak": cut_streak,
            "rf1": rf[0],
            "rf2": rf[1],
            "rf3": rf[2],
            "rf4": rf[3],
            "rf5": rf[4],
            "rf6": rf[5],
            "rf7": rf[6],
            "rflst5": avg5,
            "rflst10": avg10,
            "value": value,
            "cut_pct": cut_pct,
            "starts_count": len(history),
            "updated_at": datetime.utcnow().isoformat() + "Z"
        })

    df = pd.DataFrame(rows)

    df["value_rank"] = df["value"].rank(ascending=False, method="min").astype("Int64")
    df["rflst5_rank"] = df["rflst5"].rank(ascending=True, method="min").astype("Int64")
    df["rflst10_rank"] = df["rflst10"].rank(ascending=True, method="min").astype("Int64")

    df = df.sort_values("value_rank")
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"Successfully wrote {len(df)} players to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
