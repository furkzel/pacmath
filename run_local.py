"""run_local.py — Real-time local Pygame game entry point.

Architecture
------------
This file is the *only* place that:
* Knows about Pygame's clock and event system.
* Wires the UI layer to the engine via read-only GameState access.
* Manages the application state machine (MENU → PLAYING → GAME_OVER).
* For LAN multiplayer: launches the WebSocket server (Host) and/or
  connects a ``NetworkClient`` to the lobby/game.

Flow per frame (PLAYING state — Classic local)::

    ┌──────────────┐    dt     ┌───────────────┐    state    ┌──────────┐
    │  pygame.Clock│ ────────► │ physics.update│ ──────────► │ Renderer │
    └──────────────┘           └───────────────┘             └──────────┘
          ▲                           ▲
    events│                   direction│
    ┌──────────────┐           ┌───────────────┐
    │ InputHandler │           │    PacMan     │
    └──────────────┘           └───────────────┘

Flow per frame (PLAYING state — LAN multiplayer)::

    ┌──────────────┐  direction  ┌────────────────┐  state dict  ┌──────────┐
    │ InputHandler │ ──────────► │ NetworkClient  │ ───────────► │ Renderer │
    └──────────────┘             └────────────────┘              └──────────┘
                                        │
                            WebSocket to server.py
                                        │
                               Authoritative GameState

Run:
    python run_local.py
"""

from __future__ import annotations

import enum
import socket
import sys
import threading
from typing import Final

import pygame

from config.settings import GameSettings, ThemeSettings
from engine import ai, physics
from engine.game_state import GameState
from engine.grid import Grid
from entities.entity import Direction, Position
from entities.ghost import Ghost
from entities.pacman import PacMan
from maps.classic import CLASSIC_MAP, GHOST_SPAWNS, PACMAN_SPAWN
from net.client import NetworkClient
from engine.discovery import Broadcaster
from server import DEFAULT_PORT, run_server
from ui.input_handler import InputHandler
from ui.menu import GameMode, LobbyRenderer, MenuRenderer, CharSelectRenderer
from ui.renderer import Renderer

# ---------------------------------------------------------------------------
# Local configuration — override defaults for a comfortable display
# ---------------------------------------------------------------------------

_SETTINGS: Final = GameSettings(
    fps=60,
    cell_size=22,  # 28 cols × 22 px = 616 px wide; 31 rows = 682 px tall
    pacman_speed=4.0,
    ghost_speed=3.2,
    starting_lives=3,
)

_GHOST_NAMES: Final[tuple[str, ...]] = ("blinky", "pinky", "inky", "clyde")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_local_ip() -> str:
    """Best-effort guess of the machine's LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _start_server_thread(port: int = DEFAULT_PORT) -> threading.Thread:
    """Launch the WebSocket server on a daemon thread and return it."""
    t = threading.Thread(target=run_server, args=("0.0.0.0", port), daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Application states
# ---------------------------------------------------------------------------


class AppState(enum.Enum):
    MENU = enum.auto()
    CHAR_SELECT = enum.auto()  # character selection (local or LAN)
    LOBBY = enum.auto()  # LAN character selection
    PLAYING = enum.auto()
    PLAYING_NET = enum.auto()  # LAN game in progress
    PAUSED = enum.auto()
    CONNECTING = enum.auto()  # brief connection handshake
    GAME_OVER = enum.auto()


# ---------------------------------------------------------------------------
# State factory
# ---------------------------------------------------------------------------


def build_state() -> GameState:
    """Construct a fresh :class:`~engine.game_state.GameState` for a new game."""
    grid = Grid(data=[row[:] for row in CLASSIC_MAP])
    state = GameState(
        grid=grid,
        settings=_SETTINGS,
        pacman_spawn=PACMAN_SPAWN,
        ghost_names=_GHOST_NAMES,
        ghost_spawns=tuple(GHOST_SPAWNS),
    )

    pacman = PacMan(
        id="pacman",
        position=Position(row=float(PACMAN_SPAWN[0]), col=float(PACMAN_SPAWN[1])),
        direction=Direction.NONE,
        speed=_SETTINGS.pacman_speed,
        lives=_SETTINGS.starting_lives,
    )
    state.register_entity(pacman)

    for name, (sr, sc) in zip(_GHOST_NAMES, GHOST_SPAWNS):
        ghost = Ghost(
            id=f"ghost_{name}",
            position=Position(row=float(sr), col=float(sc)),
            direction=Direction.NONE,
            speed=_SETTINGS.ghost_speed,
        )
        state.register_entity(ghost)

    return state


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Initialise Pygame and run the application state machine."""
    pygame.init()

    clock = pygame.time.Clock()
    running = True

    # ── Start in MENU state ──────────────────────────────────────────────
    app_state = AppState.MENU
    menu = MenuRenderer()

    # These are created when transitioning MENU → PLAYING
    renderer: Renderer | None = None
    handler: InputHandler | None = None
    state: GameState | None = None
    theme: ThemeSettings | None = None

    # ── Character selection state ────────────────────────────────────────
    char_select_renderer: CharSelectRenderer | None = None
    char_sent: str | None = None  # last character we sent to the server

    # ── Networking state (LAN multiplayer) ───────────────────────────────
    net_client: NetworkClient | None = None
    lobby_renderer: LobbyRenderer | None = None
    server_thread: threading.Thread | None = None
    broadcaster: Broadcaster | None = None
    is_host: bool = False
    role_sent: str | None = None  # last role we sent to the server

    def _cleanup_net() -> None:
        """Disconnect network client and reset networking state."""
        nonlocal \
            net_client, \
            lobby_renderer, \
            server_thread, \
            broadcaster, \
            is_host, \
            role_sent, \
            char_select_renderer, \
            char_sent
        if net_client is not None:
            net_client.disconnect()
            net_client = None
        if broadcaster is not None:
            broadcaster.stop()
            broadcaster = None
        lobby_renderer = None
        server_thread = None
        is_host = False
        role_sent = None
        char_select_renderer = None
        char_sent = None

    while running:
        dt: float = clock.tick(_SETTINGS.fps) / 1000.0

        # ==============================================================
        # EVENT HANDLING
        # ==============================================================
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break

            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if app_state == AppState.PLAYING:
                    app_state = AppState.PAUSED
                    continue
                elif app_state == AppState.PAUSED:
                    app_state = AppState.PLAYING
                    continue
                elif app_state in (
                    AppState.CONNECTING,
                    AppState.LOBBY,
                    AppState.PLAYING_NET,
                    AppState.CHAR_SELECT,
                ):
                    _cleanup_net()
                    app_state = AppState.MENU
                    menu = MenuRenderer()
                    renderer = None
                    handler = None
                    state = None
                    char_select_renderer = None
                    continue
                else:
                    running = False
                    break

            # ── MENU events ──────────────────────────────────────────
            if app_state == AppState.MENU:
                menu.handle_event(event)

                if menu.is_confirmed:
                    theme = menu.theme_settings

                    if menu.game_mode == GameMode.PACMATH:
                        # Host: start server locally and connect
                        import time as _time

                        is_host = True
                        server_thread = _start_server_thread()
                        _time.sleep(0.3)  # give server time to bind

                        # Start UDP broadcaster so clients can discover us
                        def _player_count() -> int:
                            return (
                                net_client.lobby.get("player_count", 0)
                                if net_client and net_client.lobby
                                else 0
                            )

                        broadcaster = Broadcaster(
                            host_name=f"{_get_local_ip()}'s Game",
                            game_port=DEFAULT_PORT,
                            player_count_fn=_player_count,
                        )
                        broadcaster.start()

                        net_client = NetworkClient()
                        net_client.connect(
                            "127.0.0.1", DEFAULT_PORT, display_name="Host"
                        )
                        lobby_renderer = LobbyRenderer()
                        app_state = AppState.CONNECTING
                        continue

                    # Transition: MENU → CHAR_SELECT (local character preview)
                    char_select_renderer = CharSelectRenderer()
                    app_state = AppState.CHAR_SELECT
                    continue

            # ── CHAR_SELECT events (local preview or LAN) ───────────
            elif app_state == AppState.CHAR_SELECT:
                assert char_select_renderer is not None
                char_select_renderer.handle_event(event)

                # In LAN mode, sync character selection to server
                if net_client is not None:
                    if char_select_renderer.wants_select:
                        cur_char = char_select_renderer.current_character_name
                        if cur_char != char_sent:
                            net_client.send_select_character(cur_char)
                            char_sent = cur_char
                        char_select_renderer.reset_wants_select()

                # In local mode, ENTER locks in and starts the game
                if net_client is None and char_select_renderer.selected_character:
                    # Apply the selected character theme
                    from config.settings import CharacterTheme as _CT

                    try:
                        selected_ct = _CT[char_select_renderer.selected_character]
                    except KeyError:
                        selected_ct = _CT.CLASSIC
                    assert theme is not None
                    theme.character_theme = selected_ct

                    renderer = Renderer(
                        cell_size=_SETTINGS.cell_size,
                        tile_sheet_path=theme.map_asset_path,
                        character_theme=theme.character_theme,
                    )
                    handler = InputHandler()
                    state = build_state()
                    char_select_renderer = None
                    app_state = AppState.PLAYING
                    continue

            # ── PLAYING events (Classic local) ───────────────────────
            elif app_state == AppState.PLAYING:
                assert handler is not None and state is not None

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_p:
                        app_state = AppState.PAUSED
                        continue

                    if event.key == pygame.K_r:
                        state = build_state()
                        continue

                    handler.process_event(event)

            # ── PAUSED events ────────────────────────────────────────
            elif app_state == AppState.PAUSED:
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_p:
                        app_state = AppState.PLAYING
                    elif event.key in (pygame.K_m, pygame.K_q):
                        # Return to menu — destroy game state
                        _cleanup_net()
                        app_state = AppState.MENU
                        menu = MenuRenderer()
                        renderer = None
                        handler = None
                        state = None

            # ── LOBBY events (LAN) ───────────────────────────────────
            elif app_state == AppState.LOBBY:
                assert lobby_renderer is not None and net_client is not None
                lobby_renderer.handle_event(event)

                # Send role selection whenever cursor moves to a new role
                current_role = lobby_renderer.selected_role
                if current_role != role_sent:
                    net_client.send_select_role(current_role)
                    role_sent = current_role

                # Send ready when player presses ENTER in lobby
                if lobby_renderer.wants_ready:
                    net_client.send_select_role(lobby_renderer.selected_role)
                    net_client.send_ready()
                    lobby_renderer.reset_ready()

                # Host start game
                if lobby_renderer.wants_start:
                    net_client._schedule({"type": "start_game"})
                    lobby_renderer.reset_start()

            # ── PLAYING_NET events (LAN game) ────────────────────────
            elif app_state == AppState.PLAYING_NET:
                assert handler is not None and net_client is not None
                if event.type == pygame.KEYDOWN:
                    handler.process_event(event)

            # ── GAME_OVER events ─────────────────────────────────────
            elif app_state == AppState.GAME_OVER:
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_r:
                        # Restart into a new local game
                        assert theme is not None
                        _cleanup_net()
                        renderer = Renderer(
                            cell_size=_SETTINGS.cell_size,
                            tile_sheet_path=theme.map_asset_path,
                            character_theme=theme.character_theme,
                        )
                        handler = InputHandler()
                        state = build_state()
                        app_state = AppState.PLAYING
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        # Back to menu
                        _cleanup_net()
                        app_state = AppState.MENU
                        menu = MenuRenderer()
                        renderer = None
                        state = None

            # ── CONNECTING events ────────────────────────────────────
            elif app_state == AppState.CONNECTING:
                pass  # handled in the update section below

        if not running:
            break

        # ==============================================================
        # UPDATE + RENDER
        # ==============================================================

        if app_state == AppState.MENU:
            menu.update(dt)
            menu.draw()

        elif app_state == AppState.CHAR_SELECT:
            assert char_select_renderer is not None
            char_select_renderer.update(dt)
            if net_client is not None:
                # LAN mode — draw with server char_select data
                cs_data = net_client.char_select
                char_select_renderer.draw(cs_data, my_role=net_client.my_role)

                # Server started the game (timer expired)
                if net_client.phase == "PLAYING":
                    assert theme is not None
                    # Determine which character the server assigned
                    from config.settings import CharacterTheme as _CT

                    sel_name = None
                    if cs_data and cs_data.get("selections"):
                        my_id = net_client.my_id
                        for pid, sel in cs_data["selections"].items():
                            if pid == my_id and sel.get("role") == "pacman":
                                sel_name = sel.get("character")
                    if sel_name:
                        try:
                            theme.character_theme = _CT[sel_name]
                        except KeyError:
                            pass

                    renderer = Renderer(
                        cell_size=_SETTINGS.cell_size,
                        tile_sheet_path=theme.map_asset_path,
                        character_theme=theme.character_theme,
                    )
                    handler = InputHandler()
                    char_select_renderer = None
                    app_state = AppState.PLAYING_NET
            else:
                # Local mode — draw with local_mode flag
                char_select_renderer.draw(local_mode=True)

        elif app_state in (AppState.PLAYING, AppState.PAUSED):
            assert state is not None and renderer is not None and handler is not None
            is_paused = app_state == AppState.PAUSED

            if not is_paused:
                # Apply buffered player input
                intended = handler.consume()
                pacman = state.entities.get("pacman")
                if isinstance(pacman, PacMan) and intended is not None:
                    pacman.intended_direction = intended

                # Ghost AI
                if state.is_active:
                    ai.tick_wave_timer(state, dt)
                    ai.update_ghosts(state)

                # Reset ghost-eat streak when power-up expires
                if isinstance(pacman, PacMan) and not pacman.is_powered_up:
                    state.ghost_eat_streak = 0

                # Physics tick
                physics.update(state, dt)

            # Render (always, even when paused)
            renderer.draw(state, paused=is_paused)

            # Check for game-over / level-complete → transition
            if not is_paused and not state.is_active:
                app_state = AppState.GAME_OVER

        elif app_state == AppState.CONNECTING:
            assert net_client is not None and lobby_renderer is not None
            # Poll the client until connected
            if net_client.connected:
                app_state = AppState.LOBBY
            elif net_client.error:
                _cleanup_net()
                app_state = AppState.MENU
                menu = MenuRenderer()
            else:
                # Still connecting — draw a simple waiting screen
                surf = pygame.display.get_surface()
                if surf is not None:
                    surf.fill((12, 12, 30))
                    fnt = pygame.font.SysFont("consolas,monospace", 30, bold=True)
                    dots = "." * ((pygame.time.get_ticks() // 500 % 3) + 1)
                    lbl = fnt.render(f"Connecting{dots}", True, (255, 214, 0))
                    w, h = surf.get_size()
                    surf.blit(lbl, lbl.get_rect(center=(w // 2, h // 2)))
                    if is_host:
                        ip_str = _get_local_ip()
                        ip_fnt = pygame.font.SysFont("consolas,monospace", 16)
                        ip_lbl = ip_fnt.render(
                            f"Your LAN IP: {ip_str}:{DEFAULT_PORT}",
                            True,
                            (180, 180, 180),
                        )
                        surf.blit(ip_lbl, ip_lbl.get_rect(center=(w // 2, h // 2 + 40)))

        elif app_state == AppState.LOBBY:
            assert net_client is not None and lobby_renderer is not None
            lobby_renderer.update(dt)
            lobby_data = net_client.lobby
            lobby_renderer.draw(lobby_data)

            # Auto-transition: server entered CHAR_SELECT phase
            if net_client.phase == "CHAR_SELECT":
                char_select_renderer = CharSelectRenderer()
                app_state = AppState.CHAR_SELECT

            # Fallback: server jumped straight to PLAYING (shouldn't happen
            # with the new flow, but keeps backward compat)
            elif net_client.phase == "PLAYING":
                assert theme is not None
                renderer = Renderer(
                    cell_size=_SETTINGS.cell_size,
                    tile_sheet_path=theme.map_asset_path,
                    character_theme=theme.character_theme,
                )
                handler = InputHandler()
                app_state = AppState.PLAYING_NET

        elif app_state == AppState.PLAYING_NET:
            assert net_client is not None and handler is not None
            # Send local input to the server
            intended = handler.consume()
            if intended is not None:
                net_client.send_input(intended.name)

            # Read the authoritative state from the server
            state_dict = net_client.pop_state()
            if state_dict is not None:
                state = GameState.from_dict(state_dict)

            # Detect game-over
            if net_client.phase == "GAME_OVER":
                app_state = AppState.GAME_OVER

            # Render whatever state we have
            if state is not None and renderer is not None:
                renderer.draw(state)

        elif app_state == AppState.GAME_OVER:
            # Keep rendering the final frame with an overlay
            if renderer is not None and state is not None:
                renderer.draw(state)

        pygame.display.flip()

    _cleanup_net()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
