"""Ghost AI — Arcade-authentic target-vector steering.

Algorithm
---------
Original 1980 Pac-Man ghosts do NOT use pathfinding.  At each tile-center
intersection they evaluate the (up to 3) non-wall, non-reverse neighbours and
pick the one whose tile center is *closest* to their current target, with
tie-breaking priority UP > LEFT > DOWN > RIGHT.

Because the scatter targets are placed *outside* the grid (beyond the corner
walls), the ghost can never actually arrive at its target and will orbit the
corner island indefinitely — exactly the classic behaviour.

Architecture
------------
* Pure engine module — zero Pygame imports.
* ``update_ghosts(state)`` is the single public entry-point called once per
  frame by the game loop *before* ``physics.update``.
* All steering decisions are written to ``ghost.direction``; actual movement
  is performed by the physics engine.

Ghost-house exit convention
---------------------------
Ghosts inside the house navigate toward ``_EXIT_WAYPOINT`` (one tile above
the DOOR) using the same target-vector pick, which naturally drives them out
through the gate.  Once clear they follow their behaviour state.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Final

from entities.entity import Direction, Position
from entities.ghost import Ghost, GhostState

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.grid import Grid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Tolerance (tiles) for deciding an entity is "at" a tile center.
_CENTER_THRESHOLD: Final[float] = 0.18

#: Ghost-house bounding box (inclusive rows and cols).
_HOUSE_ROW_MIN: Final[int] = 13
_HOUSE_ROW_MAX: Final[int] = 15
_HOUSE_COL_MIN: Final[int] = 11
_HOUSE_COL_MAX: Final[int] = 16

#: Waypoint just outside the ghost-house door that ghosts navigate toward when
#: exiting.  This is one tile above the DOOR cells (row 12, cols 13-14).
_EXIT_WAYPOINT: Final[tuple[int, int]] = (11, 13)

#: Ghost-house centre tile — DEAD ghosts return here.
_HOUSE_CENTRE: Final[tuple[int, int]] = (14, 13)

#: Scatter targets intentionally placed *outside* the grid bounds.
#  The ghost can never reach these, so it forever chases the target vector
#  and naturally orbits the nearest corner island — classic arcade behaviour.
_SCATTER_TARGETS: Final[dict[str, tuple[float, float]]] = {
    "blinky": (-3.0, 27.0),  # above top-right
    "pinky": (-3.0, -3.0),  # above top-left
    "inky": (33.0, 27.0),  # below bottom-right
    "clyde": (33.0, -3.0),  # below bottom-left
}

#: Direction → (Δrow, Δcol) movement vector.
_DIR_VECTOR: Final[dict[Direction, tuple[int, int]]] = {
    Direction.UP: (-1, 0),
    Direction.DOWN: (1, 0),
    Direction.LEFT: (0, -1),
    Direction.RIGHT: (0, 1),
}

#: Opposite direction lookup (ghosts must not reverse into themselves).
_OPPOSITE: Final[dict[Direction, Direction]] = {
    Direction.UP: Direction.DOWN,
    Direction.DOWN: Direction.UP,
    Direction.LEFT: Direction.RIGHT,
    Direction.RIGHT: Direction.LEFT,
    Direction.NONE: Direction.NONE,
}

#: Tie-break preference order — matches the 1980 arcade ROM.
_TIE_BREAK: Final[tuple[Direction, ...]] = (
    Direction.UP,
    Direction.LEFT,
    Direction.DOWN,
    Direction.RIGHT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nearest_tile(value: float) -> int:
    """Round *value* to the nearest integer tile center (round-half-up)."""
    return math.floor(value + 0.5)


def _at_tile_center(pos: Position) -> bool:
    """Return ``True`` when the entity is close enough to a tile center to turn."""
    return (
        abs(pos.row - round(pos.row)) < _CENTER_THRESHOLD
        and abs(pos.col - round(pos.col)) < _CENTER_THRESHOLD
    )


def _in_ghost_house(tile_r: int, tile_c: int) -> bool:
    """Return ``True`` when the tile is inside the ghost-house area."""
    return (
        _HOUSE_ROW_MIN <= tile_r <= _HOUSE_ROW_MAX
        and _HOUSE_COL_MIN <= tile_c <= _HOUSE_COL_MAX
    )


def _dist(ar: float, ac: float, br: float, bc: float) -> float:
    """Euclidean distance between two points in grid-space."""
    return math.sqrt((ar - br) ** 2 + (ac - bc) ** 2)


# ---------------------------------------------------------------------------
# Core: target-vector direction picker
# ---------------------------------------------------------------------------


def _get_next_arcade_direction(
    ghost: Ghost,
    grid: "Grid",
    target: tuple[float, float],
    *,
    can_use_door: bool = True,
) -> Direction:
    """Return the best direction toward *target* using the arcade rule-set.

    Algorithm (authentic to the 1980 Namco hardware):
    1. Consider all four cardinal neighbours of the ghost's current tile.
    2. Discard any neighbour that is a wall (or DOOR when ``can_use_door`` is
       ``False``).
    3. Discard the direction that would be a direct 180° reversal.
    4. Among the remaining candidates, compute the straight-line distance from
       each neighbour's tile center to *target*.
    5. Return the direction with the shortest distance.  Ties are broken with
       the fixed priority: UP > LEFT > DOWN > RIGHT.

    If all neighbours are blocked/reversed, returns Direction.NONE.

    Args:
        ghost:        The ghost being steered (reads ``position``, ``direction``).
        grid:         The maze for passability queries.
        target:       ``(row, col)`` of the desired destination — may be
                      outside grid bounds for scatter mode.
        can_use_door: When ``True`` (default) the ghost may pass through the
                      DOOR tile (ghost-house gate).  Pass ``False`` to block.
    """
    tile_r = _nearest_tile(ghost.position.row)
    tile_c = _nearest_tile(ghost.position.col)
    opposite = _OPPOSITE[ghost.direction]
    tgt_r, tgt_c = target

    best_dir: Direction = Direction.NONE
    best_dist: float = math.inf

    for d in _TIE_BREAK:  # UP, LEFT, DOWN, RIGHT — tie-break order
        if d == opposite:
            continue  # no U-turns

        dr, dc = _DIR_VECTOR[d]
        nr, nc = tile_r + dr, tile_c + dc

        if not grid.in_bounds(nr, nc):
            continue

        passable = (
            grid.is_passable_for_ghost(nr, nc)
            if can_use_door
            else not grid.is_blocked_for_pacman(nr, nc)
        )
        if not passable:
            continue

        d_dist = _dist(float(nr), float(nc), tgt_r, tgt_c)
        if d_dist < best_dist:
            best_dist = d_dist
            best_dir = d

    return best_dir


# ---------------------------------------------------------------------------
# Target-tile selection (per-ghost personality)
# ---------------------------------------------------------------------------


def _get_target_tile(ghost: Ghost, state: "GameState") -> tuple[float, float]:
    """Return the target tile ``(row, col)`` for *ghost* given the current state.

    The returned coordinate may lie *outside* the grid (scatter targets do).
    FRIGHTENED ghosts should never reach this function — the caller randomises
    their direction instead.

    Personality rules (CHASE mode)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    * **Blinky (red)**: targets Pac-Man's exact tile.
    * **Pinky (pink)**: targets 4 tiles ahead of Pac-Man in Pac-Man's
      current direction.
    * **Inky (cyan)**: takes the point 2 tiles ahead of Pac-Man, then
      doubles the vector from Blinky's position to that point.  Falls back
      to the 2-ahead point when Blinky cannot be found.
    * **Clyde (orange)**: targets Pac-Man when farther than 8 tiles away;
      otherwise retreats to his own scatter corner ``(33, -3)``.
    """
    tile_r = _nearest_tile(ghost.position.row)
    tile_c = _nearest_tile(ghost.position.col)

    # -- DEAD: return to ghost-house centre ----------------------------------
    if ghost.state == GhostState.DEAD:
        return (float(_HOUSE_CENTRE[0]), float(_HOUSE_CENTRE[1]))

    # -- Inside ghost house: head for the exit waypoint ----------------------
    if _in_ghost_house(tile_r, tile_c):
        return (float(_EXIT_WAYPOINT[0]), float(_EXIT_WAYPOINT[1]))

    # -- SCATTER: assigned out-of-bounds corner ------------------------------
    if ghost.state == GhostState.SCATTER:
        name = ghost.id.replace("ghost_", "")
        return _SCATTER_TARGETS.get(name, (-3.0, -3.0))

    # -- CHASE: personality-based targeting -----------------------------------
    pacman = state.entities.get("pacman")
    if pacman is None:
        return (float(_HOUSE_CENTRE[0]), float(_HOUSE_CENTRE[1]))

    pac_r = pacman.position.row
    pac_c = pacman.position.col
    name = ghost.id.replace("ghost_", "")

    if name == "blinky":
        # Red — targets Pac-Man's exact position.
        return (pac_r, pac_c)

    if name == "pinky":
        # Pink — targets 4 tiles ahead of Pac-Man's facing direction.
        dr, dc = _DIR_VECTOR.get(pacman.direction, (0, 0))
        return (pac_r + dr * 4, pac_c + dc * 4)

    if name == "inky":
        # Cyan — 2 tiles ahead of Pac-Man, then double vector from Blinky.
        dr, dc = _DIR_VECTOR.get(pacman.direction, (0, 0))
        pivot_r = pac_r + dr * 2
        pivot_c = pac_c + dc * 2
        blinky = state.entities.get("ghost_blinky")
        if blinky is not None:
            return (
                pivot_r + (pivot_r - blinky.position.row),
                pivot_c + (pivot_c - blinky.position.col),
            )
        return (pivot_r, pivot_c)

    if name == "clyde":
        # Orange — targets Pac-Man only when > 8 tiles away; else scatter.
        dist_to_pac = _dist(ghost.position.row, ghost.position.col, pac_r, pac_c)
        if dist_to_pac > 8.0:
            return (pac_r, pac_c)
        return _SCATTER_TARGETS.get("clyde", (33.0, -3.0))

    # Fallback for any unknown ghost name — chase Pac-Man directly.
    return (pac_r, pac_c)


# ---------------------------------------------------------------------------
# Frightened random steering
# ---------------------------------------------------------------------------


def _random_direction(ghost: Ghost, grid: "Grid") -> None:
    """Pick a random valid direction for a FRIGHTENED ghost.

    Prefers any non-reverse direction; falls back to reverse only if
    genuinely trapped.

    Args:
        ghost: The ghost to steer.
        grid:  The maze for passability queries.
    """
    tile_r = _nearest_tile(ghost.position.row)
    tile_c = _nearest_tile(ghost.position.col)
    opposite = _OPPOSITE[ghost.direction]

    preferred: list[Direction] = []
    fallback: list[Direction] = []

    for d, (dr, dc) in _DIR_VECTOR.items():
        nr, nc = tile_r + dr, tile_c + dc
        if not grid.in_bounds(nr, nc) or not grid.is_passable_for_ghost(nr, nc):
            continue
        if d == opposite:
            fallback.append(d)
        else:
            preferred.append(d)

    choices = preferred if preferred else fallback
    if choices:
        ghost.direction = random.choice(choices)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Wave timer (global scatter / chase phase cycling)
# ---------------------------------------------------------------------------

#: Classic Level-1 wave schedule (mirrored from game_state for reference).
_WAVE_SCHEDULE: Final[list[tuple[str, float | None]]] = [
    ("SCATTER", 7.0),
    ("CHASE", 20.0),
    ("SCATTER", 7.0),
    ("CHASE", 20.0),
    ("SCATTER", 5.0),
    ("CHASE", 20.0),
    ("SCATTER", 5.0),
    ("CHASE", None),  # permanent chase
]


def tick_wave_timer(state: "GameState", dt: float) -> None:
    """Advance the global scatter/chase wave timer by *dt* seconds.

    When a phase expires the wave index advances, and all non-FRIGHTENED,
    non-DEAD ghosts are bulk-switched to the new mode.  Ghosts also receive
    a ``last_intersection_tile`` reset so they can immediately re-evaluate
    at their current tile (the arcade forces an instant reversal on mode
    switch).

    Args:
        state: The live, mutable game state.
        dt:    Seconds elapsed since the previous tick.
    """
    idx = min(state.wave_index, len(_WAVE_SCHEDULE) - 1)
    _, duration = _WAVE_SCHEDULE[idx]

    if duration is None:
        return  # permanent phase — nothing to tick

    state.wave_timer += dt
    if state.wave_timer < duration:
        return

    # Phase expired — advance to the next wave.
    state.wave_timer = 0.0
    state.wave_index = min(state.wave_index + 1, len(_WAVE_SCHEDULE) - 1)
    new_mode = _WAVE_SCHEDULE[state.wave_index][0]

    for entity in state.entities.values():
        if not isinstance(entity, Ghost):
            continue
        if entity.state in (GhostState.FRIGHTENED, GhostState.DEAD):
            continue
        entity.state = GhostState.CHASE if new_mode == "CHASE" else GhostState.SCATTER
        # Force an immediate reversal (arcade behaviour on wave switch)
        entity.direction = _OPPOSITE[entity.direction]
        entity.last_intersection_tile = (-1, -1)


# ---------------------------------------------------------------------------
# Per-frame ghost update
# ---------------------------------------------------------------------------


def update_ghosts(state: "GameState") -> None:
    """Set the direction for every ghost based on its behaviour state.

    Called once per frame *before* ``physics.update``.  Direction changes are
    committed only when the ghost is at a tile center (within
    :data:`_CENTER_THRESHOLD`) **and** the ghost has not already made a
    decision at this exact tile (intersection lock).

    Behaviour rules
    ~~~~~~~~~~~~~~~
    * **Inside ghost house**: target the exit waypoint via
      :func:`_get_target_tile`.
    * **SCATTER**: target the assigned (out-of-bounds) corner.
    * **CHASE**: each ghost applies its unique personality
      (Blinky=exact, Pinky=4-ahead, Inky=Blinky-vector, Clyde=distance-gated).
    * **FRIGHTENED**: random valid direction (no reverse) — once per tile.
    * **DEAD**: target the ghost-house centre to respawn.

    Args:
        state: The live, mutable game state.
    """
    for entity in state.entities.values():
        if not isinstance(entity, Ghost):
            continue

        # Human-controlled ghosts receive direction from network inputs;
        # the AI must never overwrite their direction.
        if entity.is_human_controlled:
            continue

        ghost = entity
        pos = ghost.position

        # Only reroute at tile centers.
        if not _at_tile_center(pos):
            continue

        tile_r = _nearest_tile(pos.row)
        tile_c = _nearest_tile(pos.col)
        current_tile = (tile_r, tile_c)

        # ── Intersection lock: one decision per tile ─────────────────────────
        if current_tile == ghost.last_intersection_tile:
            continue

        # ── FRIGHTENED: randomise ────────────────────────────────────────────
        if ghost.state == GhostState.FRIGHTENED:
            _random_direction(ghost, state.grid)
            ghost.last_intersection_tile = current_tile
            continue

        # ── Personality-aware target selection ────────────────────────────────
        target = _get_target_tile(ghost, state)

        # ── Target-vector pick ───────────────────────────────────────────────
        next_dir = _get_next_arcade_direction(ghost, state.grid, target)
        if next_dir != Direction.NONE:
            ghost.direction = next_dir
        # If Direction.NONE (completely boxed in — shouldn't happen on the
        # classic map), leave current direction unchanged.

        ghost.last_intersection_tile = current_tile


def switch_ghosts_to_chase(state: "GameState") -> None:
    """Transition all non-frightened, non-dead ghosts from SCATTER to CHASE.

    Also resets the intersection lock so each ghost can immediately pick a
    new direction at its current tile (mirrors the arcade instant-reversal
    on mode switch).

    Args:
        state: The live game state.
    """
    for entity in state.entities.values():
        if isinstance(entity, Ghost):
            if entity.state == GhostState.SCATTER:
                entity.state = GhostState.CHASE
                entity.direction = _OPPOSITE[entity.direction]
                entity.last_intersection_tile = (-1, -1)


def switch_ghosts_to_scatter(state: "GameState") -> None:
    """Transition all non-frightened, non-dead ghosts from CHASE to SCATTER.

    Also resets the intersection lock so each ghost can immediately pick a
    new direction at its current tile.

    Args:
        state: The live game state.
    """
    for entity in state.entities.values():
        if isinstance(entity, Ghost):
            if entity.state == GhostState.CHASE:
                entity.state = GhostState.SCATTER
                entity.direction = _OPPOSITE[entity.direction]
                entity.last_intersection_tile = (-1, -1)
