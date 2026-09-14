"""Select images exclusively from the bot owner's local Ongeki comic folder."""

import random
from pathlib import Path
from typing import Callable, Optional, Sequence


LOCAL_COMIC_DIR = Path("data") / "ongeki_official"
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def list_local_comics(directory: Path = LOCAL_COMIC_DIR) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def choose_local_comic(
    directory: Path = LOCAL_COMIC_DIR,
    chooser: Callable[[Sequence[Path]], Path] = random.choice,
) -> Optional[Path]:
    comics = list_local_comics(directory)
    return chooser(comics) if comics else None
