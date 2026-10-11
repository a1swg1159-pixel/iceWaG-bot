"""Refresh the bundled, public TAKUMI title-name/color definitions."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.takumi_collections import ACHIEVEMENTS_PATH, load_achievements


if __name__ == "__main__":
    catalog = load_achievements(allow_network=True)
    if not catalog:
        raise SystemExit("No valid title metadata was retrieved")
    ACHIEVEMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACHIEVEMENTS_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bundled {len(catalog)} public title definitions; no player data included.")
