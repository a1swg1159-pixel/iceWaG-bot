"""Read-only profile-schema diagnostic; never prints credentials or raw saves.

Use an existing explicitly selected bot binding. No account linking, equipment
changes, or local account-data snapshots are performed.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("takumi_profile_inspect_core", ROOT / "src/plugins/takumi_b40/core.py")
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


def shape(value, depth=0):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return f"text[{len(value)}]"
        if parsed != value:
            return shape(parsed, depth)
    if isinstance(value, dict):
        return {key: shape(item, depth + 1) if depth < 2 else type(item).__name__
                for key, item in list(value.items())[:60]}
    if isinstance(value, list):
        return {"items": len(value), "first_shape": shape(value[0], depth + 1) if value and depth < 2 else None}
    return type(value).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq", required=True, help="Select one existing binding; not printed")
    parser.add_argument("--equipment", action="store_true", help="Print only equipped collectible IDs")
    parser.add_argument("--title-schema", action="store_true", help="Inspect public title-metadata shapes")
    args = parser.parse_args()
    with (ROOT / "data/takumi_bindings.json").open(encoding="utf-8") as stream:
        selected = json.load(stream).get(args.qq)
    if not isinstance(selected, dict) or not selected.get("custom_id"):
        raise SystemExit("Selected account has no usable local binding.")
    try:
        session = core.login_with_custom_id(selected["custom_id"])
        payload = core._playfab_call("GetUserData", {"Keys": ["0000_PlayerData"]} if args.equipment else {}, session.session_ticket)
        data = payload.get("Data", {})
    except core.TakumiError as exc:
        raise SystemExit(type(exc).__name__) from None
    for key, record in sorted(data.items()):
        print(json.dumps({"key": key, "shape": shape(record.get("Value"))}, ensure_ascii=False))
    if args.equipment:
        player = json.loads(data.get("0000_PlayerData", {}).get("Value", "{}"))
        print(json.dumps({key: player.get(key) for key in ("IconID", "AchievementID", "DanIconID")}, ensure_ascii=False))
    if args.title_schema:
        metadata = core._playfab_call("GetTitleData", {}, session.session_ticket).get("Data", {})
        for key, value in sorted(metadata.items()):
            print(json.dumps({"title_key": key, "shape": shape(value)}, ensure_ascii=False))
    print("Read-only schema inspection complete. No values or credentials saved.")


if __name__ == "__main__":
    main()
