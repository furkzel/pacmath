"""Physics / Movement Engine — pure, stateless update functions.

Responsibilities (Single Responsibility per function)
------------------------------------------------------
* Sub-tile, float-precision movement in grid-space.
* Wall collision detection and position clamping.
* Pellet collection triggered by Pac-Man's center tile.
* Power-up and frightened-state timer ticking.
* Level-complete detection.

Architecture note
-----------------
All public functions accept a :class:`~engine.game_state.GameState` and a
``dt`` (delta time in seconds), mutate the state **in place**, and return
``None``.  There is no Physics *class* — module-level functions are simpler to
test in isolation and avoid hidden state.

The UI calls :func:`update` once per rendered frame; the server will call it
once per game-tick.  Neither knows about the other.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from engine.constants import Cell
from entities.entity import Direction, Entity, Position
from entities.ghost import Ghost, GhostState
from entities.pacman import PacMan

#: Collision threshold in grid-cells.  Two entities closer than this collide.
_COLLISION_RADIUS: float = 0.8

# Tolerance (in grid-cells) within which an entity is considered "at a tile
# center" and eligible to change direction or snap-correct its off-axis position.
_CENTER_THRESHOLD: float = 0.18

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.grid import Grid

# --------------------------------------------------------------------------
# Direction helpers
# --------------------------------------------------------------------------

#: Maps each :class:`~entities.entity.Direction` to a ``(Δrow, Δcol)`` vector.
_DIR_VECTOR: dict[Direction, tuple[int, int]] = {
    Direction.UP: (-1, 0),
    Direction.DOWN: (1, 0),
    Direction.LEFT: (0, -1),
    Direction.RIGHT: (0, 1),
    Direction.NONE: (0, 0),
}


def _nearest_tile(value: float) -> int:
    """Return the tile index whose center is nearest to *value*.

    Tile centers sit at integer coordinates (0, 1, 2, …).  This function
    uses round-half-up (``floor(x + 0.5)``) to avoid Python's default
    banker's rounding producing confusing results at midpoints.

    Args:
        value: A floating-point position on one axis.
    """
    return math.floor(value + 0.5)


# --------------------------------------------------------------------------
# Core movement primitive
# --------------------------------------------------------------------------


def _try_move(
    pos: Position,
    direction: Direction,
    speed: float,
    dt: float,
    grid: "Grid",
    can_use_door: bool = False,
) -> bool:
    """Attempt to advance *pos* in *direction* and clamp against walls.

    Returns:
        ``True`` if the entity moved (or wrapped); ``False`` if blocked by a
        wall, meaning the caller should zero the entity's active direction.
    """
    dr, dc = _DIR_VECTOR[direction]
    if dr == 0 and dc == 0:
        return False

    delta = speed * dt

    # --- 1. Perpendicular snap -------------------------------------------
    if dc != 0:  # horizontal movement → snap row to lane center
        pos.row = float(_nearest_tile(pos.row))
    else:  # vertical movement → snap col to lane center
        pos.col = float(_nearest_tile(pos.col))

    # --- Sanitize: clamp the parallel axis so float drift never puts the
    #     entity permanently outside the valid column range.  This is a
    #     guard — it should be a no-op under normal conditions. -----------
    if dc != 0:
        pos.col = max(-0.5, min(float(grid.cols - 1) + 0.49, pos.col))
    else:
        pos.row = max(-0.5, min(float(grid.rows - 1) + 0.49, pos.row))

    # --- 2. Propose move ---------------------------------------------------
    new_row = pos.row + dr * delta
    new_col = pos.col + dc * delta

    # --- Tunnel wrap (horizontal only) ------------------------------------
    # If the move crosses the left or right boundary the entity is using the
    # wrap-around tunnel.  Snap to the tile at the opposite side so physics
    # never sees an out-of-bounds coordinate and the entity appears
    # seamlessly at the tunnel exit on the next rendered frame.
    if dc != 0:
        if new_col < -0.5:
            pos.col = float(grid.cols - 1)  # appear at right edge
            return
        if new_col >= grid.cols - 0.5:
            pos.col = 0.0  # appear at left edge
            return

    # --- 3. Wall / bounds check on proposed tile --------------------------
    new_tile_r = _nearest_tile(new_row)
    new_tile_c = _nearest_tile(new_col)

    if not grid.in_bounds(new_tile_r, new_tile_c):
        return False

    # Ghosts may pass through DOOR tiles; Pac-Man cannot.
    tile_blocked = (
        grid.is_wall(new_tile_r, new_tile_c)
        if can_use_door
        else grid.is_blocked_for_pacman(new_tile_r, new_tile_c)
    )
    if tile_blocked:
        # Snap the travel axis to the exact tile center on wall contact so
        # _steer_pacman's threshold check passes immediately on the next frame.
        if dc != 0:
            pos.col = float(_nearest_tile(pos.col))
        else:
            pos.row = float(_nearest_tile(pos.row))
        return False  # signal: blocked

    # --- 4. Overshoot clamp (max 1 tile per frame) -------------------------
    cur_r = _nearest_tile(pos.row)
    cur_c = _nearest_tile(pos.col)

    if dc > 0:
        new_col = min(new_col, float(cur_c + 1))
    elif dc < 0:
        new_col = max(new_col, float(cur_c - 1))
    elif dr > 0:
        new_row = min(new_row, float(cur_r + 1))
    elif dr < 0:
        new_row = max(new_row, float(cur_r - 1))

    pos.row = new_row
    pos.col = new_col
    return True  # moved successfully


# --------------------------------------------------------------------------
# Per-subsystem update steps (called in order by `update`)
# --------------------------------------------------------------------------


def _steer_pacman(pacman: PacMan, grid: "Grid") -> None:
    """Commit ``intended_direction`` to ``direction`` when it is safe to do so.

    A direction change is committed only when **both** conditions hold:
    1. Pac-Man is within :data:`_CENTER_THRESHOLD` of the current tile center
       on **both** axes (he is effectively "at" the intersection).
    2. The tile one step in ``intended_direction`` from the current tile is
       walkable (not a wall).

    When committed the off-axis position is snapped to the tile center exactly,
    eliminating pixel-level misalignment and enabling pixel-perfect cornering.

    Args:
        pacman: The :class:`~entities.pacman.PacMan` entity to steer.
        grid:   The maze for wall lookups.
    """
    intended = pacman.intended_direction
    if intended == Direction.NONE or intended == pacman.direction:
        return

    pos = pacman.position
    tile_r = _nearest_tile(pos.row)
    tile_c = _nearest_tile(pos.col)

    # 1. Close enough to the tile centre on both axes?
    if abs(pos.row - tile_r) > _CENTER_THRESHOLD:
        return
    if abs(pos.col - tile_c) > _CENTER_THRESHOLD:
        return

    # 2. Target tile in intended direction passable for Pac-Man?
    dr, dc = _DIR_VECTOR[intended]
    nr, nc = tile_r + dr, tile_c + dc
    if not grid.in_bounds(nr, nc) or grid.is_blocked_for_pacman(nr, nc):
        return

    # Both checks pass — commit and snap to centre
    pacman.direction = intended
    pos.row = float(tile_r)
    pos.col = float(tile_c)


def _update_entities(state: "GameState", dt: float) -> None:
    """Steer Pac-Man then move every entity.

    Order: steer → move ensures the new direction is applied this very frame
    rather than lagging one frame behind the keypress.

    Ghosts in FRIGHTENED state move at half their normal speed.
    DEAD ghosts are not yet moved (ghost-house pathfinding is a later step).

    Args:
        state: The authoritative game state.
        dt:    Delta time in seconds.
    """
    for entity in state.entities.values():
        if isinstance(entity, Ghost):
            # DEAD ghosts move at full speed back toward the ghost house
            # (the AI module sets their direction to navigate home).
            effective_speed = (
                entity.speed * 0.5
                if entity.state.name == "FRIGHTENED"
                else entity.speed
            )
            _try_move(
                entity.position,
                entity.direction,
                effective_speed,
                dt,
                state.grid,
                can_use_door=True,
            )
        elif isinstance(entity, PacMan):
            _steer_pacman(entity, state.grid)
            blocked = not _try_move(
                entity.position, entity.direction, entity.speed, dt, state.grid
            )
            # Zero the active direction when Pac-Man walks into a wall.
            # This breaks the snap-push oscillation loop: with direction=NONE
            # the entity is stationary, but intended_direction is preserved so
            # _steer_pacman can immediately commit a valid buffered turn on the
            # very next frame — identical to the arcade's "stop and redirect".
            if blocked:
                entity.direction = Direction.NONE
        else:
            # Generic Entity fallback — used during tests / mocking
            speed = getattr(entity, "speed", 4.0)
            _try_move(entity.position, entity.direction, speed, dt, state.grid)


def _update_pacman_collection(state: "GameState") -> None:
    """Check Pac-Man's current tile and collect any pellet there.

    Side-effects on *state*:
    * ``state.score`` incremented.
    * Consumed tile set to ``Cell.PATH`` inside the grid.
    * Power-pellet activation triggers :meth:`~entities.pacman.PacMan.activate_power`
      on Pac-Man and :meth:`~entities.ghost.Ghost.frighten` on every ghost.

    Args:
        state: The authoritative game state.
    """
    pacman = state.entities.get("pacman")
    if not isinstance(pacman, PacMan):
        return

    tile_r = _nearest_tile(pacman.position.row)
    tile_c = _nearest_tile(pacman.position.col)

    points = state.collect_at(tile_r, tile_c)

    if points == state.settings.power_pellet_score:
        pacman.activate_power()
        for entity in state.entities.values():
            if isinstance(entity, Ghost):
                entity.frighten()


def _update_timers(state: "GameState", dt: float) -> None:
    """Tick power-up and frightened timers for all relevant entities.

    Args:
        state: The authoritative game state.
        dt:    Delta time in seconds.
    """
    pacman = state.entities.get("pacman")
    if isinstance(pacman, PacMan):
        pacman.tick_power(dt)

    for entity in state.entities.values():
        if isinstance(entity, Ghost):
            entity.tick_frighten(dt)


def _check_level_complete(state: "GameState") -> None:
    """Deactivate the game when all pellets have been collected.

    Args:
        state: The authoritative game state.
    """
    if state.is_level_complete:
        state.is_active = False


# --------------------------------------------------------------------------
# Collision detection  (pure engine — no Pygame dependency)
# --------------------------------------------------------------------------


def _entity_distance(a: Position, b: Position) -> float:
    """Euclidean distance between two grid-space positions."""
    return math.sqrt((a.row - b.row) ** 2 + (a.col - b.col) ** 2)


def _handle_collisions(state: "GameState") -> None:
    """Detect and resolve Pac-Man / ghost overlaps after movement.

    Rules (identical to the 1980 arcade):

    * **DEAD ghost** — no collision (the floating eyes are harmless).
    * **FRIGHTENED ghost** — Pac-Man eats the ghost:
      - Ghost becomes DEAD and is teleported to the ghost-house centre.
      - Score awarded = ``ghost_eaten_score * 2^streak``, where *streak* is
        the number of ghosts already eaten during **this** power-pellet.
    * **CHASE / SCATTER ghost** — ghost eats Pac-Man:
      - ``state.lose_life()`` is called.
      - If lives remain, ``state.reset_after_death()`` repositions everyone.

    Args:
        state: The mutable, authoritative game state.
    """
    pacman = state.entities.get("pacman")
    if not isinstance(pacman, PacMan):
        return

    hit_normal = False

    for entity in list(state.entities.values()):
        if not isinstance(entity, Ghost):
            continue
        if entity.state == GhostState.DEAD:
            continue
        if _entity_distance(pacman.position, entity.position) > _COLLISION_RADIUS:
            continue

        if entity.state == GhostState.FRIGHTENED:
            # Pac-Man eats this ghost.
            points = state.settings.ghost_eaten_score * (2**state.ghost_eat_streak)
            state.score += points
            state.ghost_eat_streak += 1
            entity.die()
            # Teleport eyes to ghost-house centre so the DEAD AI takes over.
            entity.position = Position(row=14.0, col=13.0)
        else:
            # Normal ghost kills Pac-Man.
            hit_normal = True

    if hit_normal:
        state.lose_life()
        if state.lives > 0:
            state.reset_after_death()


# --------------------------------------------------------------------------
# Public API — single entry point called by the game loop
# --------------------------------------------------------------------------


def update(state: "GameState", dt: float) -> None:
    """Advance the entire game state by one time step.

    This is the **only** function the game loop (or server tick) needs to
    call.  It orchestrates the four update steps in the correct order:
    move → collect → tick timers → check win condition.

    Args:
        state: The mutable, authoritative :class:`~engine.game_state.GameState`.
        dt:    Delta time in seconds since the last update call
               (e.g. ``1 / 60`` for a 60 FPS loop).
    """
    if not state.is_active:
        return

    _update_entities(state, dt)
    _update_pacman_collection(state)
    _update_timers(state, dt)
    _handle_collisions(state)
    _check_level_complete(state)
