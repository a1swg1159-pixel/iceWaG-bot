# TAKUMI³ chart constants

`charts.json` is a snapshot of the community-maintained chart table from
[`dora-ryukyu/takumi-score-manager`](https://github.com/dora-ryukyu/takumi-score-manager/blob/3ba484e3360ab1bc074b33731b87cd7282a01e19/src/data/charts.json),
commit `3ba484e3360ab1bc074b33731b87cd7282a01e19`, retrieved 2026-09-10.

The snapshot contains charts with constants of 13.0 and above. The bot labels a
result as partial when fewer than 40 rated charts can be matched.
Two matching aliases are adjusted locally for titles renamed or reformatted in
the current official catalog; rating constants remain unchanged.

`song_catalog.json` maps PlayFab song IDs to titles, difficulties, and visible
levels. It is generated from the game's public `SongsInfo` sheet with
`tools/update_takumi_catalog.py`. At runtime the bot refreshes that sheet and
falls back to the bundled snapshot when Google Sheets is unavailable.

`jacket_atlas.webp` and `jacket_atlas.json` contain a compact song-ID-indexed
atlas generated from the official Android APK by
`tools/extract_takumi_jackets.py`. The original APK is not kept in the project.
