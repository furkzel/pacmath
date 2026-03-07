"""Pac-Man engine prototype — entry point.

Purpose
-------
Step-2 smoke test for the physics and entity systems (zero UI dependencies):

* Build a ``GameState`` with specialised :class:`~entities.pacman.PacMan` and
  :class:`~entities.ghost.Ghost` entities.
* Simulate a short corridor run at ~60 FPS using :func:`~engine.physics.update`.
* Print Pac-Man's changing sub-tile coordinates, score, and power-up status
  after every simulated frame to prove the movement physics work.

Run with:
    python main.py
"""

from __future__ import annotations

import json

from config.settings import DEFAULT_SETTINGS
from engine import physics
from engine.game_state import GameState
from engine.grid import Grid
from entities.entity import Direction, Position
from entities.ghost import Ghost
from entities.pacman import PacMan
from maps.classic import CLASSIC_MAP, GHOST_SPAWNS

# ------------------------------------------------------------------
# Demo route: Pac-Man starts at tile (1, 1) — the top-left power-pellet —
# and heads RIGHT along the top corridor:
#   (1,1) POWER_PELLET  →  (1,2)(1,3)(1,4)(1,5) PELLET  →  WALL at (1,6)
# This exercises: power-pellet collection, pellet collection, ghost
# frightening, and wall-collision clamping — entirely without UI.
# ------------------------------------------------------------------
_DEMO_SPAWN_ROW: int = 1
_DEMO_SPAWN_COL: int = 1
_DEMO_DIRECTION: Direction = Direction.RIGHT
_DEMO_FRAMES: int = 80
_DEMO_DT: float = 1 / 60  # ~16.7 ms per frame → 60 FPS


# --------------------------------------------------------------------------
# State construction
# --------------------------------------------------------------------------


def build_initial_state() -> GameState:
    """Construct a fresh GameState for the Step-2 physics demo.

    Returns:
        A fully initialised :class:`~engine.game_state.GameState`.
    """
    grid = Grid(data=[row[:] for row in CLASSIC_MAP])  # defensive copy
    state = GameState(grid=grid, settings=DEFAULT_SETTINGS)

    pacman = PacMan(
        id="pacman",
        position=Position(row=float(_DEMO_SPAWN_ROW), col=float(_DEMO_SPAWN_COL)),
        direction=_DEMO_DIRECTION,
        speed=DEFAULT_SETTINGS.pacman_speed,
        lives=DEFAULT_SETTINGS.starting_lives,
    )
    state.register_entity(pacman)

    ghost_names = ("blinky", "pinky", "inky", "clyde")
    for name, (spawn_row, spawn_col) in zip(ghost_names, GHOST_SPAWNS):
        ghost = Ghost(
            id=f"ghost_{name}",
            position=Position(row=float(spawn_row), col=float(spawn_col)),
            direction=Direction.NONE,
            speed=DEFAULT_SETTINGS.ghost_speed,
        )
        state.register_entity(ghost)

    return state


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _banner(text: str) -> None:
    """Print a simple framed section banner to stdout."""
    border = "─" * (len(text) + 4)
    print(f"\n┌{border}┐")
    print(f"│  {text}  │")
    print(f"└{border}┘")


def _ghost_summary(state: GameState) -> str:
    """Return a compact ghost-state string for the frame table."""
    parts = []
    for eid, entity in state.entities.items():
        if isinstance(entity, Ghost):
            short = eid.replace("ghost_", "")
            parts.append(f"{short}={entity.state.name[:3]}")
    return "  ".join(parts)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> None:
    """Run the Step-2 physics smoke test."""
    _banner("Pac-Man Engine — Step 2 Physics Smoke Test")

    state = build_initial_state()
    pacman: PacMan = state.entities["pacman"]  # type: ignore[assignment]

    print(
        f"\n  Starting tile  : ({_DEMO_SPAWN_ROW}, {_DEMO_SPAWN_COL})"
        f"  direction={_DEMO_DIRECTION.name}"
    )
    print(f"  Speed          : {pacman.speed} grid-cells/s  dt={_DEMO_DT:.4f}s")
    print(f"  Initial pellets: {state.pellets_remaining}")
    print(f"  Simulating {_DEMO_FRAMES} frames at ~60 FPS\n")

    header = (
        f"{'Frame':>5}  {'col':>8}  {'row':>6}  {'score':>6}  {'powered':>7}  Ghosts"
    )
    print(header)
    print("─" * len(header))

    prev_score = 0

    for frame in range(1, _DEMO_FRAMES + 1):
        physics.update(state, _DEMO_DT)

        score_delta = state.score - prev_score
        prev_score = state.score

        # Print every 4th frame plus any frame where a pellet was collected.
        if frame % 4 == 0 or score_delta > 0:
            powered_marker = "YES" if pacman.is_powered_up else " no"
            ghost_info = _ghost_summary(state)
            annotation = f"  ← +{score_delta} pts" if score_delta > 0 else ""
            print(
                f"{frame:>5}  {pacman.position.col:>8.4f}  {pacman.position.row:>6.3f}"
                f"  {state.score:>6}"
                f"  {powered_marker:>7}"
                f"  {ghost_info}"
                f"{annotation}"
            )

    _banner("Final State Summary")
    print(f"  Score          : {state.score}")
    print(f"  Pellets left   : {state.pellets_remaining}")
    print(
        f"  Pac-Man pos    : row={pacman.position.row:.4f}  col={pacman.position.col:.4f}"
    )
    print(
        f"  Powered up     : {pacman.is_powered_up}  (timer={pacman.power_timer:.2f}s)"
    )
    print(f"  Level complete : {state.is_level_complete}")

    # --- JSON round-trip validation ----------------------------------------
    rehydrated = GameState.from_dict(json.loads(state.to_json()))
    assert rehydrated.score == state.score, "Round-trip score mismatch!"
    assert rehydrated.lives == state.lives, "Round-trip lives mismatch!"
    print("\n  ✓ JSON round-trip check passed.\n")


if __name__ == "__main__":
    main()
