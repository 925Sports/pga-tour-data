import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "recent_form.csv"

# Years to look at for recent form
YEARS = [2025, 2024, 2023]
TOUR_TYPES = ["PGA", "OTHER"]

def load_field():
    field = pd.read_csv(DATA_DIR / "upcoming_field.csv")
    # Keep only the earliest PGA event
    pga = field[field["tour"].str.lower() == "pga"].copy()
    pga["date_start"] = pd.to_datetime(pga["date_start"], errors="coerce")
    earliest = pga["date_start"].min()
    current = pga[pga["date_start"] == earliest]
    return current

def load_all_logs():
    frames = []
    for year in YEARS:
        for t in TOUR_TYPES:
            path = DATA_DIR / f"pga_tour_player_logs_{year}_{t}.csv"
            if path.exists():
                df = pd.read_csv(path)
                df["source_file"] = path.name
                frames.append(df)
    if not frames:
        raise FileNotFoundError("No historical log files found")
    logs = pd.concat(frames, ignore_index=True)
    return logs

def normalize_name(name):
    if pd.isna(name):
        return ""
    return str(name).lower().replace(" ", "").replace(".", "").replace("-", "")

def get_player_history(logs, player_name, name_adjusted=None):
    # Try exact matches first
    candidates = [player_name, name_adjusted]
    candidates = [c for c in candidates if c and str(c).strip()]
    
    for cand in candidates:
        mask = (logs["player_name"] == cand) | (logs.get("name_adjusted", pd.Series()) == cand)
        subset = logs[mask]
        if len(subset) > 0:
            return subset
    
    # Fallback normalized match
    target = normalize_name(player_name or name_adjusted)
    logs["_norm"] = logs["player_name"].apply(normalize_name)
    if "name_adjusted" in logs.columns:
        logs["_norm2"] = logs["name_adjusted"].apply(normalize_name)
        subset = logs[(logs["_norm"] == target) | (logs["_norm2"] == target)]
    else:
        subset = logs[logs["_norm"] == target]
    
    return subset

def format_finish(row):
    fin = str(row.get("fin_text", "")).upper()
    if "CUT" in fin:
        return "CUT"
    pos = row.get("pos") or row.get("POS") or ""
    sg = row.get("sg_total")
    if pd.isna(sg):
        return f"{pos}"
    return f"{pos} ({float(sg):.2f})"

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

    print("Loading historical logs...")
    logs = load_all_logs()
    
    # Sort logs by date descending
    date_col = "date_start" if "date_start" in logs.columns else "Date"
    logs[date_col] = pd.to_datetime(logs[date_col], errors="coerce")
    logs = logs.sort_values(date_col, ascending=False)

    rows = []
    
    for _, player in field.iterrows():
        name = player.get("name_adjusted") or player.get("player_name")
        history = get_player_history(logs, player.get("player_name"), player.get("name_adjusted"))
        history = history.head(25)  # keep recent starts
        
        last7 = history.head(7)
        
        # RF1 - RF7
        rf = []
        for i in range(7):
            if i < len(last7):
                rf.append(format_finish(last7.iloc[i]))
            else:
                rf.append("—")
        
        # Cut streak (full available history)
        cut_streak = calculate_cut_streak(history)
        
        # Average finishes (made cuts only)
        finishes = []
        sgs = []
        for _, row in history.iterrows():
            fin = str(row.get("fin_text", "")).upper()
            if "CUT" not in fin:
                pos = row.get("pos") or row.get("POS")
                try:
                    finishes.append(float(pos))
                except:
                    pass
            if pd.notna(row.get("sg_total")):
                sgs.append(float(row["sg_total"]))
        
        avg5 = round(np.mean(finishes[:5]), 1) if len(finishes) >= 5 else None
        avg10 = round(np.mean(finishes[:10]), 1) if len(finishes) >= 10 else (round(np.mean(finishes), 1) if finishes else None)
        
        # Weighted Value (SG last 7)
        weights = [3.2, 2.6, 2.1, 1.6, 1.3, 1.0, 0.8]
        weighted_sum = 0
        weight_total = 0
        for i, row in enumerate(last7.itertuples()):
            sg = getattr(row, "sg_total", None)
            if pd.notna(sg):
                weighted_sum += float(sg) * weights[i]
                weight_total += weights[i]
        
        value = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0
        
        # Cut %
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
    
    # Ranks
    df["value_rank"] = df["value"].rank(ascending=False, method="min").astype("Int64")
    df["rflst5_rank"] = df["rflst5"].rank(ascending=True, method="min").astype("Int64")
    df["rflst10_rank"] = df["rflst10"].rank(ascending=True, method="min").astype("Int64")
    
    # Sort by value rank
    df = df.sort_values("value_rank")
    
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"Wrote {len(df)} players to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
