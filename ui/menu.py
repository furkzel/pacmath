"""Main Menu, Browse Games, Lobby, and Character Selection renderers for PAC-MATH.

Implements **The Principia of Darkness** (Style Guide v2.0):
  - Deep Onyx backgrounds, Obsidian panels, Cold Steel borders
  - Neon Cyan accents, Pale Silver text, Dim Steel hints
  - Typewriter prompt, bracket-flash selectors, arrow-sprite navigation
  - Logo sprite centrepiece with graceful fallback to text

Architectural contract
----------------------
* Reads ``config.settings`` for available themes; **never** touches the engine.
* ``MenuState`` owns all selection data; ``MenuRenderer`` is a pure view.
* Exposes ``GameMode`` so the run-loop can branch Classic vs. Pac-Math.
* ``BrowseGamesRenderer`` shows discovered LAN games.
* ``LobbyRenderer`` shows the 5-slot character lobby and host START button.
* ``CharSelectRenderer`` shows the 10 s character-selection phase with live
  animated sprite preview, timer bar, and role assignment.
* All Pygame drawing is self-contained — the game loop only calls
  ``handle_event()``, ``update()``, and ``draw()``.
"""

from __future__ import annotations

import math
from enum import Enum
from pathlib import Path
from typing import Any, Final

import pygame

from config.settings import (
    CHARACTER_THEMES,
    CharacterTheme,
    MapTheme,
    MAP_THEMES,
    ThemeSettings,
    resource_path,
)
from server import ALL_ROLES

# ══════════════════════════════════════════════════════════════════════════════
# Principia of Darkness — palette
# ══════════════════════════════════════════════════════════════════════════════

_DEEP_ONYX: Final = (15, 15, 20)
_OBSIDIAN: Final = (26, 26, 36)
_COLD_STEEL: Final = (138, 155, 168)
_PALE_SILVER: Final = (200, 205, 208)
_DIM_STEEL: Final = (90, 106, 116)
_NEON_CYAN: Final = (0, 229, 255)
_ETHEREAL_TEAL: Final = (0, 191, 165)
_CRIMSON: Final = (183, 28, 28)
_VIRIDIAN: Final = (46, 125, 50)

# Semantic aliases
_BG: Final = _DEEP_ONYX
_TITLE_COLOR: Final = _NEON_CYAN
_TITLE_SHADOW: Final = (0, 114, 127)
_LABEL_COLOR: Final = _COLD_STEEL
_VALUE_COLOR: Final = _PALE_SILVER
_ARROW_COLOR: Final = _DIM_STEEL
_DISABLED_COLOR: Final = (50, 55, 60)
_PROMPT_COLOR: Final = _NEON_CYAN
_HINT_COLOR: Final = _DIM_STEEL
_DIVIDER_COLOR: Final = (60, 68, 76)
_SELECTED_COLOR: Final = _NEON_CYAN
_ACTIVE_COLOR: Final = _NEON_CYAN

# Layout
_MENU_WIDTH: Final[int] = 620
_MENU_HEIGHT: Final[int] = 640
_PANEL_RADIUS: Final[int] = 6
_PANEL_PAD_X: Final[int] = 30
_PANEL_INNER_PAD: Final[int] = 12

# ══════════════════════════════════════════════════════════════════════════════
# Asset loading — robust with fallback
# ══════════════════════════════════════════════════════════════════════════════

_ASSET_DIR: Final = resource_path("assets/ui")


def _load_asset(name: str) -> pygame.Surface | None:
    """Load a PNG from ``assets/ui/`` and return the converted surface.

    Returns *None* when the file is missing or corrupt so callers can
    fall back to geometric primitives gracefully.
    """
    path = _ASSET_DIR / name
    if not path.is_file():
        return None
    try:
        return pygame.image.load(str(path)).convert_alpha()
    except pygame.error:
        return None


def _scale_to_width(surf: pygame.Surface, target_w: int) -> pygame.Surface:
    """Smooth-scale *surf* to *target_w*, preserving aspect ratio."""
    w, h = surf.get_size()
    ratio = target_w / w
    return pygame.transform.smoothscale(surf, (target_w, int(h * ratio)))


def _tint_surface(surf: pygame.Surface, color: tuple[int, int, int]) -> pygame.Surface:
    """Return a colour-tinted copy of *surf* (multiply blend)."""
    tinted = surf.copy()
    overlay = pygame.Surface(tinted.get_size(), pygame.SRCALPHA)
    overlay.fill((*color, 255))
    tinted.blit(overlay, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return tinted


# ══════════════════════════════════════════════════════════════════════════════
# Scanline wipe helper
# ══════════════════════════════════════════════════════════════════════════════

_WIPE_DURATION_MS: Final[float] = 300.0


class _ScanlineWipe:
    """300 ms top→bottom wipe with a 2 px Neon Cyan leading scanline.

    Usage
    -----
    Call ``start()`` to begin.  Each frame ``draw(surf)`` blits a
    progressively-shrinking black rect from the bottom up, with a Cyan
    line at the leading edge.  ``is_active`` becomes *False* once done.
    """

    def __init__(self) -> None:
        self._timer: float = 0.0
        self._active: bool = False

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        self._timer = 0.0
        self._active = True

    def update(self, dt_ms: float) -> None:
        if not self._active:
            return
        self._timer += dt_ms
        if self._timer >= _WIPE_DURATION_MS:
            self._active = False

    def draw(self, surf: pygame.Surface) -> None:
        if not self._active:
            return
        w, h = surf.get_size()
        progress = min(1.0, self._timer / _WIPE_DURATION_MS)  # 0→1, linear
        reveal_y = int(h * progress)
        # Black curtain below scanline
        if reveal_y < h:
            curtain = pygame.Rect(0, reveal_y, w, h - reveal_y)
            pygame.draw.rect(surf, _DEEP_ONYX, curtain)
        # Cyan leading scanline (2 px)
        if reveal_y < h:
            pygame.draw.line(surf, _NEON_CYAN, (0, reveal_y), (w, reveal_y), 2)


# ══════════════════════════════════════════════════════════════════════════════
# GameMode enum  (public — imported by run_local.py)
# ══════════════════════════════════════════════════════════════════════════════


class GameMode(Enum):
    CLASSIC = "Classic (Local AI)"
    PACMATH = "Pac-Math (Online 1v4)"


_GAME_MODES: Final[list[GameMode]] = list(GameMode)


# ══════════════════════════════════════════════════════════════════════════════
# MenuState — pure data, no rendering
# ══════════════════════════════════════════════════════════════════════════════

_ROW_MODE: Final[int] = 0
_ROW_MAP: Final[int] = 1
_ROW_COUNT: Final[int] = 2


class _MenuState:
    """Holds all mutable selection state for the main menu.

    ``MenuRenderer`` delegates mutation here so it remains a pure view.
    """

    def __init__(self) -> None:
        self.game_modes: list[GameMode] = list(_GAME_MODES)
        self.mode_index: int = 0

        self.map_themes: list[MapTheme] = list(MAP_THEMES)
        self.map_index: int = 0

        self.active_row: int = _ROW_MODE
        self.confirmed: bool = False

    # --- Convenience accessors ---

    @property
    def game_mode(self) -> GameMode:
        return self.game_modes[self.mode_index]

    @property
    def theme_settings(self) -> ThemeSettings:
        return ThemeSettings(
            map_theme=self.map_themes[self.map_index],
        )

    # --- Mutation ---

    def nudge(self, delta: int) -> None:
        if self.active_row == _ROW_MODE:
            self.mode_index = (self.mode_index + delta) % len(self.game_modes)
        elif self.active_row == _ROW_MAP:
            self.map_index = (self.map_index + delta) % len(self.map_themes)

    def cycle_row(self, delta: int) -> None:
        self.active_row = (self.active_row + delta) % _ROW_COUNT


# ══════════════════════════════════════════════════════════════════════════════
# MenuRenderer — pure view
# ══════════════════════════════════════════════════════════════════════════════


class MenuRenderer:
    """Self-contained main-menu screen with Principia aesthetic.

    Lifecycle
    ---------
    1. Instantiate once.
    2. Each frame: ``handle_event(e)`` → ``update(dt)`` → ``draw()``.
    3. When ``is_confirmed`` is *True*, read ``theme_settings`` and
       transition to PLAYING.
    """

    def __init__(self) -> None:
        if not pygame.get_init():
            pygame.init()
        pygame.font.init()

        # ── Fonts (Consolas / monospace) ─────────────────────────────────
        self._font_title = pygame.font.SysFont("consolas,monospace", 52, bold=True)
        self._font_subtitle = pygame.font.SysFont("consolas,monospace", 16)
        self._font_label = pygame.font.SysFont("consolas,monospace", 22, bold=True)
        self._font_value = pygame.font.SysFont("consolas,monospace", 22)
        self._font_prompt = pygame.font.SysFont("consolas,monospace", 26, bold=True)
        self._font_hint = pygame.font.SysFont("consolas,monospace", 14)

        # ── Display surface ──────────────────────────────────────────────
        self._surface: pygame.Surface = pygame.display.set_mode(
            (_MENU_WIDTH, _MENU_HEIGHT)
        )
        pygame.display.set_caption("Pac-Math  •  Main Menu")

        # ── State (decoupled) ────────────────────────────────────────────
        self._state = _MenuState()

        # ── Assets (loaded once, fallback-safe) ──────────────────────────
        self._logo: pygame.Surface | None = None
        self._arrow_l: pygame.Surface | None = None
        self._arrow_r: pygame.Surface | None = None
        self._arrow_l_active: pygame.Surface | None = None
        self._arrow_r_active: pygame.Surface | None = None
        self._assets_loaded: bool = False

        # ── Typewriter state ─────────────────────────────────────────────
        self._tw_text: str = "PRESS  ENTER  TO  START"
        self._tw_char_ms: float = 33.0
        self._tw_hold_ms: float = 1000.0
        self._tw_timer: float = 0.0
        self._tw_phase: str = "typing"  # typing | holding | clearing

        # ── Scanline wipe ────────────────────────────────────────────────
        self._wipe = _ScanlineWipe()
        self._wipe.start()  # initial reveal

        # ── Animation clock ──────────────────────────────────────────────
        self._elapsed: float = 0.0

        # ── Click hitboxes (populated on first draw) ─────────────────────
        self._mode_left_rect = pygame.Rect(0, 0, 0, 0)
        self._mode_right_rect = pygame.Rect(0, 0, 0, 0)
        self._map_left_rect = pygame.Rect(0, 0, 0, 0)
        self._map_right_rect = pygame.Rect(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # Asset bootstrap (deferred until display exists)
    # ------------------------------------------------------------------

    def _load_assets(self) -> None:
        if self._assets_loaded:
            return
        self._assets_loaded = True

        raw_logo = _load_asset("logo.png")
        if raw_logo is not None:
            self._logo = _scale_to_width(raw_logo, min(460, _MENU_WIDTH - 40))

        raw_l = _load_asset("left.png")
        raw_r = _load_asset("right.png")
        arrow_size = 28
        if raw_l is not None:
            scaled = pygame.transform.smoothscale(raw_l, (arrow_size, arrow_size))
            self._arrow_l = _tint_surface(scaled, _DIM_STEEL)
            self._arrow_l_active = _tint_surface(scaled, _NEON_CYAN)
        if raw_r is not None:
            scaled = pygame.transform.smoothscale(raw_r, (arrow_size, arrow_size))
            self._arrow_r = _tint_surface(scaled, _DIM_STEEL)
            self._arrow_r_active = _tint_surface(scaled, _NEON_CYAN)

    # ------------------------------------------------------------------
    # Public API (same surface as before — no breaking changes)
    # ------------------------------------------------------------------

    @property
    def is_confirmed(self) -> bool:
        return self._state.confirmed

    @property
    def game_mode(self) -> GameMode:
        return self._state.game_mode

    @property
    def theme_settings(self) -> ThemeSettings:
        return self._state.theme_settings

    def handle_event(self, event: pygame.event.Event) -> None:
        if self._state.confirmed:
            return

        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_DOWN):
                self._state.cycle_row(1 if event.key == pygame.K_DOWN else -1)
            elif event.key == pygame.K_LEFT:
                self._state.nudge(-1)
            elif event.key == pygame.K_RIGHT:
                self._state.nudge(+1)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._state.confirmed = True

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._mode_left_rect.collidepoint(mx, my):
                self._state.active_row = _ROW_MODE
                self._state.nudge(-1)
            elif self._mode_right_rect.collidepoint(mx, my):
                self._state.active_row = _ROW_MODE
                self._state.nudge(+1)
            elif self._map_left_rect.collidepoint(mx, my):
                self._state.active_row = _ROW_MAP
                self._state.nudge(-1)
            elif self._map_right_rect.collidepoint(mx, my):
                self._state.active_row = _ROW_MAP
                self._state.nudge(+1)

    def update(self, dt: float) -> None:
        self._elapsed += dt
        dt_ms = dt * 1000.0

        # Typewriter FSM
        self._tw_timer += dt_ms
        total = len(self._tw_text) * self._tw_char_ms
        if self._tw_phase == "typing" and self._tw_timer >= total:
            self._tw_phase = "holding"
            self._tw_timer = 0.0
        elif self._tw_phase == "holding" and self._tw_timer >= self._tw_hold_ms:
            self._tw_phase = "clearing"
            self._tw_timer = 0.0
        elif self._tw_phase == "clearing" and self._tw_timer >= total * 0.5:
            self._tw_phase = "typing"
            self._tw_timer = 0.0

        # Scanline wipe
        self._wipe.update(dt_ms)

    def draw(self) -> None:
        self._load_assets()

        s = self._state
        surf = self._surface
        surf.fill(_BG)
        cx = _MENU_WIDTH // 2

        # ── Logo / Title ─────────────────────────────────────────────────
        title_bottom = self._draw_title(surf, cx, 12)

        # ── Divider ──────────────────────────────────────────────────────
        div_y = title_bottom + 8
        pygame.draw.line(
            surf, _DIVIDER_COLOR, (60, div_y), (_MENU_WIDTH - 60, div_y), 1
        )

        # ── Selector panels ──────────────────────────────────────────────
        panel_y = div_y + 14

        panel_y = self._draw_panel_selector(
            surf,
            cx,
            panel_y,
            label="GAME  MODE",
            items=[m.value for m in s.game_modes],
            index=s.mode_index,
            active=s.active_row == _ROW_MODE,
            left_attr="_mode_left_rect",
            right_attr="_mode_right_rect",
        )

        panel_y = self._draw_panel_selector(
            surf,
            cx,
            panel_y + 16,
            label="MAP  THEME",
            items=[t.label for t in s.map_themes],
            index=s.map_index,
            active=s.active_row == _ROW_MAP,
            left_attr="_map_left_rect",
            right_attr="_map_right_rect",
        )

        # ── Divider ──────────────────────────────────────────────────────
        div2_y = panel_y + 16
        pygame.draw.line(
            surf, _DIVIDER_COLOR, (60, div2_y), (_MENU_WIDTH - 60, div2_y), 1
        )

        # ── Typewriter prompt ────────────────────────────────────────────
        self._draw_prompt(surf, cx, div2_y + 48)

        # ── Scanline wipe overlay ────────────────────────────────────────
        self._wipe.draw(surf)

    # ------------------------------------------------------------------
    # Private drawing helpers
    # ------------------------------------------------------------------

    def _draw_title(self, surf: pygame.Surface, cx: int, y: int) -> int:
        """Draw the logo sprite or fallback title text.

        Returns the y-coordinate of the bottom edge so layout
        continues below.
        """
        if self._logo is not None:
            lr = self._logo.get_rect(midtop=(cx, y + 4))
            surf.blit(self._logo, lr)
            # Subtitle below logo
            sub = self._font_subtitle.render(
                "Select your battlefield, then press ENTER", True, _HINT_COLOR
            )
            sub_y = lr.bottom + 4
            surf.blit(sub, sub.get_rect(center=(cx, sub_y)))
            return sub_y + sub.get_height() // 2 + 2
        else:
            # Fallback: rendered text title with drop-shadow
            text = "PAC - MATH"
            title_y = y + 40
            shadow = self._font_title.render(text, True, _TITLE_SHADOW)
            surf.blit(shadow, shadow.get_rect(center=(cx + 3, title_y + 3)))
            fg = self._font_title.render(text, True, _TITLE_COLOR)
            surf.blit(fg, fg.get_rect(center=(cx, title_y)))
            sub = self._font_subtitle.render(
                "Select your battlefield, then press ENTER", True, _HINT_COLOR
            )
            sub_y = title_y + 38
            surf.blit(sub, sub.get_rect(center=(cx, sub_y)))
            return sub_y + 10

    def _draw_panel_selector(
        self,
        surf: pygame.Surface,
        cx: int,
        y: int,
        *,
        label: str,
        items: list[str],
        index: int,
        active: bool,
        left_attr: str,
        right_attr: str,
    ) -> int:
        """Draw an Obsidian panel containing section label + value selector.

        Returns the y-coordinate *below* the panel for layout chaining.
        """
        panel_w = _MENU_WIDTH - _PANEL_PAD_X * 2
        panel_h = 96
        panel_rect = pygame.Rect(_PANEL_PAD_X, y, panel_w, panel_h)

        # Obsidian fill
        pygame.draw.rect(surf, _OBSIDIAN, panel_rect, border_radius=_PANEL_RADIUS)
        # Border — Neon Cyan when active, Cold Steel otherwise
        border_color = _NEON_CYAN if active else _COLD_STEEL
        pygame.draw.rect(surf, border_color, panel_rect, 1, border_radius=_PANEL_RADIUS)

        inner_cx = panel_rect.centerx

        # Section label (22 pt ALL CAPS Cold Steel / Neon Cyan)
        label_color = _ACTIVE_COLOR if active else _LABEL_COLOR
        lbl_surf = self._font_label.render(label, True, label_color)
        lbl_y = panel_rect.top + 12
        surf.blit(lbl_surf, lbl_surf.get_rect(center=(inner_cx, lbl_y)))

        # Value
        val_color = _SELECTED_COLOR if active else _VALUE_COLOR
        val_text = items[index]
        val_surf = self._font_value.render(val_text, True, val_color)
        val_y = panel_rect.top + 44
        val_rect = val_surf.get_rect(center=(inner_cx, val_y))
        surf.blit(val_surf, val_rect)

        # Bracket flash at 2 Hz
        if active:
            bracket_on = (pygame.time.get_ticks() // 250) % 2 == 0
            if bracket_on:
                bl = self._font_value.render("[", True, _NEON_CYAN)
                br = self._font_value.render("]", True, _NEON_CYAN)
                surf.blit(bl, (val_rect.left - 18, val_rect.top))
                surf.blit(br, (val_rect.right + 6, val_rect.top))

        # Arrow sprites or fallback text arrows
        arrow_margin = 52
        a_inactive_l = self._arrow_l
        a_active_l = self._arrow_l_active
        a_inactive_r = self._arrow_r
        a_active_r = self._arrow_r_active

        al_x = val_rect.left - arrow_margin
        ar_x = val_rect.right + arrow_margin - 28  # 28 = arrow size

        if a_inactive_l and a_active_l:
            al_img = a_active_l if active else a_inactive_l
            al_rect = al_img.get_rect(center=(al_x, val_y))
            surf.blit(al_img, al_rect)
        else:
            # Geometric fallback: Neon Cyan / Dim Steel triangle
            color = _NEON_CYAN if active else _DIM_STEEL
            al_rect = self._draw_arrow_triangle(
                surf, al_x, val_y, facing="left", color=color
            )

        if a_inactive_r and a_active_r:
            ar_img = a_active_r if active else a_inactive_r
            ar_rect = ar_img.get_rect(center=(ar_x + 28, val_y))
            surf.blit(ar_img, ar_rect)
        else:
            color = _NEON_CYAN if active else _DIM_STEEL
            ar_rect = self._draw_arrow_triangle(
                surf, ar_x + 28, val_y, facing="right", color=color
            )

        setattr(self, left_attr, al_rect.inflate(16, 16))
        setattr(self, right_attr, ar_rect.inflate(16, 16))

        # Pagination dots
        dot_y = panel_rect.top + 72
        total = len(items)
        dot_spacing = 14
        start_x = inner_cx - (total - 1) * dot_spacing // 2
        for i in range(total):
            dot_color = _NEON_CYAN if i == index else _DISABLED_COLOR
            pygame.draw.circle(surf, dot_color, (start_x + i * dot_spacing, dot_y), 3)

        # Hint inside panel bottom
        hint_text = (
            "\u2191 \u2193  switch section  \u2022  \u2190 \u2192  change"
            if active
            else "\u2191 \u2193  to select"
        )
        hint = self._font_hint.render(hint_text, True, _HINT_COLOR)
        surf.blit(hint, hint.get_rect(center=(inner_cx, dot_y + 14)))

        return panel_rect.bottom

    @staticmethod
    def _draw_arrow_triangle(
        surf: pygame.Surface,
        cx: int,
        cy: int,
        *,
        facing: str,
        color: tuple[int, int, int],
        size: int = 10,
    ) -> pygame.Rect:
        """Draw a filled triangle facing left or right. Returns bounding rect."""
        if facing == "left":
            pts = [
                (cx - size, cy),
                (cx + size // 2, cy - size),
                (cx + size // 2, cy + size),
            ]
        else:
            pts = [
                (cx + size, cy),
                (cx - size // 2, cy - size),
                (cx - size // 2, cy + size),
            ]
        pygame.draw.polygon(surf, color, pts)
        return pygame.Rect(cx - size, cy - size, size * 2, size * 2)

    def _draw_prompt(self, surf: pygame.Surface, cx: int, y: int) -> None:
        """Typewriter 'PRESS ENTER TO START' with trailing block cursor."""
        total_chars = len(self._tw_text)

        if self._tw_phase == "typing":
            visible = min(total_chars, int(self._tw_timer / self._tw_char_ms))
        elif self._tw_phase == "holding":
            visible = total_chars
        else:  # clearing
            erase_speed = self._tw_char_ms * 0.5
            erased = min(total_chars, int(self._tw_timer / erase_speed))
            visible = total_chars - erased

        display_text = self._tw_text[:visible]

        # Blinking block cursor at 2 Hz
        cursor_on = (pygame.time.get_ticks() // 250) % 2 == 0
        if cursor_on and self._tw_phase != "clearing":
            display_text += "\u2588"

        rendered = self._font_prompt.render(display_text, True, _PROMPT_COLOR)
        surf.blit(rendered, rendered.get_rect(center=(cx, y)))

        # Footer
        footer = self._font_hint.render("ESC to quit", True, _HINT_COLOR)
        surf.blit(footer, footer.get_rect(center=(cx, y + 36)))


# ══════════════════════════════════════════════════════════════════════════════
# BrowseGamesRenderer — LAN discovery game list
# ══════════════════════════════════════════════════════════════════════════════

_BROWSE_WIDTH: Final[int] = 620
_BROWSE_HEIGHT: Final[int] = 560
_ROW_HT: Final[int] = 52
_MAX_VISIBLE: Final[int] = 7


class BrowseGamesRenderer:
    """Dynamic list of discovered LAN games (Principia styled).

    Lifecycle: instantiate → ``handle_event``/``update``/``draw(games)``
    each frame → read ``selected_game`` when non-None.
    """

    def __init__(self) -> None:
        if not pygame.get_init():
            pygame.init()
        pygame.font.init()

        self._font_title = pygame.font.SysFont("consolas,monospace", 38, bold=True)
        self._font_row = pygame.font.SysFont("consolas,monospace", 20)
        self._font_row_sm = pygame.font.SysFont("consolas,monospace", 14)
        self._font_hint = pygame.font.SysFont("consolas,monospace", 14)
        self._font_prompt = pygame.font.SysFont("consolas,monospace", 22, bold=True)

        self._surface: pygame.Surface = pygame.display.set_mode(
            (_BROWSE_WIDTH, _BROWSE_HEIGHT)
        )
        pygame.display.set_caption("Pac-Math  \u2022  Browse Games")

        self._cursor: int = 0
        self._selected_game: Any | None = None
        self._elapsed: float = 0.0
        self._last_games: list[Any] = []
        self._row_rects: list[pygame.Rect] = []
        self._wipe = _ScanlineWipe()
        self._wipe.start()

    @property
    def selected_game(self) -> Any | None:
        return self._selected_game

    def handle_event(self, event: pygame.event.Event) -> None:
        count = len(self._last_games)
        if count == 0:
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_UP:
                self._cursor = (self._cursor - 1) % count
            elif event.key == pygame.K_DOWN:
                self._cursor = (self._cursor + 1) % count
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if 0 <= self._cursor < count:
                    self._selected_game = self._last_games[self._cursor]

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for i, rect in enumerate(self._row_rects):
                if rect.collidepoint(mx, my) and i < count:
                    self._cursor = i
                    self._selected_game = self._last_games[i]
                    break

    def update(self, dt: float) -> None:
        self._elapsed += dt
        self._wipe.update(dt * 1000.0)

    def draw(self, games: list[Any] | None = None) -> None:
        if games is None:
            games = []
        self._last_games = games
        if games and self._cursor >= len(games):
            self._cursor = max(0, len(games) - 1)

        surf = self._surface
        surf.fill(_BG)
        cx = _BROWSE_WIDTH // 2

        # Title
        title = self._font_title.render("JOIN  A  GAME", True, _TITLE_COLOR)
        surf.blit(title, title.get_rect(center=(cx, 36)))

        sub = self._font_hint.render(
            "Searching for LAN games\u2026  \u2191 \u2193 select  \u2022  ENTER to join",
            True,
            _HINT_COLOR,
        )
        surf.blit(sub, sub.get_rect(center=(cx, 68)))

        pygame.draw.line(surf, _DIVIDER_COLOR, (40, 88), (_BROWSE_WIDTH - 40, 88), 1)

        self._row_rects.clear()

        if not games:
            dots = "." * ((pygame.time.get_ticks() // 400 % 3) + 1)
            msg = self._font_prompt.render(
                f"Searching for games{dots}", True, _HINT_COLOR
            )
            surf.blit(msg, msg.get_rect(center=(cx, 240)))
            hint = self._font_hint.render(
                "Make sure a host is running on your LAN",
                True,
                _DISABLED_COLOR,
            )
            surf.blit(hint, hint.get_rect(center=(cx, 280)))
        else:
            y = 100
            for i, game in enumerate(games[:_MAX_VISIBLE]):
                is_sel = i == self._cursor
                rect = pygame.Rect(30, y, _BROWSE_WIDTH - 60, _ROW_HT)
                self._row_rects.append(rect)

                bg = _OBSIDIAN if is_sel else _DEEP_ONYX
                pygame.draw.rect(surf, bg, rect, border_radius=_PANEL_RADIUS)
                border = _NEON_CYAN if is_sel else _COLD_STEEL
                pygame.draw.rect(surf, border, rect, 1, border_radius=_PANEL_RADIUS)

                name = self._font_row.render(
                    game.host_name[:25],
                    True,
                    _SELECTED_COLOR if is_sel else _PALE_SILVER,
                )
                surf.blit(name, (rect.left + 14, rect.top + 6))

                info = self._font_row_sm.render(
                    f"{game.host_ip}:{game.port}   "
                    f"Players: {game.players}/{game.max_players}",
                    True,
                    _HINT_COLOR,
                )
                surf.blit(info, (rect.left + 14, rect.top + 30))

                if is_sel:
                    arrow = self._font_row.render("\u25b6", True, _NEON_CYAN)
                    surf.blit(
                        arrow,
                        (rect.left - 22, rect.centery - arrow.get_height() // 2),
                    )

                y += _ROW_HT + 6

        # Footer
        footer_y = _BROWSE_HEIGHT - 40
        pygame.draw.line(
            surf,
            _DIVIDER_COLOR,
            (40, footer_y - 12),
            (_BROWSE_WIDTH - 40, footer_y - 12),
            1,
        )
        footer = self._font_hint.render("ESC  to return to menu", True, _HINT_COLOR)
        surf.blit(footer, footer.get_rect(center=(cx, footer_y + 6)))

        # Scanline wipe
        self._wipe.draw(surf)


# ══════════════════════════════════════════════════════════════════════════════
# LobbyRenderer — 5-slot character lobby
# ══════════════════════════════════════════════════════════════════════════════

_ROLE_LABELS: Final[dict[str, str]] = {
    "pacman": "Pac-Man",
    "ghost_blinky": "Blinky",
    "ghost_pinky": "Pinky",
    "ghost_inky": "Inky",
    "ghost_clyde": "Clyde",
}

_ROLE_COLORS: Final[dict[str, tuple[int, int, int]]] = {
    "pacman": (255, 214, 0),
    "ghost_blinky": (220, 0, 0),
    "ghost_pinky": (255, 182, 255),
    "ghost_inky": (0, 232, 216),
    "ghost_clyde": (240, 160, 32),
}

_LOBBY_WIDTH: Final[int] = 620
_LOBBY_HEIGHT: Final[int] = 620

_SLOT_OPEN: Final = _DIM_STEEL
_SLOT_MINE: Final = _NEON_CYAN
_READY_COLOR: Final = _VIRIDIAN
_NOT_READY_COLOR: Final = _ETHEREAL_TEAL
_START_BG: Final = (0, 114, 127)
_START_BG_HOVER: Final = _NEON_CYAN


class LobbyRenderer:
    """5-slot character lobby with host START GAME button (Principia styled).

    Lifecycle: instantiate → ``handle_event``/``update``/``draw(lobby_data)``
    each frame.
    """

    def __init__(self) -> None:
        if not pygame.get_init():
            pygame.init()
        pygame.font.init()

        self._font_title = pygame.font.SysFont("consolas,monospace", 38, bold=True)
        self._font_label = pygame.font.SysFont("consolas,monospace", 22, bold=True)
        self._font_value = pygame.font.SysFont("consolas,monospace", 18)
        self._font_hint = pygame.font.SysFont("consolas,monospace", 14)
        self._font_prompt = pygame.font.SysFont("consolas,monospace", 22, bold=True)
        self._font_start = pygame.font.SysFont("consolas,monospace", 28, bold=True)

        self._surface: pygame.Surface = pygame.display.set_mode(
            (_LOBBY_WIDTH, _LOBBY_HEIGHT)
        )
        pygame.display.set_caption("Pac-Math  \u2022  Lobby")

        self._roles: list[str] = list(ALL_ROLES)
        self._cursor: int = 0
        self._wants_ready: bool = False
        self._wants_start: bool = False
        self._elapsed: float = 0.0
        self._start_rect: pygame.Rect = pygame.Rect(0, 0, 0, 0)
        self._wipe = _ScanlineWipe()
        self._wipe.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def selected_role(self) -> str:
        return self._roles[self._cursor]

    @property
    def wants_ready(self) -> bool:
        return self._wants_ready

    @property
    def wants_start(self) -> bool:
        return self._wants_start

    def reset_ready(self) -> None:
        self._wants_ready = False

    def reset_start(self) -> None:
        self._wants_start = False

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_UP:
                self._cursor = (self._cursor - 1) % len(self._roles)
            elif event.key == pygame.K_DOWN:
                self._cursor = (self._cursor + 1) % len(self._roles)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._wants_ready = True
            elif event.key == pygame.K_SPACE:
                self._wants_start = True

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._start_rect.collidepoint(mx, my):
                self._wants_start = True

    def update(self, dt: float) -> None:
        self._elapsed += dt
        self._wipe.update(dt * 1000.0)

    def draw(self, lobby_data: dict[str, Any] | None = None) -> None:
        surf = self._surface
        surf.fill(_BG)
        cx = _LOBBY_WIDTH // 2

        is_host = False
        if lobby_data is not None:
            is_host = lobby_data.get("is_host", False)

        # Title
        title = self._font_title.render("CHARACTER  LOBBY", True, _TITLE_COLOR)
        surf.blit(title, title.get_rect(center=(cx, 36)))

        sub = self._font_hint.render(
            "\u2191 \u2193 select role  \u2022  ENTER to pick & ready",
            True,
            _HINT_COLOR,
        )
        surf.blit(sub, sub.get_rect(center=(cx, 68)))

        pygame.draw.line(surf, _DIVIDER_COLOR, (40, 88), (_LOBBY_WIDTH - 40, 88), 1)

        # Slots
        slots_data: dict[str, Any] = {}
        my_id: str | None = None
        if lobby_data is not None:
            slots_data = lobby_data.get("slots", {})
            my_id = lobby_data.get("your_id")

        slot_y = 108
        slot_h = 72
        slot_pad = 8

        for i, role in enumerate(self._roles):
            y = slot_y + i * (slot_h + slot_pad)
            is_cursor = i == self._cursor

            slot_info = slots_data.get(role)
            occupant_name: str | None = None
            occupant_ready = False
            is_mine = False
            if slot_info is not None:
                occupant_name = slot_info.get("name")
                occupant_ready = slot_info.get("ready", False)
                pid = slot_info.get("player_id")
                is_mine = pid is not None and pid == my_id

            # Obsidian panel per slot
            rect = pygame.Rect(30, y, _LOBBY_WIDTH - 60, slot_h)
            if is_mine:
                bg = (18, 36, 36)
            elif is_cursor:
                bg = _OBSIDIAN
            else:
                bg = _DEEP_ONYX
            pygame.draw.rect(surf, bg, rect, border_radius=_PANEL_RADIUS)

            # Border — Neon Cyan mine/cursor, Cold Steel otherwise
            if is_mine or is_cursor:
                border_c = _NEON_CYAN
                border_w = 2
            else:
                border_c = _COLD_STEEL
                border_w = 1
            pygame.draw.rect(
                surf, border_c, rect, border_w, border_radius=_PANEL_RADIUS
            )

            # Role colour swatch
            role_color = _ROLE_COLORS.get(role, (180, 180, 180))
            swatch = pygame.Rect(rect.left + 12, rect.centery - 14, 28, 28)
            pygame.draw.rect(surf, role_color, swatch, border_radius=4)

            # Role label
            label_text = _ROLE_LABELS.get(role, role)
            lbl = self._font_label.render(label_text, True, role_color)
            surf.blit(lbl, (rect.left + 52, rect.top + 10))

            # Status text
            if occupant_name:
                sc = _READY_COLOR if occupant_ready else _NOT_READY_COLOR
                st = f"{occupant_name}  {'READY' if occupant_ready else 'JOINED'}"
                surf.blit(
                    self._font_value.render(st, True, sc),
                    (rect.left + 52, rect.top + 38),
                )
            else:
                surf.blit(
                    self._font_value.render("\u2014 open \u2014", True, _SLOT_OPEN),
                    (rect.left + 52, rect.top + 38),
                )

            # YOU tag
            if is_mine:
                you = self._font_label.render("YOU", True, _SLOT_MINE)
                surf.blit(you, (rect.right - 70, rect.centery - you.get_height() // 2))

            # Cursor arrow
            if is_cursor:
                arrow = self._font_label.render("\u25b6", True, _NEON_CYAN)
                surf.blit(
                    arrow,
                    (rect.left - 22, rect.centery - arrow.get_height() // 2),
                )

        # Status bar
        bar_y = slot_y + len(self._roles) * (slot_h + slot_pad) + 8
        pygame.draw.line(
            surf, _DIVIDER_COLOR, (40, bar_y), (_LOBBY_WIDTH - 40, bar_y), 1
        )

        pc = lobby_data.get("player_count", 0) if lobby_data else 0
        info = self._font_value.render(f"Players: {pc}/5", True, _HINT_COLOR)
        surf.blit(info, info.get_rect(center=(cx, bar_y + 22)))

        # Host START / non-host waiting
        if is_host:
            btn_w, btn_h = 260, 48
            btn_rect = pygame.Rect(cx - btn_w // 2, bar_y + 42, btn_w, btn_h)
            self._start_rect = btn_rect

            mx, my = pygame.mouse.get_pos()
            hover = btn_rect.collidepoint(mx, my)

            bg_color = _START_BG_HOVER if hover else _START_BG
            pygame.draw.rect(surf, bg_color, btn_rect, border_radius=8)
            pygame.draw.rect(surf, _COLD_STEEL, btn_rect, 2, border_radius=8)

            bt = self._font_start.render("START  GAME", True, _DEEP_ONYX)
            surf.blit(bt, bt.get_rect(center=btn_rect.center))

            hint = self._font_hint.render("SPACE or click to start", True, _HINT_COLOR)
            surf.blit(hint, hint.get_rect(center=(cx, bar_y + 100)))
        else:
            alpha = 0.5 + 0.5 * math.sin(self._elapsed * 1.8 * math.tau)
            prompt = self._font_prompt.render(
                "Waiting for host to start\u2026", True, _PROMPT_COLOR
            )
            tmp = pygame.Surface(prompt.get_size(), pygame.SRCALPHA)
            tmp.fill((255, 255, 255, int(alpha * 255)))
            prompt.blit(tmp, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(prompt, prompt.get_rect(center=(cx, bar_y + 60)))

        footer = self._font_hint.render("ESC  to return to menu", True, _HINT_COLOR)
        surf.blit(footer, footer.get_rect(center=(cx, _LOBBY_HEIGHT - 20)))

        # Scanline wipe
        self._wipe.draw(surf)


# ══════════════════════════════════════════════════════════════════════════════
# CharSelectRenderer — 10 s character-selection with live animated preview
# ══════════════════════════════════════════════════════════════════════════════

#: Sprite-sheet geometry (must match ui/renderer.py).
_CHAR_SHEET_ROWS: Final[int] = 4
_CHAR_SHEET_COLS: Final[int] = 5
_CHAR_FRAME_MS: Final[int] = 120

_CSEL_WIDTH: Final[int] = 620
_CSEL_HEIGHT: Final[int] = 640
_PREVIEW_SIZE: Final[int] = 128  # large sprite preview (px)

_CHAR_DIR: Final = resource_path("assets/characters")


def _load_preview_frames(
    sheet_path: Path | None,
    size: int = _PREVIEW_SIZE,
) -> list[pygame.Surface] | None:
    """Load the right-facing row (row 0) from *sheet_path* at *size* px.

    Returns a list of 5 ``Surface`` objects (one per animation frame),
    or ``None`` when the asset cannot be loaded.
    """
    if sheet_path is None or not sheet_path.is_file():
        return None
    try:
        sheet = pygame.image.load(str(sheet_path)).convert_alpha()
    except pygame.error:
        return None

    sw, sh = sheet.get_size()
    tw = sw // _CHAR_SHEET_COLS
    th = sh // _CHAR_SHEET_ROWS
    if tw < 1 or th < 1:
        return None

    frames: list[pygame.Surface] = []
    for c in range(_CHAR_SHEET_COLS):
        sub = sheet.subsurface(pygame.Rect(c * tw, 0, tw, th))
        scaled = pygame.transform.smoothscale(sub, (size, size))
        frames.append(scaled)
    return frames


class CharSelectRenderer:
    """Character-selection screen with animated sprite preview.

    * **Pac-Man** player: left/right arrows cycle through skins, ENTER locks in.
    * **Ghost** player: sees assigned ghost role — future skin selection placeholder.
    * A countdown timer bar shows the remaining selection time.
    * Live preview panel displays the current character as an animated sprite.

    Lifecycle: ``handle_event``/``update``/``draw(data)`` each frame.
    ``data`` is the latest ``char_select_update`` dict from the server
    (or a locally-constructed equivalent for single-player preview).
    """

    def __init__(self) -> None:
        if not pygame.get_init():
            pygame.init()
        pygame.font.init()

        # ── Fonts ────────────────────────────────────────────────────────
        self._font_title = pygame.font.SysFont("consolas,monospace", 38, bold=True)
        self._font_role = pygame.font.SysFont("consolas,monospace", 28, bold=True)
        self._font_label = pygame.font.SysFont("consolas,monospace", 22, bold=True)
        self._font_value = pygame.font.SysFont("consolas,monospace", 22)
        self._font_timer = pygame.font.SysFont("consolas,monospace", 48, bold=True)
        self._font_hint = pygame.font.SysFont("consolas,monospace", 14)
        self._font_player = pygame.font.SysFont("consolas,monospace", 16)

        # ── Display surface ──────────────────────────────────────────────
        self._surface: pygame.Surface = pygame.display.set_mode(
            (_CSEL_WIDTH, _CSEL_HEIGHT)
        )
        pygame.display.set_caption("Pac-Math  \u2022  Character Selection")

        # ── Arrow assets ─────────────────────────────────────────────────
        self._arrow_l: pygame.Surface | None = None
        self._arrow_r: pygame.Surface | None = None
        self._assets_loaded: bool = False

        # ── Character list and preview cache ─────────────────────────────
        self._characters: list[CharacterTheme] = [
            ct for ct in CHARACTER_THEMES if ct.unlocked
        ]
        self._char_index: int = 0
        self._preview_cache: dict[str, list[pygame.Surface] | None] = {}
        self._locked: bool = False

        # ── Animation / timing ───────────────────────────────────────────
        self._elapsed: float = 0.0
        self._wipe = _ScanlineWipe()
        self._wipe.start()

        # ── Public flags (consumed by run-loop) ──────────────────────────
        self._selected_character: str | None = None  # locked-in CharacterTheme name
        self._wants_select: bool = False  # True when user cycles

    # ------------------------------------------------------------------
    # Asset loading
    # ------------------------------------------------------------------

    def _ensure_assets(self) -> None:
        if self._assets_loaded:
            return
        self._assets_loaded = True

        arrow_size = 36
        raw_l = _load_asset("left.png")
        raw_r = _load_asset("right.png")
        if raw_l is not None:
            scaled = pygame.transform.smoothscale(raw_l, (arrow_size, arrow_size))
            self._arrow_l = _tint_surface(scaled, _NEON_CYAN)
        if raw_r is not None:
            scaled = pygame.transform.smoothscale(raw_r, (arrow_size, arrow_size))
            self._arrow_r = _tint_surface(scaled, _NEON_CYAN)

    def _get_preview_frames(self, ct: CharacterTheme) -> list[pygame.Surface] | None:
        """Return cached preview frames for *ct*, loading lazily."""
        if ct.name not in self._preview_cache:
            self._preview_cache[ct.name] = _load_preview_frames(ct.asset_path)
        return self._preview_cache[ct.name]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def selected_character(self) -> str | None:
        """The locked-in ``CharacterTheme`` enum name, or ``None``."""
        return self._selected_character

    @property
    def current_character_name(self) -> str:
        """Name of the currently-highlighted character (for server sync)."""
        return self._characters[self._char_index].name

    @property
    def wants_select(self) -> bool:
        return self._wants_select

    def reset_wants_select(self) -> None:
        self._wants_select = False

    def handle_event(self, event: pygame.event.Event) -> None:
        if self._locked:
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_LEFT:
                self._char_index = (self._char_index - 1) % len(self._characters)
                self._wants_select = True
            elif event.key == pygame.K_RIGHT:
                self._char_index = (self._char_index + 1) % len(self._characters)
                self._wants_select = True
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._locked = True
                self._selected_character = self._characters[self._char_index].name
                self._wants_select = True

    def update(self, dt: float) -> None:
        self._elapsed += dt
        self._wipe.update(dt * 1000.0)

    def draw(
        self,
        data: dict[str, Any] | None = None,
        *,
        my_role: str | None = None,
        local_mode: bool = False,
    ) -> None:
        """Render one frame.

        Parameters
        ----------
        data
            Server ``char_select_update`` dict.  Contains ``timer``,
            ``selections``, ``your_role``, ``available_characters``.
            ``None`` for local single-player preview.
        my_role
            Override for the player's role label (optional).
        local_mode
            ``True`` for the single-player character preview (no timer,
            no other-player list).
        """
        self._ensure_assets()
        surf = self._surface
        surf.fill(_BG)
        cx = _CSEL_WIDTH // 2

        # ── Parse server data ────────────────────────────────────────────
        timer_val: float = 0.0
        role: str = my_role or "pacman"
        selections: dict[str, dict[str, Any]] = {}
        if data is not None:
            timer_val = data.get("timer", 0.0)
            role = data.get("your_role") or role
            selections = data.get("selections", {})
        is_pacman = role == "pacman"

        # ── Title ────────────────────────────────────────────────────────
        title = self._font_title.render("CHARACTER  SELECT", True, _TITLE_COLOR)
        surf.blit(title, title.get_rect(center=(cx, 34)))

        # ── Role banner ──────────────────────────────────────────────────
        role_label = _ROLE_LABELS.get(role, role.upper())
        role_color = _ROLE_COLORS.get(role, _PALE_SILVER)
        role_text = f"YOU  ARE  {role_label.upper()}"
        role_surf = self._font_role.render(role_text, True, role_color)
        surf.blit(role_surf, role_surf.get_rect(center=(cx, 74)))

        pygame.draw.line(surf, _DIVIDER_COLOR, (40, 96), (_CSEL_WIDTH - 40, 96), 1)

        # ── Preview panel (Obsidian) ─────────────────────────────────────
        panel_w = 320
        panel_h = 250
        panel_rect = pygame.Rect(cx - panel_w // 2, 110, panel_w, panel_h)
        pygame.draw.rect(surf, _OBSIDIAN, panel_rect, border_radius=_PANEL_RADIUS)
        border_color = _NEON_CYAN if not self._locked else _VIRIDIAN
        pygame.draw.rect(surf, border_color, panel_rect, 2, border_radius=_PANEL_RADIUS)

        if is_pacman or local_mode:
            # Animated sprite preview
            ct = self._characters[self._char_index]
            frames = self._get_preview_frames(ct)

            if frames is not None:
                frame_idx = (pygame.time.get_ticks() // _CHAR_FRAME_MS) % len(frames)
                preview = frames[frame_idx]
                preview_rect = preview.get_rect(
                    center=(panel_rect.centerx, panel_rect.top + 100)
                )
                surf.blit(preview, preview_rect)
            else:
                # Classic shapes fallback — draw a large yellow circle
                pygame.draw.circle(
                    surf,
                    (255, 214, 0),
                    (panel_rect.centerx, panel_rect.top + 100),
                    48,
                )
                lbl = self._font_hint.render("Classic Shapes", True, _HINT_COLOR)
                surf.blit(
                    lbl, lbl.get_rect(center=(panel_rect.centerx, panel_rect.top + 160))
                )

            # Character name
            name_surf = self._font_label.render(ct.label, True, _VALUE_COLOR)
            surf.blit(
                name_surf,
                name_surf.get_rect(center=(panel_rect.centerx, panel_rect.top + 190)),
            )

            # Pagination dots
            dot_y = panel_rect.top + 220
            total = len(self._characters)
            dot_spacing = 16
            start_x = panel_rect.centerx - (total - 1) * dot_spacing // 2
            for i in range(total):
                dot_col = _NEON_CYAN if i == self._char_index else _DISABLED_COLOR
                pygame.draw.circle(surf, dot_col, (start_x + i * dot_spacing, dot_y), 4)

            # Locked indicator
            if self._locked:
                lock_surf = self._font_label.render(
                    "\u2714  LOCKED IN", True, _VIRIDIAN
                )
                surf.blit(
                    lock_surf,
                    lock_surf.get_rect(
                        center=(panel_rect.centerx, panel_rect.bottom + 16)
                    ),
                )

            # Arrows (outside panel)
            if not self._locked:
                al_x = panel_rect.left - 44
                ar_x = panel_rect.right + 12
                arrow_cy = panel_rect.top + 100
                if self._arrow_l:
                    al_rect = self._arrow_l.get_rect(center=(al_x, arrow_cy))
                    surf.blit(self._arrow_l, al_rect)
                else:
                    MenuRenderer._draw_arrow_triangle(
                        surf, al_x, arrow_cy, facing="left", color=_NEON_CYAN, size=14
                    )
                if self._arrow_r:
                    ar_rect = self._arrow_r.get_rect(center=(ar_x, arrow_cy))
                    surf.blit(self._arrow_r, ar_rect)
                else:
                    MenuRenderer._draw_arrow_triangle(
                        surf, ar_x, arrow_cy, facing="right", color=_NEON_CYAN, size=14
                    )
        else:
            # Ghost player — show assigned ghost colour swatch + name
            ghost_color = _ROLE_COLORS.get(role, (180, 180, 180))
            pygame.draw.circle(
                surf,
                ghost_color,
                (panel_rect.centerx, panel_rect.top + 90),
                44,
            )
            # Ghost "eyes" (simple white circles)
            eye_y = panel_rect.top + 82
            for ex_off in (-14, 14):
                pygame.draw.circle(
                    surf,
                    (255, 255, 255),
                    (panel_rect.centerx + ex_off, eye_y),
                    10,
                )
                pygame.draw.circle(
                    surf,
                    (30, 30, 60),
                    (panel_rect.centerx + ex_off + 3, eye_y + 2),
                    5,
                )
            ghost_label = self._font_label.render(role_label, True, ghost_color)
            surf.blit(
                ghost_label,
                ghost_label.get_rect(center=(panel_rect.centerx, panel_rect.top + 160)),
            )
            placeholder = self._font_hint.render(
                "Ghost skins coming soon\u2026", True, _HINT_COLOR
            )
            surf.blit(
                placeholder,
                placeholder.get_rect(center=(panel_rect.centerx, panel_rect.top + 200)),
            )

        # ── Timer bar (not shown in local mode) ─────────────────────────
        if not local_mode:
            bar_y = panel_rect.bottom + 38
            bar_w = _CSEL_WIDTH - 80
            bar_h = 14
            bar_bg = pygame.Rect(40, bar_y, bar_w, bar_h)
            pygame.draw.rect(surf, _OBSIDIAN, bar_bg, border_radius=4)
            pygame.draw.rect(surf, _COLD_STEEL, bar_bg, 1, border_radius=4)

            # Filled portion
            max_time = 10.0
            fill_ratio = max(0.0, min(1.0, timer_val / max_time))
            if fill_ratio > 0:
                fill_w = int(bar_w * fill_ratio)
                fill_rect = pygame.Rect(40, bar_y, fill_w, bar_h)
                bar_color = _NEON_CYAN if timer_val > 3 else _CRIMSON
                pygame.draw.rect(surf, bar_color, fill_rect, border_radius=4)

            # Timer text
            timer_str = f"{timer_val:.1f}s"
            timer_surf = self._font_timer.render(timer_str, True, _COLD_STEEL)
            surf.blit(
                timer_surf,
                timer_surf.get_rect(center=(cx, bar_y + bar_h + 36)),
            )

            # ── Other players list ───────────────────────────────────────
            list_y = bar_y + bar_h + 70
            pygame.draw.line(
                surf,
                _DIVIDER_COLOR,
                (60, list_y - 8),
                (_CSEL_WIDTH - 60, list_y - 8),
                1,
            )
            for pid, sel in selections.items():
                sel_role = sel.get("role", "")
                sel_name = sel.get("name", pid)
                sel_char = sel.get("character")
                rc = _ROLE_COLORS.get(sel_role, _PALE_SILVER)
                rl = _ROLE_LABELS.get(sel_role, sel_role)
                char_txt = sel_char or "choosing\u2026"
                line = f"{sel_name} \u2022 {rl} \u2022 {char_txt}"
                player_surf = self._font_player.render(line, True, rc)
                surf.blit(player_surf, (50, list_y))
                list_y += 22
        else:
            # Local mode: show "ENTER to confirm" prompt
            prompt_y = panel_rect.bottom + 40
            if not self._locked:
                alpha = 0.5 + 0.5 * math.sin(self._elapsed * 1.8 * math.tau)
                ptxt = self._font_label.render(
                    "\u2190 \u2192  browse  \u2022  ENTER  to confirm",
                    True,
                    _PROMPT_COLOR,
                )
                tmp = pygame.Surface(ptxt.get_size(), pygame.SRCALPHA)
                tmp.fill((255, 255, 255, int(alpha * 255)))
                ptxt.blit(tmp, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                surf.blit(ptxt, ptxt.get_rect(center=(cx, prompt_y)))

        # ── Hints ────────────────────────────────────────────────────────
        if not local_mode:
            hints = (
                "\u2190 \u2192  change character  \u2022  ENTER  to lock in"
                if is_pacman and not self._locked
                else "Waiting for countdown\u2026"
            )
        else:
            hints = "ESC  to return to menu"
        hint_surf = self._font_hint.render(hints, True, _HINT_COLOR)
        surf.blit(hint_surf, hint_surf.get_rect(center=(cx, _CSEL_HEIGHT - 20)))

        # ── Scanline wipe ────────────────────────────────────────────────
        self._wipe.draw(surf)
