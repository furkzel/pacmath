"""Static, immutable game configuration.

A frozen dataclass rather than a plain module-level dict so that:
* IDE autocomplete works on all fields.
* Accidental mutation is a runtime error (``frozen=True``).
* A future config loader only needs to call ``GameSettings(**json_data)``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# PyInstaller-safe path resolution
# ---------------------------------------------------------------------------


def resource_path(relative: str = "") -> Path:
    """Return an absolute path to *relative*, relative to the project root.

    When the application is bundled with **PyInstaller** (``--onefile`` /
    ``--add-data``), assets are extracted into a temporary directory exposed
    via ``sys._MEIPASS``.  In a normal interpreter session the project root
    is derived from this file's location on disk.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent.parent
    return base / relative if relative else base


# ---------------------------------------------------------------------------
# Project root (resolved once)
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path = resource_path()


# ---------------------------------------------------------------------------
# Map themes
# ---------------------------------------------------------------------------


class MapTheme(Enum):
    """Available map tile-set themes.

    Each member carries two display attributes:
    * ``label`` — human-readable name shown in the menu.
    * ``asset_path`` — resolved ``Path`` to the sprite-sheet PNG, or *None*
      for the procedural "Classic Shapes" fallback.
    """

    CLASSIC = ("Classic Shapes", None)
    GOTHIC = ("Gothic", _PROJECT_ROOT / "assets" / "maps" / "gothic.png")
    GREEK = ("Ancient Greek", _PROJECT_ROOT / "assets" / "maps" / "greek.png")
    MATH = ("Math", _PROJECT_ROOT / "assets" / "maps" / "math.jpg")
    ABSTRACT = ("Abstract Math", None)  # placeholder for future asset

    def __init__(self, label: str, asset_path: Path | None) -> None:
        self.label = label
        self.asset_path = asset_path


#: Ordered list for UI iteration (preserves declaration order).
MAP_THEMES: list[MapTheme] = list(MapTheme)


# ---------------------------------------------------------------------------
# Character themes
# ---------------------------------------------------------------------------


class CharacterTheme(Enum):
    """Character sprite themes.

    * ``label`` — human-readable name shown in the menu.
    * ``asset_path`` — resolved ``Path`` to the character sprite-sheet PNG,
      or *None* for procedural geometric drawing.
    * ``unlocked`` — whether the theme is available for selection.
    """

    CLASSIC = ("Classic Shapes", None, True)
    NEWTON = (
        "Isaac Newton",
        _PROJECT_ROOT / "assets" / "characters" / "sirisaacnewton.png",
        True,
    )
    THALES = (
        "Thales",
        _PROJECT_ROOT / "assets" / "characters" / "thales.png",
        True,
    )
    LEIBNIZ = (
        "Leibniz",
        _PROJECT_ROOT / "assets" / "characters" / "liebniz.png",
        True,
    )
    HYPATIA = (
        "Hypatia",
        _PROJECT_ROOT / "assets" / "characters" / "hypatia.png",
        True,
    )

    def __init__(self, label: str, asset_path: Path | None, unlocked: bool) -> None:
        self.label = label
        self.asset_path = asset_path
        self.unlocked = unlocked


#: Ordered list for UI iteration.
CHARACTER_THEMES: list[CharacterTheme] = list(CharacterTheme)


# ---------------------------------------------------------------------------
# Theme settings (mutable — selected by the menu before the game starts)
# ---------------------------------------------------------------------------


@dataclass
class ThemeSettings:
    """Holds the user's current theme selection.

    This is intentionally **not** frozen — the menu mutates it before the
    game loop reads it.
    """

    map_theme: MapTheme = MapTheme.GOTHIC
    character_theme: CharacterTheme = CharacterTheme.CLASSIC

    @property
    def map_asset_path(self) -> Path | None:
        """Resolved path to the selected map tile-sheet, or *None* for procedural."""
        return self.map_theme.asset_path

    @property
    def character_asset_path(self) -> Path | None:
        """Resolved path to the selected character sprite-sheet, or *None*."""
        return self.character_theme.asset_path


@dataclass(frozen=True)
class GameSettings:
    """Tuning constants consumed by the engine and the UI layer alike.

    The UI layer (Pygame / HTML Canvas) may read ``cell_size`` and ``fps``.
    The engine reads everything else.
    """

    # Display (read by UI only — engine is pixel-agnostic)
    fps: int = 60
    cell_size: int = 32  # pixels per grid cell

    # Movement (grid-cells per second)
    pacman_speed: float = 4.0
    ghost_speed: float = 3.5

    # Scoring
    pellet_score: int = 10
    power_pellet_score: int = 50
    ghost_eaten_score: int = 200  # first ghost; doubles each consecutive

    # Gameplay
    starting_lives: int = 3


#: Default singleton shared across the application.
DEFAULT_SETTINGS = GameSettings()
