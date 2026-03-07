"""Base entity data structures shared across all game participants.

``Position`` and ``Entity`` are intentionally pure data — no rendering logic,
no Pygame imports.  The UI layer wraps / reads these to decide what to draw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class Direction(Enum):
    """Cardinal movement directions understood by the engine."""

    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    NONE = auto()


@dataclass
class Position:
    """A floating-point coordinate in grid-space.

    Using floats (not ints) allows smooth sub-cell interpolation for
    movement without losing the grid's integer tile reference later.

    Args:
        row: Vertical position (0 = top of the maze).
        col: Horizontal position (0 = left of the maze).
    """

    row: float
    col: float

    def to_dict(self) -> dict[str, float]:
        """Return a JSON-safe dict."""
        return {"row": self.row, "col": self.col}

    @classmethod
    def from_dict(cls, payload: dict[str, float]) -> "Position":
        """Reconstruct from :meth:`to_dict` output."""
        return cls(row=payload["row"], col=payload["col"])

    def tile(self) -> tuple[int, int]:
        """Return the integer ``(row, col)`` of the tile being occupied."""
        return int(self.row), int(self.col)


@dataclass
class Entity:
    """Minimal base for every actor on the board (Pac-Man, ghosts, …).

    Each entity has a unique string ``id`` so game state dicts can be keyed
    by it and sent verbatim over a WebSocket without extra mapping steps.

    Sub-class this for Pac-Man and each ghost type — add speed, state
    machine, AI strategy, etc. there, not here.

    Args:
        id: Unique identifier string (e.g. ``"pacman"``, ``"ghost_blinky"``).
        position: Current :class:`Position` in grid-space.
        direction: Current movement direction.
    """

    id: str
    position: Position
    direction: Direction = field(default=Direction.NONE)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of this entity."""
        return {
            "id": self.id,
            "position": self.position.to_dict(),
            "direction": self.direction.name,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Entity":
        """Reconstruct from :meth:`to_dict` output."""
        return cls(
            id=payload["id"],
            position=Position.from_dict(payload["position"]),
            direction=Direction[payload["direction"]],
        )
