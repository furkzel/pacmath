"""Pac-Man — the player-controlled entity.

Extends the base :class:`~entities.entity.Entity` with player-specific state:
power-up tracking, lives (mirrored from GameState for quick per-entity access
during collision resolution), and speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from entities.entity import Direction, Entity, Position

#: Duration of the FRIGHTENED effect granted by one power-pellet (seconds).
POWER_PELLET_DURATION: float = 7.0


@dataclass
class PacMan(Entity):
    """Player-controlled Pac-Man entity.

    Args:
        id:           Stable identifier — always ``"pacman"``.
        position:     Current floating-point position in grid-space.
        direction:    Current movement direction.
        speed:        Movement speed in grid-cells per second.
        lives:        Remaining lives; game ends when this reaches 0.
        is_powered_up: ``True`` while a power-pellet effect is active.
        power_timer:  Seconds remaining on the current power-pellet effect.
    """

    speed: float = 4.0
    lives: int = 3
    is_powered_up: bool = False
    power_timer: float = 0.0
    #: The direction the player *wants* to go next.  The physics engine
    #: commits to it only when Pac-Man is close enough to a tile center
    #: and the target tile is passable.  This gives butter-smooth cornering.
    intended_direction: Direction = field(default=Direction.NONE)

    # ------------------------------------------------------------------
    # Power-up lifecycle
    # ------------------------------------------------------------------

    def activate_power(self, duration: float = POWER_PELLET_DURATION) -> None:
        """Begin (or refresh) the power-pellet effect.

        Args:
            duration: How many seconds the effect should last.
        """
        self.is_powered_up = True
        self.power_timer = duration

    def tick_power(self, dt: float) -> bool:
        """Decrement the power timer by *dt* seconds.

        Args:
            dt: Delta time in seconds.

        Returns:
            ``True`` if the power-up **expired** this tick, ``False``
            otherwise (including when Pac-Man was not powered-up).
        """
        if not self.is_powered_up:
            return False
        self.power_timer = max(0.0, self.power_timer - dt)
        if self.power_timer == 0.0:
            self.is_powered_up = False
            return True
        return False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot including power-up and intended direction."""
        base = super().to_dict()
        base.update(
            {
                "speed": self.speed,
                "lives": self.lives,
                "is_powered_up": self.is_powered_up,
                "power_timer": round(self.power_timer, 4),
                "intended_direction": self.intended_direction.name,
            }
        )
        return base

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PacMan":
        """Reconstruct a :class:`PacMan` from a :meth:`to_dict` snapshot."""
        return cls(
            id=payload["id"],
            position=Position.from_dict(payload["position"]),
            direction=Direction[payload["direction"]],
            speed=payload.get("speed", 4.0),
            lives=payload.get("lives", 3),
            is_powered_up=payload.get("is_powered_up", False),
            power_timer=payload.get("power_timer", 0.0),
            intended_direction=Direction[payload.get("intended_direction", "NONE")],
        )
