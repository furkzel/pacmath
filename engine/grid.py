"""Grid — the authoritative 2D maze representation.

Design decisions
----------------
* A plain ``list[list[int]]`` is the backing store so the data is trivially
  JSON-serializable and cheap to send over a WebSocket later.
* ``Grid`` is a dataclass (not a NumPy array) to keep the dependency tree
  minimal for the engine layer.
* All coordinate convention: ``(row, col)`` — row 0 is the top of the maze.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterator

from engine.constants import Cell

# Type alias — rows of columns of raw integer cell values.
GridData = list[list[int]]


@dataclass
class Grid:
    """2D maze represented as a matrix of :class:`~engine.constants.Cell` values.

    Args:
        data: A rectangular 2-D list of integers matching :class:`Cell` enum
              values.  The Grid owns this data and mutations are tracked here.
    """

    data: GridData = field(default_factory=list)

    # ------------------------------------------------------------------
    # Dimensional properties
    # ------------------------------------------------------------------

    @property
    def rows(self) -> int:
        """Number of rows (height) in the grid."""
        return len(self.data)

    @property
    def cols(self) -> int:
        """Number of columns (width) in the grid."""
        return len(self.data[0]) if self.data else 0

    # ------------------------------------------------------------------
    # Cell access
    # ------------------------------------------------------------------

    def get_cell(self, row: int, col: int) -> Cell:
        """Return the :class:`Cell` type at the given coordinates.

        Raises:
            IndexError: If ``(row, col)`` is out of bounds.
        """
        return Cell(self.data[row][col])

    def set_cell(self, row: int, col: int, value: Cell) -> None:
        """Overwrite a single cell (e.g. consume a pellet).

        Args:
            row: Row index.
            col: Column index.
            value: New :class:`Cell` value to write.
        """
        self.data[row][col] = int(value)

    def consume_pellet(self, row: int, col: int) -> Cell | None:
        """Remove a pellet or power-pellet from the grid.

        Args:
            row: Row index.
            col: Column index.

        Returns:
            The :class:`Cell` that was consumed, or ``None`` if the tile held
            no collectible.
        """
        cell = self.get_cell(row, col)
        if cell in (Cell.PELLET, Cell.POWER_PELLET):
            self.set_cell(row, col, Cell.PATH)
            return cell
        return None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_wall(self, row: int, col: int) -> bool:
        """Return ``True`` when the tile at ``(row, col)`` is a wall."""
        return self.get_cell(row, col) == Cell.WALL

    def is_door(self, row: int, col: int) -> bool:
        """Return ``True`` when the tile is a ghost-house gate."""
        return self.get_cell(row, col) == Cell.DOOR

    def is_blocked_for_pacman(self, row: int, col: int) -> bool:
        """Return ``True`` when Pac-Man cannot enter this tile.

        Pac-Man is blocked by both solid walls and the ghost-house gate.
        """
        return self.get_cell(row, col) in (Cell.WALL, Cell.DOOR)

    def is_passable_for_ghost(self, row: int, col: int) -> bool:
        """Return ``True`` when a ghost can traverse this tile.

        Ghosts may pass through the ghost-house door (Cell.DOOR) but are
        blocked by solid walls.
        """
        return self.get_cell(row, col) != Cell.WALL

    def is_walkable(self, row: int, col: int) -> bool:
        """Return ``True`` when an entity can occupy ``(row, col)``."""
        return self.get_cell(row, col) != Cell.WALL

    def in_bounds(self, row: int, col: int) -> bool:
        """Return ``True`` when ``(row, col)`` lies inside the grid."""
        return 0 <= row < self.rows and 0 <= col < self.cols

    def count_pellets(self) -> int:
        """Return the total number of collectible tiles remaining."""
        return sum(
            1
            for row in self.data
            for cell in row
            if cell in (Cell.PELLET, Cell.POWER_PELLET)
        )

    def walkable_cells(self) -> Iterator[tuple[int, int]]:
        """Yield ``(row, col)`` for every non-wall cell — useful for AI."""
        for r, row in enumerate(self.data):
            for c, cell in enumerate(row):
                if cell != Cell.WALL:
                    yield r, c

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict snapshot of the grid."""
        return {
            "rows": self.rows,
            "cols": self.cols,
            "data": copy.deepcopy(self.data),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Grid":
        """Reconstruct a :class:`Grid` from a :meth:`to_dict` snapshot.

        Args:
            payload: Dict produced by :meth:`to_dict`.
        """
        return cls(data=copy.deepcopy(payload["data"]))
