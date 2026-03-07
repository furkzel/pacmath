"""Classic — the standard 28 × 31 Pac-Man maze.

Cell legend (matches engine.constants.Cell):
    0  WALL
    1  PATH         (empty, walkable)
    2  PELLET       (standard dot,   +10 pts)
    3  POWER_PELLET (energizer,      +50 pts, enables ghost-eating)
    4  DOOR         (ghost-house gate — ghosts pass through, Pac-Man blocked)

Layout notes
------------
* 28 columns × 31 rows — faithful to the original arcade proportions.
* Left-right symmetric; four power-pellets in the traditional corners.
* Rows 13-15 are the ghost house (PATH tiles, centre of the map).
  The ghost-house door (DOOR tiles) sits at row 12, cols 13-14.
  Ghosts exit through the door; Pac-Man is blocked by it.
* Pac-Man spawns at row 23, col 13 (just below the ghost house).
* Ghost spawns are inside the house (rows 13-15, cols 11-16).
* The tunnel on row 14, cols 0 and 27, wraps left↔right (handled by engine).
"""

from typing import Final

W = 0  # WALL
P = 2  # PELLET
E = 3  # POWER_PELLET (Energizer)
_ = 1  # PATH (empty corridor)
D = 4  # DOOR (ghost-house gate)

# fmt: off
CLASSIC_MAP: Final[list[list[int]]] = [
    # col  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27
    #     ──────────────────────────────────────────────────────────────────────────────────────
    [W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W],  # 0
    [W, P, P, P, P, P, P, P, P, P, P, P, P, W, W, P, P, P, P, P, P, P, P, P, P, P, P, W],  # 1
    [W, P, W, W, W, W, P, W, W, W, W, W, P, W, W, P, W, W, W, W, W, P, W, W, W, W, P, W],  # 2
    [W, E, W, W, W, W, P, W, W, W, W, W, P, W, W, P, W, W, W, W, W, P, W, W, W, W, E, W],  # 3
    [W, P, W, W, W, W, P, W, W, W, W, W, P, W, W, P, W, W, W, W, W, P, W, W, W, W, P, W],  # 4
    [W, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, W],  # 5
    [W, P, W, W, W, W, P, W, W, P, W, W, W, W, W, W, W, W, P, W, W, P, W, W, W, W, P, W],  # 6
    [W, P, W, W, W, W, P, W, W, P, W, W, W, W, W, W, W, W, P, W, W, P, W, W, W, W, P, W],  # 7
    [W, P, P, P, P, P, P, W, W, P, P, P, P, W, W, P, P, P, P, W, W, P, P, P, P, P, P, W],  # 8
    [W, W, W, W, W, W, P, W, W, W, W, W, _, W, W, _, W, W, W, W, W, P, W, W, W, W, W, W],  # 9
    [W, W, W, W, W, W, P, W, W, W, W, W, _, W, W, _, W, W, W, W, W, P, W, W, W, W, W, W],  # 10
    [W, W, W, W, W, W, P, W, W, _, _, _, _, _, _, _, _, _, _, W, W, P, W, W, W, W, W, W],  # 11
    [W, W, W, W, W, W, P, W, W, _, W, W, W, D, D, W, W, W, _, W, W, P, W, W, W, W, W, W],  # 12  ← DOOR at cols 13-14
    [W, W, W, W, W, W, P, W, W, _, W, _, _, _, _, _, _, W, _, W, W, P, W, W, W, W, W, W],  # 13
    [_, _, _, _, _, _, P, _, _, _, W, _, _, _, _, _, _, W, _, _, _, P, _, _, _, _, _, _],   # 14 ← tunnel row
    [W, W, W, W, W, W, P, W, W, _, W, _, _, _, _, _, _, W, _, W, W, P, W, W, W, W, W, W],  # 15
    [W, W, W, W, W, W, P, W, W, _, W, W, W, W, W, W, W, W, _, W, W, P, W, W, W, W, W, W],  # 16
    [W, W, W, W, W, W, P, W, W, _, _, _, _, _, _, _, _, _, _, W, W, P, W, W, W, W, W, W],  # 17
    [W, W, W, W, W, W, P, W, W, _, W, W, W, W, W, W, W, W, _, W, W, P, W, W, W, W, W, W],  # 18
    [W, W, W, W, W, W, P, W, W, _, W, W, W, W, W, W, W, W, _, W, W, P, W, W, W, W, W, W],  # 19
    [W, P, P, P, P, P, P, P, P, P, P, P, P, W, W, P, P, P, P, P, P, P, P, P, P, P, P, W],  # 20
    [W, P, W, W, W, W, P, W, W, W, W, W, P, W, W, P, W, W, W, W, W, P, W, W, W, W, P, W],  # 21
    [W, P, W, W, W, W, P, W, W, W, W, W, P, W, W, P, W, W, W, W, W, P, W, W, W, W, P, W],  # 22
    [W, E, P, P, W, W, P, P, P, P, P, P, P, _, _, P, P, P, P, P, P, P, W, W, P, P, E, W],  # 23
    [W, W, W, P, W, W, P, W, W, P, W, W, W, W, W, W, W, W, P, W, W, P, W, W, P, W, W, W],  # 24
    [W, W, W, P, W, W, P, W, W, P, W, W, W, W, W, W, W, W, P, W, W, P, W, W, P, W, W, W],  # 25
    [W, P, P, P, P, P, P, W, W, P, P, P, P, W, W, P, P, P, P, W, W, P, P, P, P, P, P, W],  # 26
    [W, P, W, W, W, W, W, W, W, W, W, W, P, W, W, P, W, W, W, W, W, W, W, W, W, W, P, W],  # 27
    [W, P, W, W, W, W, W, W, W, W, W, W, P, W, W, P, W, W, W, W, W, W, W, W, W, W, P, W],  # 28
    [W, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, P, W],  # 29
    [W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W],  # 30
]
# fmt: on

# ---------------------------------------------------------------------------
# Spawn hints — data only; the engine & run_local.py consume these.
# ---------------------------------------------------------------------------

#: Pac-Man starts just below the ghost house, centred.
PACMAN_SPAWN: Final[tuple[int, int]] = (23, 13)

#: Four ghost starting positions inside the ghost house.
GHOST_SPAWNS: Final[list[tuple[int, int]]] = [
    (13, 13),  # blinky  — exits first (one tile above house centre)
    (14, 11),  # pinky
    (14, 13),  # inky
    (14, 15),  # clyde
]

#: Column indices of the wrap-around tunnel exits on row 14.
TUNNEL_ROW: Final[int] = 14
TUNNEL_LEFT_COL: Final[int] = 0
TUNNEL_RIGHT_COL: Final[int] = 27
