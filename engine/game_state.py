"""GameState — the single source of truth for the entire game.

Design principles
-----------------
* **No side effects on construction** — the caller builds the grid and passes
  it in; GameState never loads files or touches I/O itself.
* **Serialisable** — ``to_dict()`` / ``to_json()`` emit pure JSON-safe types
  so the same snapshot can be sent over a WebSocket in the future web version.
* **Single Responsibility** — GameState tracks *what* is true (score, lives,
  entities, grid state). It does *not* decide *how* things move; that belongs
  to the entity-update / physics subsystem (Step 2+).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from config.settings import GameSettings, DEFAULT_SETTINGS
from engine.constants import Cell
from engine.grid import Grid
from entities.entity import Direction, Entity, Position
from entities.ghost import Ghost, GhostState
from entities.pacman import PacMan

#: Classic Level-1 wave schedule: (mode, duration_seconds).
#  The last entry has duration ``None`` — it lasts forever.
_WAVE_SCHEDULE: list[tuple[str, float | None]] = [
    ("SCATTER", 7.0),
    ("CHASE", 20.0),
    ("SCATTER", 7.0),
    ("CHASE", 20.0),
    ("SCATTER", 5.0),
    ("CHASE", 20.0),
    ("SCATTER", 5.0),
    ("CHASE", None),  # permanent chase
]


@dataclass
class GameState:
    """Authoritative snapshot of a running Pac-Man game.

    All fields are plain Python primitives or objects that know how to
    serialise themselves, so ``to_dict()`` is a trivial recursive call.

    Args:
        grid:      The active :class:`~engine.grid.Grid` for this level.
        settings:  Tuning constants; defaults to :data:`~config.settings.DEFAULT_SETTINGS`.
        score:     Accumulated player score.
        lives:     Remaining lives (game over when 0).
        level:     Current level number (1-indexed).
        is_active: ``False`` once the game has ended (all lives lost or won).
        entities:  Dict of ``entity_id → Entity`` for every actor on board.
    """

    grid: Grid
    settings: GameSettings = field(default_factory=lambda: DEFAULT_SETTINGS)
    score: int = 0
    lives: int = 3
    level: int = 1
    is_active: bool = True
    entities: dict[str, Entity] = field(default_factory=dict)

    #: Global ghost-behaviour wave index into :data:`_WAVE_SCHEDULE`.
    wave_index: int = 0
    #: Seconds elapsed in the current wave phase.
    wave_timer: float = 0.0

    #: Consecutive-ghost-eat multiplier during one power-pellet activation.
    ghost_eat_streak: int = 0

    #: Spawn coordinates used by :meth:`reset_after_death`.
    pacman_spawn: tuple[int, int] = (23, 13)
    ghost_names: tuple[str, ...] = ("blinky", "pinky", "inky", "clyde")
    ghost_spawns: tuple[tuple[int, int], ...] = (
        (13, 13),
        (14, 11),
        (14, 13),
        (14, 15),
    )

    # ------------------------------------------------------------------
    # Derived state (computed, never stored redundantly)
    # ------------------------------------------------------------------

    @property
    def current_wave_mode(self) -> str:
        """Return ``'SCATTER'`` or ``'CHASE'`` for the active wave phase."""
        idx = min(self.wave_index, len(_WAVE_SCHEDULE) - 1)
        return _WAVE_SCHEDULE[idx][0]

    @property
    def pellets_remaining(self) -> int:
        """Number of collectible tiles still on the grid."""
        return self.grid.count_pellets()

    @property
    def is_level_complete(self) -> bool:
        """``True`` when every pellet and power-pellet has been consumed."""
        return self.pellets_remaining == 0

    @property
    def is_game_over(self) -> bool:
        """``True`` when the player has no lives left."""
        return self.lives <= 0

    # ------------------------------------------------------------------
    # Mutations — thin wrappers that keep all scoring logic centralised
    # ------------------------------------------------------------------

    def collect_at(self, row: int, col: int) -> int:
        """Attempt to collect whatever is at ``(row, col)``.

        Args:
            row: Grid row of the Pac-Man's current tile.
            col: Grid col of the Pac-Man's current tile.

        Returns:
            Points awarded (0 if the tile held nothing collectable).
        """
        consumed = self.grid.consume_pellet(row, col)
        if consumed is None:
            return 0
        points = (
            self.settings.power_pellet_score
            if consumed == Cell.POWER_PELLET
            else self.settings.pellet_score
        )
        self.score += points
        return points

    def lose_life(self) -> None:
        """Decrement lives by one and mark game over when exhausted."""
        self.lives = max(0, self.lives - 1)
        if self.is_game_over:
            self.is_active = False

    def reset_after_death(self) -> None:
        """Reset all entity positions and ghost states after Pac-Man loses a life.

        Called when ``lives > 0`` to prepare for the next attempt without
        rebuilding the entire state (pellets already collected stay gone).
        """
        pacman = self.entities.get("pacman")
        if isinstance(pacman, PacMan):
            pacman.position = Position(
                row=float(self.pacman_spawn[0]),
                col=float(self.pacman_spawn[1]),
            )
            pacman.direction = Direction.NONE
            pacman.intended_direction = Direction.NONE
            pacman.is_powered_up = False
            pacman.power_timer = 0.0

        for name, (sr, sc) in zip(self.ghost_names, self.ghost_spawns):
            ghost = self.entities.get(f"ghost_{name}")
            if isinstance(ghost, Ghost):
                ghost.position = Position(row=float(sr), col=float(sc))
                ghost.direction = Direction.NONE
                ghost.state = GhostState.SCATTER
                ghost.frightened_timer = 0.0
                ghost.last_intersection_tile = (-1, -1)

        self.wave_index = 0
        self.wave_timer = 0.0
        self.ghost_eat_streak = 0

    def register_entity(self, entity: Entity) -> None:
        """Add or replace an entity in the state dict.

        Args:
            entity: The :class:`~entities.entity.Entity` to register.
        """
        self.entities[entity.id] = entity

    def remove_entity(self, entity_id: str) -> None:
        """Remove an entity by id (e.g. ghost eaten).

        Args:
            entity_id: The ``id`` of the entity to remove.
        """
        self.entities.pop(entity_id, None)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a fully JSON-safe snapshot of the game state.

        This is the payload that will eventually be broadcast to all clients
        over a WebSocket after every game tick.
        """
        return {
            "score": self.score,
            "lives": self.lives,
            "level": self.level,
            "is_active": self.is_active,
            "is_level_complete": self.is_level_complete,
            "is_game_over": self.is_game_over,
            "pellets_remaining": self.pellets_remaining,
            "grid": self.grid.to_dict(),
            "entities": {eid: e.to_dict() for eid, e in self.entities.items()},
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise game state to a JSON string.

        Args:
            indent: Indentation level for pretty-printing (default 2).
        """
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GameState":
        """Reconstruct a :class:`GameState` from a :meth:`to_dict` snapshot.

        This now fully reconstructs entities (PacMan and Ghost instances)
        so that a networked client can render the received state correctly.

        Args:
            payload: Dict produced by :meth:`to_dict`.
        """
        state = cls(
            grid=Grid.from_dict(payload["grid"]),
            score=payload["score"],
            lives=payload["lives"],
            level=payload["level"],
            is_active=payload["is_active"],
        )

        # Reconstruct entities from their serialised dicts.
        for eid, edata in payload.get("entities", {}).items():
            if eid == "pacman":
                state.register_entity(PacMan.from_dict(edata))
            elif eid.startswith("ghost_"):
                state.register_entity(Ghost.from_dict(edata))

        return state
