"""Refresh the bundled TAKUMI³ song-id catalogue from the game's public sheet."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import requests


SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1cYF7RUax3XuRSgufHG02Fy-GgYzYWjAv/gviz/tq"
    "?tqx=out:csv&sheet=SongsInfo"
)
OUTPUT = Path(__file__).parents[1] / "src" / "plugins" / "takumi_b40" / "song_catalog.json"


def main() -> None:
    response = requests.get(SHEET_URL, timeout=30)
    response.raise_for_status()
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    songs = []
    for row in rows[1:]:
        if len(row) < 19:
            continue
        try:
            song_id = int(row[15])
        except ValueError:
            continue
        title = row[17].strip() or row[0].strip()
        if not title:
            continue
        songs.append(
            {
                "song_id": song_id,
                "internal_name": row[0].strip(),
                "title": title,
                "levels": row[7:11],
                "special": row[11],
                "is_public": row[18].strip().lower() == "true",
            }
        )
    songs.sort(key=lambda item: item["song_id"])
    OUTPUT.write_text(
        json.dumps(songs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(songs)} songs to {OUTPUT}")


if __name__ == "__main__":
    main()
