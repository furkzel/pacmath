"""Ghost — an AI-controlled antagonist entity.

Each ghost has a simple four-state machine that controls its behaviour.
The *logic* that drives state transitions lives in the AI subsystem (Step 3+);
this module is purely data — state values and lifecycle helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from entities.entity import Direction, Entity, Position

#: How long a ghost stays FRIGHTENED when a power-pellet is collected.
FRIGHTENED_DURATION: float = 7.0


class GhostState(Enum):
    """Finite states of a single ghost's behaviour machine.

    Transitions (decided by the AI subsystem, not this class):

    .. code-block:: text

        SCATTER ──────────────────────► CHASE
           ▲                              │
           │     (power pellet eaten)     │
           └──── FRIGHTENED ◄─────────────┘
                     │
                     │  (eaten while frightened)
                     ▼
                    DEAD ──► SCATTER  (on return to ghost house)
    """

    CHASE = auto()  # hunting Pac-Man using ghost-specific AI
    SCATTER = auto()  # retreating to a pre-defined corner target
    FRIGHTENED = auto()  # vulnerable; reverses direction, moves slower
    DEAD = auto()  # returning to the ghost house after being eaten


@dataclass
class Ghost(Entity):
    """A ghost entity with a state machine and speed.

    Args:
        id:               Stable identifier, e.g. ``"ghost_blinky"``.
        position:         Floating-point position in grid-space.
        direction:        Current movement direction.
        speed:            Normal movement speed in grid-cells per second.
        state:            Current behaviour state.
        frightened_timer: Seconds remaining in the FRIGHTENED state.
    """

    speed: float = 3.5
    state: GhostState = field(default=GhostState.SCATTER)
    frightened_timer: float = 0.0

    #: When ``True`` this ghost is driven by a human player over the network.
    #: The AI subsystem will skip it entirely; direction comes from inputs.
    is_human_controlled: bool = False

    #: The last tile center where the ghost made an intersection decision.
    #: Used by the AI to guarantee at most ONE direction pick per tile.
    last_intersection_tile: tuple[int, int] = field(default=(-1, -1))

    # ------------------------------------------------------------------
    # State transitions (called by the physics/AI layer, not here)
    # ------------------------------------------------------------------

    def frighten(self, duration: float = FRIGHTENED_DURATION) -> None:
        """Enter the FRIGHTENED state for *duration* seconds.

        Args:
            duration: Seconds to remain frightened.
        """
        self.state = GhostState.FRIGHTENED
        self.frightened_timer = duration

    def tick_frighten(self, dt: float) -> bool:
        """Decrement the frightened timer by *dt* seconds.

        Args:
            dt: Delta time in seconds.

        Returns:
            ``True`` if FRIGHTENED **expired** this tick, ``False``
            otherwise (including when the ghost was not frightened).
        """
        if self.state != GhostState.FRIGHTENED:
            return False
        self.frightened_timer = max(0.0, self.frightened_timer - dt)
        if self.frightened_timer == 0.0:
            self.state = GhostState.SCATTER
            return True
        return False

    def die(self) -> None:
        """Transition to DEAD — ghost is eaten and heads back to the house."""
        self.state = GhostState.DEAD
        self.frightened_timer = 0.0

    def revive(self) -> None:
        """Transition from DEAD back to SCATTER on reaching the ghost house."""
        self.state = GhostState.SCATTER
        self.last_intersection_tile = (-1, -1)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot including ghost state."""
        base = super().to_dict()
        base.update(
            {
                "speed": self.speed,
                "state": self.state.name,
                "frightened_timer": round(self.frightened_timer, 4),
                "is_human_controlled": self.is_human_controlled,
                "last_intersection_tile": list(self.last_intersection_tile),
            }
        )
        return base

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Ghost":
        """Reconstruct a :class:`Ghost` from a :meth:`to_dict` snapshot."""
        return cls(
            id=payload["id"],
            position=Position.from_dict(payload["position"]),
            direction=Direction[payload["direction"]],
            speed=payload.get("speed", 3.5),
            state=GhostState[payload.get("state", "SCATTER")],
            frightened_timer=payload.get("frightened_timer", 0.0),
            is_human_controlled=payload.get("is_human_controlled", False),
            last_intersection_tile=tuple(
                payload.get("last_intersection_tile", (-1, -1))
            ),
        )
