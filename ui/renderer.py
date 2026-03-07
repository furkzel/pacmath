"""Renderer — Pygame drawing layer.

Architectural contract
----------------------
* This module imports from ``engine`` and ``entities`` only.
* Zero game logic lives here.  ``draw()`` is a pure read of ``GameState``.
* Pygame is the only third-party import; it must NEVER appear in engine code.

Coordinate convention
---------------------
Grid ``(row, col)`` → pixel screen centre ``(cx, cy)`` via::

    cx = (col + 0.5) × cell_size        # exact float → int at draw time
    cy = (row + 0.5) × cell_size

Grid tile top-left:: col × cell_size, row × cell_size.

All entity and pellet drawing uses the **centre** formula so a position of
``(row=3.5, col=1.0)`` maps to the exact pixel centre of the cell at
``col=1``, halfway between rows 3 and 4 — correct for mid-corridor rendering.
The HUD is placed *below* the grid (``grid_rows × cell_size`` → window bottom).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Final

import pygame

from config.settings import CharacterTheme, resource_path
from engine.constants import Cell
from engine.game_state import GameState
from entities.entity import Direction
from entities.ghost import Ghost, GhostState
from entities.pacman import PacMan

# ---------------------------------------------------------------------------
# Palette — Principia of Darkness (Style Guide v2.0)
# ---------------------------------------------------------------------------

_DEEP_ONYX: Final = (15, 15, 20)  # Background / void
_OBSIDIAN: Final = (26, 26, 36)  # Surface / panel fill
_COLD_STEEL: Final = (138, 155, 168)  # Border / divider
_PALE_SILVER: Final = (200, 205, 208)  # Text — primary
_DIM_STEEL: Final = (90, 106, 116)  # Text — secondary
_NEON_CYAN: Final = (0, 229, 255)  # Accent — primary
_ETHEREAL_TEAL: Final = (0, 191, 165)  # Accent — alt
_CRIMSON: Final = (183, 28, 28)  # Danger
_EMBER: Final = (230, 81, 0)  # Warning
_VIRIDIAN: Final = (46, 125, 50)  # Success

# Mapped role aliases (backward compat)
_BLACK: Final = _DEEP_ONYX
_WALL: Final = _OBSIDIAN
_WALL_HIGHLIGHT: Final = (46, 46, 56)  # top-left highlight (+20)
_WALL_SHADOW: Final = (10, 10, 18)  # bottom-right shadow (-16)
_WALL_BORDER: Final = _COLD_STEEL
_DOOR: Final = _CRIMSON  # ghost-house gate
_PELLET: Final = (255, 255, 255)  # white core dot
_PELLET_GLOW: Final = (0, 229, 255)  # cyan halo
_POWER_PELLET: Final = _NEON_CYAN
_PACMAN: Final = (255, 214, 0)  # character yellow — keep
_FRIGHTENED: Final = (20, 20, 200)
_FRIGHTENED_FLASH: Final = _PALE_SILVER
_EYE_WHITE: Final = _PALE_SILVER
_EYE_PUPIL: Final = _DEEP_ONYX
_HUD_BG: Final = _DEEP_ONYX
_HUD_TEXT: Final = _PALE_SILVER
_HUD_SCORE_LABEL: Final = _COLD_STEEL
_WIN_OVERLAY: Final = _VIRIDIAN
_LOSE_OVERLAY: Final = _CRIMSON
_PAUSE_OVERLAY: Final = _NEON_CYAN

#: Ghost body colours keyed by the tail-end of the entity id (e.g. "blinky").
_GHOST_COLOR: Final[dict[str, tuple[int, int, int]]] = {
    "blinky": (220, 0, 0),
    "pinky": (255, 182, 255),
    "inky": (0, 232, 216),
    "clyde": (240, 160, 32),
}

#: Direction → base angle (radians) for Pac-Man's mouth in screen-space.
#  Screen-space: x-right = 0, y-down means angles go *clockwise*.
_DIR_ANGLE: Final[dict[Direction, float]] = {
    Direction.RIGHT: 0.0,
    Direction.LEFT: math.pi,
    Direction.UP: math.pi / 2,
    Direction.DOWN: -math.pi / 2,
    Direction.NONE: 0.0,
}

_HUD_HEIGHT: Final[int] = 48
_MOUTH_MAX: Final[float] = math.radians(40)  # max half-angle of open mouth
_MOUTH_SPEED: Final[float] = 6.0  # oscillations per second
_POWER_FLASH_HZ: Final[float] = 3.0  # power-pellet blink speed
_FRIGHTENED_FLASH_THRESHOLD: Final[float] = 2.0  # start flashing last N secs

# ---------------------------------------------------------------------------
# Sprite-sheet slicing
# ---------------------------------------------------------------------------

#: Sentinel used to distinguish "never attempted" from "attempted & failed".
_NOT_LOADED: Final[object] = object()

#: Mapping from Cell enum value to (sheet_row, sheet_col) in a 3×2 sprite grid.
_TILE_SHEET_MAP: Final[dict[Cell, tuple[int, int]]] = {
    Cell.WALL: (0, 0),
    Cell.PATH: (0, 1),
    Cell.PELLET: (0, 2),
    Cell.POWER_PELLET: (1, 0),
    Cell.DOOR: (1, 1),
}

#: Character sprite sheet layout — 4 rows × 5 columns.
#  Row 0 = RIGHT, Row 1 = LEFT, Row 2 = UP, Row 3 = DOWN.
_CHAR_SHEET_ROWS: Final[int] = 4
_CHAR_SHEET_COLS: Final[int] = 5
_CHAR_FRAME_MS: Final[int] = 100  # change frame every 100 ms

#: Direction → sprite-sheet row.
_DIR_TO_CHAR_ROW: Final[dict[Direction, int]] = {
    Direction.RIGHT: 0,
    Direction.LEFT: 1,
    Direction.UP: 2,
    Direction.DOWN: 3,
    Direction.NONE: 0,  # idle = facing right
}


def _load_tile_sprites(
    cell_size: int,
    sheet_path: Path | None = None,
) -> dict[Cell, pygame.Surface] | None:
    """Load a tile-sheet PNG, slice its 3×2 grid, and scale each tile.

    Parameters
    ----------
    cell_size:
        Target pixel size for every tile after scaling.
    sheet_path:
        Explicit path to the sprite-sheet PNG.  When *None* the function
        searches a few conventional locations relative to the project root.

    Returns ``None`` when the asset cannot be loaded (missing file, corrupt
    image, unexpected geometry, etc.).  The renderer will then fall back to
    procedural drawing.
    """
    if sheet_path is None:
        return None  # No asset → use procedural fallback

    resolved = Path(sheet_path)
    if not resolved.is_file():
        return None

    try:
        sheet = pygame.image.load(str(resolved)).convert_alpha()
    except pygame.error:
        return None

    cols, rows = 3, 2
    sw, sh = sheet.get_size()
    tile_w = sw // cols
    tile_h = sh // rows
    if tile_w < 1 or tile_h < 1:
        return None

    sprites: dict[Cell, pygame.Surface] = {}
    for cell, (sr, sc) in _TILE_SHEET_MAP.items():
        sub = sheet.subsurface(pygame.Rect(sc * tile_w, sr * tile_h, tile_w, tile_h))
        # Nearest-neighbour scale preserves pixel-art crispness
        scaled = pygame.transform.scale(sub, (cell_size, cell_size))
        sprites[cell] = scaled
    return sprites


def _load_character_sprites(
    cell_size: int,
    sheet_path: Path | None = None,
) -> list[list[pygame.Surface]] | None:
    """Load a character sprite-sheet (4 rows × 5 cols) and return a 2-D list.

    Returns ``animations[row][col]`` where each surface is scaled to
    *cell_size* × *cell_size*, or ``None`` on failure.
    """
    if sheet_path is None or not Path(sheet_path).is_file():
        return None

    try:
        sheet = pygame.image.load(str(sheet_path)).convert_alpha()
    except pygame.error:
        return None

    sw, sh = sheet.get_size()
    tile_w = sw // _CHAR_SHEET_COLS
    tile_h = sh // _CHAR_SHEET_ROWS
    if tile_w < 1 or tile_h < 1:
        return None

    animations: list[list[pygame.Surface]] = []
    for r in range(_CHAR_SHEET_ROWS):
        row_frames: list[pygame.Surface] = []
        for c in range(_CHAR_SHEET_COLS):
            sub = sheet.subsurface(pygame.Rect(c * tile_w, r * tile_h, tile_w, tile_h))
            scaled = pygame.transform.smoothscale(sub, (cell_size, cell_size))
            row_frames.append(scaled)
        animations.append(row_frames)
    return animations


class Renderer:
    """Pygame rendering layer.  Reads ``GameState``, never writes it.

    Args:
        cell_size:   Pixel size of one grid cell.  Passed in from
                     ``GameSettings.cell_size`` so both layers share the
                     constant.  Defaults to 40 px for comfortable display.
        caption:     Window title string.
    """

    def __init__(
        self,
        cell_size: int = 40,
        caption: str = "Pac-Math  •  Local Prototype",
        tile_sheet_path: Path | None = None,
        character_theme: CharacterTheme = CharacterTheme.CLASSIC,
    ) -> None:
        if not pygame.get_init():
            pygame.init()

        self._cs = cell_size  # pixels per grid cell
        self._tile_sheet_path: Path | None = tile_sheet_path
        self._character_theme: CharacterTheme = character_theme

        # Fonts — initialised lazily after pygame.font.init()
        pygame.font.init()
        self._font_hud = pygame.font.SysFont("consolas,monospace", 22, bold=True)
        self._font_overlay = pygame.font.SysFont("consolas,monospace", 36, bold=True)

        # Surface allocated on first draw() when we know the grid size
        self._surface: pygame.Surface | None = None
        self._win_w: int = 0
        self._win_h: int = 0
        self._caption = caption

        # Tile sprites — deferred until the first draw() so a display exists
        # for convert_alpha().  ``_NOT_LOADED`` means "not yet attempted".
        self._tile_sprites: dict[Cell, pygame.Surface] | None = _NOT_LOADED  # type: ignore[assignment]

        # Character sprites — same deferred loading pattern.
        self._char_sprites: list[list[pygame.Surface]] | None = _NOT_LOADED  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def set_tile_sheet(self, path: Path | None) -> None:
        """Hot-swap the map tile-set (e.g. after a menu selection change).

        The actual image load is deferred to the next ``draw()`` call so we
        don't need a live display surface at call time.
        """
        self._tile_sheet_path = path
        self._tile_sprites = _NOT_LOADED  # type: ignore[assignment]

    def set_character_theme(self, theme: CharacterTheme) -> None:
        """Hot-swap the character sprite-set.

        Deferred to the next ``draw()`` call, same as ``set_tile_sheet``.
        """
        self._character_theme = theme
        self._char_sprites = _NOT_LOADED  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_surface(self, grid_rows: int, grid_cols: int) -> None:
        """Create (or resize) the display surface to fit the current grid."""
        w = grid_cols * self._cs
        h = grid_rows * self._cs + _HUD_HEIGHT
        if self._surface is None or w != self._win_w or h != self._win_h:
            self._win_w, self._win_h = w, h
            self._surface = pygame.display.set_mode((w, h))
            pygame.display.set_caption(self._caption)

    def _cell_rect(self, row: int, col: int) -> pygame.Rect:
        """Return the on-screen pixel rect for a grid tile (top-left origin)."""
        return pygame.Rect(
            col * self._cs,
            row * self._cs,  # HUD is drawn below the grid
            self._cs,
            self._cs,
        )

    def _cell_center(self, pos_row: float, pos_col: float) -> tuple[int, int]:
        """Convert a float grid-space position to pixel screen coordinates.

        The entity ``Position`` stores the center of the entity in grid-space
        (e.g. ``col=1.0`` means the center of column-1).  We therefore map::

            cx = (pos_col + 0.5) × cell_size
            cy = (pos_row + 0.5) × cell_size

        This ensures an entity sitting exactly at tile ``(r, c)`` renders at
        the pixel center of that tile, not at its top-left corner.
        """
        cx = int((pos_col + 0.5) * self._cs)
        cy = int((pos_row + 0.5) * self._cs)
        return cx, cy

    # ------------------------------------------------------------------
    # Grid drawing
    # ------------------------------------------------------------------

    def _draw_grid(self, state: GameState) -> None:
        assert self._surface is not None
        surf = self._surface
        cs = self._cs
        sprites = self._tile_sprites

        # --- Fast path: sprite-sheet tiles --------------------------------
        if sprites is not None:
            t = pygame.time.get_ticks() / 1000.0
            for r, row in enumerate(state.grid.data):
                for c, val in enumerate(row):
                    cell = Cell(val)
                    tile_surf = sprites.get(cell)
                    if tile_surf is not None:
                        dest = (c * cs, r * cs)
                        surf.blit(tile_surf, dest)

                        # Power-pellet overlay pulse (slightly darken/brighten)
                        if cell == Cell.POWER_PELLET:
                            pulse = 0.5 + 0.5 * math.sin(t * _POWER_FLASH_HZ * math.tau)
                            if pulse < 0.4:
                                # Briefly dim the sprite for a blink effect
                                dim = pygame.Surface((cs, cs), pygame.SRCALPHA)
                                dim.fill((0, 0, 0, 90))
                                surf.blit(dim, dest)
                    else:
                        # Cell type not in sprite sheet (should not happen)
                        pygame.draw.rect(
                            surf, _BLACK, pygame.Rect(c * cs, r * cs, cs, cs)
                        )
            return

        # --- Fallback: procedural drawing ---------------------------------
        t = pygame.time.get_ticks() / 1000.0  # seconds since init

        pellet_r = max(2, cs // 8)
        power_r = max(4, cs // 4)
        glow_r = max(5, cs // 4)  # cyan halo radius

        for r, row in enumerate(state.grid.data):
            for c, val in enumerate(row):
                rect = self._cell_rect(r, c)
                cell = Cell(val)

                if cell == Cell.WALL:
                    # Obsidian fill + depth bands (highlight TL, shadow BR)
                    pygame.draw.rect(surf, _WALL, rect)
                    # Top highlight band (2 px)
                    pygame.draw.line(
                        surf,
                        _WALL_HIGHLIGHT,
                        (rect.left, rect.top),
                        (rect.right - 1, rect.top),
                        2,
                    )
                    # Left highlight band (2 px)
                    pygame.draw.line(
                        surf,
                        _WALL_HIGHLIGHT,
                        (rect.left, rect.top),
                        (rect.left, rect.bottom - 1),
                        2,
                    )
                    # Bottom shadow band (2 px)
                    pygame.draw.line(
                        surf,
                        _WALL_SHADOW,
                        (rect.left, rect.bottom - 1),
                        (rect.right - 1, rect.bottom - 1),
                        2,
                    )
                    # Right shadow band (2 px)
                    pygame.draw.line(
                        surf,
                        _WALL_SHADOW,
                        (rect.right - 1, rect.top),
                        (rect.right - 1, rect.bottom - 1),
                        2,
                    )
                    # Cold Steel inner border for definition
                    inner = rect.inflate(-6, -6)
                    pygame.draw.rect(surf, _WALL_BORDER, inner, 1)

                elif cell == Cell.DOOR:
                    # Ghost-house gate: black background + thin pink horizontal bar
                    pygame.draw.rect(surf, _BLACK, rect)
                    bar_h = max(3, cs // 7)
                    bar_rect = pygame.Rect(
                        rect.left,
                        rect.centery - bar_h // 2,
                        rect.width,
                        bar_h,
                    )
                    pygame.draw.rect(surf, _DOOR, bar_rect)

                else:
                    # All non-wall tiles have black background
                    pygame.draw.rect(surf, _BLACK, rect)

                    if cell == Cell.PELLET:
                        cx = rect.centerx
                        cy = rect.centery
                        # Cyan glow halo (40% alpha)
                        glow_surf = pygame.Surface(
                            (glow_r * 2, glow_r * 2), pygame.SRCALPHA
                        )
                        pygame.draw.circle(
                            glow_surf, (*_PELLET_GLOW, 100), (glow_r, glow_r), glow_r
                        )
                        surf.blit(glow_surf, (cx - glow_r, cy - glow_r))
                        # White core dot
                        pygame.draw.circle(surf, _PELLET, (cx, cy), pellet_r)

                    elif cell == Cell.POWER_PELLET:
                        cx = rect.centerx
                        cy = rect.centery
                        # Pulse: scale radius with a sine wave
                        pulse = 0.85 + 0.15 * math.sin(t * _POWER_FLASH_HZ * math.tau)
                        r_px = int(power_r * pulse)
                        pygame.draw.circle(surf, _POWER_PELLET, (cx, cy), r_px)

    # ------------------------------------------------------------------
    # Entity drawing
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Character animation helpers (UI-only, no engine state)
    # ------------------------------------------------------------------

    @staticmethod
    def _character_frame_index() -> int:
        """Return the current animation frame (0..4) based on wall-clock ticks."""
        return (pygame.time.get_ticks() // _CHAR_FRAME_MS) % _CHAR_SHEET_COLS

    def _draw_pacman(self, pacman: PacMan) -> None:
        """Draw Pac-Man — sprite sheet or classic wedge depending on theme."""
        assert self._surface is not None

        # ── Sprite path ──────────────────────────────────────────────────
        if self._char_sprites is not None:
            cx, cy = self._cell_center(pacman.position.row, pacman.position.col)
            row = _DIR_TO_CHAR_ROW[pacman.direction]
            if pacman.direction == Direction.NONE:
                frame = 0  # idle
            else:
                frame = self._character_frame_index()
            sprite = self._char_sprites[row][frame]
            # Blit centred on the entity pixel position
            rect = sprite.get_rect(center=(cx, cy))
            self._surface.blit(sprite, rect)
            return

        # ── Fallback: classic procedural yellow wedge ────────────────────
        self._draw_pacman_classic(pacman)

    def _draw_pacman_classic(self, pacman: PacMan) -> None:
        """Draw Pac-Man as an animated yellow pie/wedge."""
        assert self._surface is not None
        cx, cy = self._cell_center(pacman.position.row, pacman.position.col)
        radius = int(
            self._cs * 0.38
        )  # slightly smaller than half-cell to avoid wall clip
        t = pygame.time.get_ticks() / 1000.0

        # Mouth angle oscillates between 0 (closed) and _MOUTH_MAX (open)
        mouth_half = _MOUTH_MAX * abs(math.sin(t * _MOUTH_SPEED * math.pi))

        base = _DIR_ANGLE[pacman.direction]

        if mouth_half < 0.05:
            # Fully closed — draw a simple circle
            pygame.draw.circle(self._surface, _PACMAN, (cx, cy), radius)
        else:
            # Draw body as a filled polygon (pie slice with mouth removed)
            steps = 32
            start_angle = base + mouth_half
            end_angle = base + math.tau - mouth_half

            points: list[tuple[float, float]] = [(float(cx), float(cy))]
            for i in range(steps + 1):
                angle = start_angle + (end_angle - start_angle) * i / steps
                px = cx + radius * math.cos(angle)
                py = cy - radius * math.sin(angle)
                points.append((px, py))

            pygame.draw.polygon(self._surface, _PACMAN, points)

    def _draw_ghost(self, ghost: Ghost) -> None:
        """Draw a ghost with its body, eyes, and state-dependent colour."""
        assert self._surface is not None
        surf = self._surface
        cx, cy = self._cell_center(ghost.position.row, ghost.position.col)
        cs = self._cs

        # --- Determine body colour -----------------------------------------
        name = ghost.id.replace("ghost_", "")
        t = pygame.time.get_ticks() / 1000.0

        if ghost.state == GhostState.FRIGHTENED:
            # Flash between dark-blue and near-white in the final 2 seconds
            if ghost.frightened_timer <= _FRIGHTENED_FLASH_THRESHOLD:
                flash = math.sin(t * 10) > 0
                body_color = _FRIGHTENED_FLASH if flash else _FRIGHTENED
            else:
                body_color = _FRIGHTENED
        else:
            body_color = _GHOST_COLOR.get(name, (180, 180, 180))

        # --- Ghost silhouette geometry ------------------------------------
        r = int(cs * 0.38)  # slightly smaller than half-cell to avoid wall clip
        body_top = cy - r  # top of ghost
        body_bottom = cy + r  # bottom of ghost
        body_left = cx - r
        body_right = cx + r
        body_width = r * 2

        # Head (upper semicircle)
        pygame.draw.circle(surf, body_color, (cx, cy - r // 3), r)

        # Body rectangle (middle section)
        body_rect = pygame.Rect(body_left, cy - r // 3, body_width, r + r // 3)
        pygame.draw.rect(surf, body_color, body_rect)

        # Feet — three bumps at the bottom (filled semicircles pointing down)
        bump_r = body_width // 6
        bump_y = body_bottom
        for i in range(3):
            bx = body_left + bump_r + i * bump_r * 2
            # Draw a small filled circle at the bottom to form the bumping waves
            pygame.draw.circle(surf, _BLACK, (bx, bump_y), bump_r)

        # --- Eyes ---------------------------------------------------------
        if ghost.state == GhostState.FRIGHTENED:
            # Two small white dots for frightened expression
            eye_y = cy - r // 2
            for ex in (cx - r // 3, cx + r // 3):
                pygame.draw.circle(surf, _EYE_WHITE, (ex, eye_y), max(2, r // 5))
            # Zigzag grimace line
            zig_y = cy + r // 6
            zig_pts = [
                (cx - r // 2, zig_y),
                (cx - r // 4, zig_y - r // 6),
                (cx, zig_y),
                (cx + r // 4, zig_y - r // 6),
                (cx + r // 2, zig_y),
            ]
            pygame.draw.lines(surf, _EYE_WHITE, False, zig_pts, max(1, r // 8))
        else:
            # Normal eyes: white sclera + dark pupil that tracks direction
            eye_offset_x = r // 3
            eye_offset_y = -r // 2
            eye_r = max(3, r // 4)
            pupil_r = max(1, eye_r // 2)

            # Pupil direction offset in pixels
            dir_map: dict[Direction, tuple[int, int]] = {
                Direction.RIGHT: (pupil_r, 0),
                Direction.LEFT: (-pupil_r, 0),
                Direction.UP: (0, -pupil_r),
                Direction.DOWN: (0, pupil_r),
                Direction.NONE: (pupil_r, 0),
            }
            pdx, pdy = dir_map[ghost.direction]

            for ex in (cx - eye_offset_x, cx + eye_offset_x):
                ey = cy + eye_offset_y
                pygame.draw.circle(surf, _EYE_WHITE, (ex, ey), eye_r)
                pygame.draw.circle(surf, _EYE_PUPIL, (ex + pdx, ey + pdy), pupil_r)

    # ------------------------------------------------------------------
    # HUD
    # ------------------------------------------------------------------

    def _draw_hud(self, state: GameState) -> None:
        """Draw score, lives, and end-game messages below the grid."""
        assert self._surface is not None
        surf = self._surface
        hud_y = self._win_h - _HUD_HEIGHT

        # Background bar
        hud_rect = pygame.Rect(0, hud_y, self._win_w, _HUD_HEIGHT)
        pygame.draw.rect(surf, _HUD_BG, hud_rect)
        # 2px Cold Steel separator for visibility
        pygame.draw.line(surf, _COLD_STEEL, (0, hud_y), (self._win_w, hud_y), 2)

        # Score
        score_surf = self._font_hud.render(f"SCORE  {state.score:>6}", True, _HUD_TEXT)
        surf.blit(
            score_surf, (12, hud_y + (_HUD_HEIGHT - score_surf.get_height()) // 2)
        )

        # Lives — draw small yellow circles
        pacman = state.entities.get("pacman")
        lives = pacman.lives if isinstance(pacman, PacMan) else state.lives
        life_label = self._font_hud.render("LIVES", True, _HUD_SCORE_LABEL)
        lx = self._win_w - 16 - lives * 22 - life_label.get_width() - 8
        surf.blit(
            life_label, (lx, hud_y + (_HUD_HEIGHT - life_label.get_height()) // 2)
        )
        for i in range(lives):
            lx += life_label.get_width() + 8 + i * 22
            pygame.draw.circle(
                surf,
                _PACMAN,
                (lx + life_label.get_width() + 10, hud_y + _HUD_HEIGHT // 2),
                8,
            )

    # ------------------------------------------------------------------
    # Overlay screens
    # ------------------------------------------------------------------

    def _draw_overlay(self, text: str, color: tuple[int, int, int]) -> None:
        """Draw a bordered Obsidian panel overlay with centred message."""
        assert self._surface is not None
        surf = self._surface
        grid_h = self._win_h - _HUD_HEIGHT

        # Dim curtain
        overlay = pygame.Surface((self._win_w, grid_h), pygame.SRCALPHA)
        overlay.fill((*_DEEP_ONYX, 180))
        surf.blit(overlay, (0, 0))

        # Obsidian panel
        panel_w, panel_h = min(360, self._win_w - 40), 120
        panel_rect = pygame.Rect(
            (self._win_w - panel_w) // 2,
            (grid_h - panel_h) // 2,
            panel_w,
            panel_h,
        )
        pygame.draw.rect(surf, _OBSIDIAN, panel_rect, border_radius=6)
        pygame.draw.rect(surf, _COLD_STEEL, panel_rect, 1, border_radius=6)

        # Title text
        label = self._font_overlay.render(text, True, color)
        surf.blit(
            label, label.get_rect(center=(panel_rect.centerx, panel_rect.centery - 16))
        )

        # Subtitle
        sub_label = self._font_hud.render("Press  R  to restart", True, _PALE_SILVER)
        surf.blit(
            sub_label,
            sub_label.get_rect(center=(panel_rect.centerx, panel_rect.centery + 28)),
        )

    def _draw_pause_overlay(self) -> None:
        """Draw a bordered Obsidian panel with 'PAUSED' in Neon Cyan."""
        assert self._surface is not None
        surf = self._surface
        grid_h = self._win_h - _HUD_HEIGHT

        # Dim curtain
        overlay = pygame.Surface((self._win_w, grid_h), pygame.SRCALPHA)
        overlay.fill((*_DEEP_ONYX, 160))
        surf.blit(overlay, (0, 0))

        # Obsidian panel
        panel_w, panel_h = min(340, self._win_w - 40), 110
        panel_rect = pygame.Rect(
            (self._win_w - panel_w) // 2,
            (grid_h - panel_h) // 2,
            panel_w,
            panel_h,
        )
        pygame.draw.rect(surf, _OBSIDIAN, panel_rect, border_radius=6)
        pygame.draw.rect(surf, _COLD_STEEL, panel_rect, 1, border_radius=6)

        # Title
        label = self._font_overlay.render("PAUSED", True, _PAUSE_OVERLAY)
        surf.blit(
            label, label.get_rect(center=(panel_rect.centerx, panel_rect.centery - 16))
        )

        # Controls hint
        sub_label = self._font_hud.render("ESC  Resume  |  M  Menu", True, _PALE_SILVER)
        surf.blit(
            sub_label,
            sub_label.get_rect(center=(panel_rect.centerx, panel_rect.centery + 26)),
        )

    def _draw_role_label(self, label: str) -> None:
        """Draw a small 'YOU ARE <role>' tag in the top-right corner."""
        assert self._surface is not None
        surf = self._surface
        text = f"YOU ARE {label}"
        rendered = self._font_hud.render(text, True, _NEON_CYAN)
        pad_x, pad_y = 10, 4
        bg_w = rendered.get_width() + pad_x * 2
        bg_h = rendered.get_height() + pad_y * 2
        bg_rect = pygame.Rect(self._win_w - bg_w - 4, 2, bg_w, bg_h)
        pygame.draw.rect(surf, _OBSIDIAN, bg_rect, border_radius=4)
        pygame.draw.rect(surf, _COLD_STEEL, bg_rect, 1, border_radius=4)
        surf.blit(rendered, (bg_rect.x + pad_x, bg_rect.y + pad_y))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def draw(
        self,
        state: GameState,
        *,
        paused: bool = False,
        role_label: str | None = None,
    ) -> None:
        """Render one complete frame.

        This is the **only** public method the game loop needs.  It does NOT
        call ``pygame.display.flip()`` — that belongs to the loop so frame
        timing stays outside the renderer.

        Args:
            state:      The current, authoritative :class:`~engine.game_state.GameState`.
            paused:     When *True*, draw the game state with a pause overlay on top.
            role_label: Optional character name to display (e.g. ``"BLINKY"``).
                        Used in multiplayer to show which entity this window controls.
        """
        self._ensure_surface(state.grid.rows, state.grid.cols)
        assert self._surface is not None

        # Lazily load tile sprites (needs an active display for convert_alpha)
        if self._tile_sprites is _NOT_LOADED:
            self._tile_sprites = _load_tile_sprites(self._cs, self._tile_sheet_path)
        if self._char_sprites is _NOT_LOADED:
            self._char_sprites = _load_character_sprites(
                self._cs, self._character_theme.asset_path
            )

        self._surface.fill(_BLACK)
        self._draw_grid(state)

        # Draw entities in two passes: ghosts behind Pac-Man
        for entity in state.entities.values():
            if isinstance(entity, Ghost):
                self._draw_ghost(entity)

        for entity in state.entities.values():
            if isinstance(entity, PacMan):
                self._draw_pacman(entity)

        self._draw_hud(state)

        # Role label overlay (multiplayer)
        if role_label:
            self._draw_role_label(role_label)

        # Overlays
        if paused:
            self._draw_pause_overlay()
        elif not state.is_active:
            if state.is_game_over:
                self._draw_overlay("GAME  OVER", _LOSE_OVERLAY)
            elif state.is_level_complete:
                self._draw_overlay("YOU  WIN!", _WIN_OVERLAY)
