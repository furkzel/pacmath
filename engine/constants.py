"""Grid cell type definitions.

Using IntEnum so values remain JSON-serializable integers while
still being self-documenting throughout the engine code.
"""

from enum import IntEnum


class Cell(IntEnum):
    """Represents the possible states of a single grid tile."""

    WALL = 0
    PATH = 1  # empty walkable tile
    PELLET = 2  # standard dot — 10 pts
    POWER_PELLET = 3  # energizer — 50 pts, enables ghost-eating
    DOOR = 4  # ghost-house gate — ghosts pass through, Pac-Man blocked
