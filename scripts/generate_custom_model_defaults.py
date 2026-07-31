#!/usr/bin/env python3
"""Write default Custom Model preset for current course."""
from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main():
    course = "Current Course"
    for f in ("cheat_sheet.csv", "recent_form.csv"):
        p = DATA / f
        if p.exists():
            df = pd.read_csv(p, nrows=5)
            cols = {c.lower(): c for c in df.columns}
            if "course_name" in cols:
                course = str(df[cols["course_name"]].dropna().iloc[0])
                break
    preset = {
        "name": "Default",
        "mode": "sg",
        "course_name": course,
        "metrics": [
            {"group": "recent_form", "stat": "sg_total", "window": "last_7_starts"},
            {"group": "course_history", "stat": "sg_total", "window": "all"},
            {"group": "specialist", "stat": "sg_total", "window": "course"},
        ],
        "min_rounds": 4,
    }
    out = DATA / "custom_model_defaults.json"
    out.write_text(json.dumps(preset, indent=2))
    print("Wrote", out)


if __name__ == "__main__":
    main()
