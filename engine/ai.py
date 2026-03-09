"""Ghost AI — Predictive A* Pathfinding & Target-Vector Steering.

Algorithm Update ("Principia of Darkness" Edition)
--------------------------------------------------
Unlike the 1980s arcade ROM which used blind Euclidean distance, this AI 
uses a hybrid approach:
1. SCATTER / DEAD: Uses the classic greedy target-vector steering to naturally 
   orbit out-of-bounds corners without crashing.
2. CHASE: Uses a ruthless A* (A-Star) Pathfinding algorithm. Ghosts understand 
   wall topologies and will plot the absolute shortest viable path to their 
   targets, making them extremely aggressive and coordinated.

Architecture
------------
* Pure engine module — zero Pygame imports. Drop-in replacement.
* Preserves intersection-lock and exact public API.
"""

from __future__ import annotations

import math
import random
import heapq
from typing import TYPE_CHECKING, Final

from entities.entity import Direction, Position
from entities.ghost import Ghost, GhostState

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.grid import Grid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CENTER_THRESHOLD: Final[float] = 0.18

_HOUSE_ROW_MIN: Final[int] = 13
_HOUSE_ROW_MAX: Final[int] = 15
_HOUSE_COL_MIN: Final[int] = 11
_HOUSE_COL_MAX: Final[int] = 16

_EXIT_WAYPOINT: Final[tuple[int, int]] = (11, 13)
_HOUSE_CENTRE: Final[tuple[int, int]] = (14, 13)

_SCATTER_TARGETS: Final[dict[str, tuple[float, float]]] = {
    "blinky": (-3.0, 27.0),  
    "pinky": (-3.0, -3.0),   
    "inky": (33.0, 27.0),    
    "clyde": (33.0, -3.0),   
}

_DIR_VECTOR: Final[dict[Direction, tuple[int, int]]] = {
    Direction.UP: (-1, 0),
    Direction.DOWN: (1, 0),
    Direction.LEFT: (0, -1),
    Direction.RIGHT: (0, 1),
}

_OPPOSITE: Final[dict[Direction, Direction]] = {
    Direction.UP: Direction.DOWN,
    Direction.DOWN: Direction.UP,
    Direction.LEFT: Direction.RIGHT,
    Direction.RIGHT: Direction.LEFT,
    Direction.NONE: Direction.NONE,
}

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
    return math.floor(value + 0.5)

def _at_tile_center(pos: Position) -> bool:
    return (
        abs(pos.row - round(pos.row)) < _CENTER_THRESHOLD
        and abs(pos.col - round(pos.col)) < _CENTER_THRESHOLD
    )

def _in_ghost_house(tile_r: int, tile_c: int) -> bool:
    return (
        _HOUSE_ROW_MIN <= tile_r <= _HOUSE_ROW_MAX
        and _HOUSE_COL_MIN <= tile_c <= _HOUSE_COL_MAX
    )

def _dist(ar: float, ac: float, br: float, bc: float) -> float:
    return math.sqrt((ar - br) ** 2 + (ac - bc) ** 2)

# ---------------------------------------------------------------------------
# Core: Steering Engines (A* & Classic)
# ---------------------------------------------------------------------------

def _get_astar_direction(ghost: Ghost, grid: "Grid", target: tuple[float, float], can_use_door: bool = True) -> Direction:
    """Modern A* Pathfinding for ruthless CHASE modes."""
    start_r, start_c = _nearest_tile(ghost.position.row), _nearest_tile(ghost.position.col)
    tgt_r, tgt_c = int(target[0]), int(target[1])
    opposite = _OPPOSITE[ghost.direction]

    # open_set tuples: (f_score, g_score, (row, col), initial_direction)
    open_set = []
    
    # Initialize with valid neighbours (No U-Turns on first step)
    for d in _TIE_BREAK:
        if d == opposite:
            continue
        dr, dc = _DIR_VECTOR[d]
        nr, nc = start_r + dr, start_c + dc

        if not grid.in_bounds(nr, nc):
            continue

        passable = grid.is_passable_for_ghost(nr, nc) if can_use_door else not grid.is_blocked_for_pacman(nr, nc)
        if passable:
            g = 1
            h = abs(nr - tgt_r) + abs(nc - tgt_c) # Manhattan Heuristic
            heapq.heappush(open_set, (g + h, g, (nr, nc), d))

    visited = set()

    while open_set:
        f, g, current, first_dir = heapq.heappop(open_set)

        if current == (tgt_r, tgt_c):
            return first_dir # Found path, return the first step taken

        if current in visited:
            continue
        visited.add(current)

        curr_r, curr_c = current
        for d, (dr, dc) in _DIR_VECTOR.items():
            nr, nc = curr_r + dr, curr_c + dc
            
            if not grid.in_bounds(nr, nc) or (nr, nc) in visited:
                continue

            passable = grid.is_passable_for_ghost(nr, nc) if can_use_door else not grid.is_blocked_for_pacman(nr, nc)
            if passable:
                new_g = g + 1
                h = abs(nr - tgt_r) + abs(nc - tgt_c)
                heapq.heappush(open_set, (new_g + h, new_g, (nr, nc), first_dir))

    # Fallback to greedy if target is walled off
    return _get_next_arcade_direction(ghost, grid, target, can_use_door=can_use_door)

def _get_next_arcade_direction(ghost: Ghost, grid: "Grid", target: tuple[float, float], *, can_use_door: bool = True) -> Direction:
    """Classic Euclidean greedy steering (Used for SCATTER out-of-bounds)."""
    tile_r = _nearest_tile(ghost.position.row)
    tile_c = _nearest_tile(ghost.position.col)
    opposite = _OPPOSITE[ghost.direction]
    tgt_r, tgt_c = target

    best_dir: Direction = Direction.NONE
    best_dist: float = math.inf

    for d in _TIE_BREAK: 
        if d == opposite:
            continue 
        dr, dc = _DIR_VECTOR[d]
        nr, nc = tile_r + dr, tile_c + dc

        if not grid.in_bounds(nr, nc):
            continue
        passable = grid.is_passable_for_ghost(nr, nc) if can_use_door else not grid.is_blocked_for_pacman(nr, nc)
        if not passable:
            continue

        d_dist = _dist(float(nr), float(nc), tgt_r, tgt_c)
        if d_dist < best_dist:
            best_dist = d_dist
            best_dir = d

    return best_dir

# ---------------------------------------------------------------------------
# Target-tile selection
# ---------------------------------------------------------------------------

def _get_target_tile(ghost: Ghost, state: "GameState") -> tuple[float, float]:
    tile_r = _nearest_tile(ghost.position.row)
    tile_c = _nearest_tile(ghost.position.col)

    if ghost.state == GhostState.DEAD:
        return (float(_HOUSE_CENTRE[0]), float(_HOUSE_CENTRE[1]))

    if _in_ghost_house(tile_r, tile_c):
        return (float(_EXIT_WAYPOINT[0]), float(_EXIT_WAYPOINT[1]))

    if ghost.state == GhostState.SCATTER:
        name = ghost.id.replace("ghost_", "")
        return _SCATTER_TARGETS.get(name, (-3.0, -3.0))

    pacman = state.entities.get("pacman")
    if pacman is None:
        return (float(_HOUSE_CENTRE[0]), float(_HOUSE_CENTRE[1]))

    pac_r = pacman.position.row
    pac_c = pacman.position.col
    name = ghost.id.replace("ghost_", "")

    if name == "blinky":
        return (pac_r, pac_c)

    if name == "pinky":
        dr, dc = _DIR_VECTOR.get(pacman.direction, (0, 0))
        # Pinky is now deadlier: predicts 4 tiles ahead, and A* finds the fastest way there!
        return (max(1.0, pac_r + dr * 4), max(1.0, pac_c + dc * 4))

    if name == "inky":
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
        dist_to_pac = _dist(ghost.position.row, ghost.position.col, pac_r, pac_c)
        if dist_to_pac > 8.0:
            return (pac_r, pac_c)
        return _SCATTER_TARGETS.get("clyde", (33.0, -3.0))

    return (pac_r, pac_c)

# ---------------------------------------------------------------------------
# Frightened random steering
# ---------------------------------------------------------------------------

def _random_direction(ghost: Ghost, grid: "Grid") -> None:
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
# Wave timer & Public API
# ---------------------------------------------------------------------------

_WAVE_SCHEDULE: Final[list[tuple[str, float | None]]] = [
    ("SCATTER", 7.0),
    ("CHASE", 20.0),
    ("SCATTER", 7.0),
    ("CHASE", 20.0),
    ("SCATTER", 5.0),
    ("CHASE", 20.0),
    ("SCATTER", 5.0),
    ("CHASE", None),
]

def tick_wave_timer(state: "GameState", dt: float) -> None:
    idx = min(state.wave_index, len(_WAVE_SCHEDULE) - 1)
    _, duration = _WAVE_SCHEDULE[idx]

    if duration is None:
        return  

    state.wave_timer += dt
    if state.wave_timer < duration:
        return

    state.wave_timer = 0.0
    state.wave_index = min(state.wave_index + 1, len(_WAVE_SCHEDULE) - 1)
    new_mode = _WAVE_SCHEDULE[state.wave_index][0]

    for entity in state.entities.values():
        if not isinstance(entity, Ghost):
            continue
        if entity.state in (GhostState.FRIGHTENED, GhostState.DEAD):
            continue
        entity.state = GhostState.CHASE if new_mode == "CHASE" else GhostState.SCATTER
        entity.direction = _OPPOSITE[entity.direction]
        entity.last_intersection_tile = (-1, -1)

def update_ghosts(state: "GameState") -> None:
    for entity in state.entities.values():
        if not isinstance(entity, Ghost):
            continue

        if entity.is_human_controlled:
            continue

        ghost = entity
        pos = ghost.position

        if not _at_tile_center(pos):
            continue

        tile_r = _nearest_tile(pos.row)
        tile_c = _nearest_tile(pos.col)
        current_tile = (tile_r, tile_c)

        if current_tile == ghost.last_intersection_tile:
            continue

        if ghost.state == GhostState.FRIGHTENED:
            _random_direction(ghost, state.grid)
            ghost.last_intersection_tile = current_tile
            continue

        target = _get_target_tile(ghost, state)

        # ── THE HYBRID BRAIN (A* for Chase, Greedy for Scatter) ──────────────
        if ghost.state == GhostState.CHASE and not _in_ghost_house(tile_r, tile_c):
            next_dir = _get_astar_direction(ghost, state.grid, target)
        else:
            next_dir = _get_next_arcade_direction(ghost, state.grid, target)

        if next_dir != Direction.NONE:
            ghost.direction = next_dir

        ghost.last_intersection_tile = current_tile

def switch_ghosts_to_chase(state: "GameState") -> None:
    for entity in state.entities.values():
        if isinstance(entity, Ghost) and entity.state == GhostState.SCATTER:
            entity.state = GhostState.CHASE
            entity.direction = _OPPOSITE[entity.direction]
            entity.last_intersection_tile = (-1, -1)

def switch_ghosts_to_scatter(state: "GameState") -> None:
    for entity in state.entities.values():
        if isinstance(entity, Ghost) and entity.state == GhostState.CHASE:
            entity.state = GhostState.SCATTER
            entity.direction = _OPPOSITE[entity.direction]
            entity.last_intersection_tile = (-1, -1)
