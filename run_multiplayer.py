"""run_multiplayer.py — Networked Pac-Math client with LAN discovery.

Flow
----
1. **Browse Games** — a :class:`~engine.discovery.Listener` discovers LAN
   hosts via UDP broadcast.  The :class:`~ui.menu.BrowseGamesRenderer`
   draws the list; the user clicks or presses ENTER to join one.
2. **Lobby** — :class:`~net.client.NetworkClient` connects (WebSocket)
   and sends ``join``.  The :class:`~ui.menu.LobbyRenderer` lets the
   player pick a character slot and ready-up.  Non-hosts see
   "Waiting for host…"; the host sees a START GAME button.
3. **Playing** — the client receives ``state`` snapshots at 60 fps and
   renders them.  Local input is sent as ``direction`` messages.

Launch
------
::

    # Terminal 1 — start the server (e.g. via run_local.py → Pac-Math mode)
    python run_local.py

    # Terminal 2+ — clients discover and join
    python run_multiplayer.py
"""

from __future__ import annotations

import sys
from typing import Any, Final

import pygame

from config.settings import GameSettings
from engine.discovery import Listener
from engine.game_state import GameState
from entities.entity import Direction
from net.client import NetworkClient
from server import DEFAULT_PORT, ROLE_LABELS
from ui.input_handler import InputHandler
from ui.menu import BrowseGamesRenderer, CharSelectRenderer, LobbyRenderer
from ui.renderer import Renderer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SETTINGS: Final = GameSettings(
    fps=60,
    cell_size=22,
    pacman_speed=4.0,
    ghost_speed=3.2,
    starting_lives=3,
)


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------


class _Phase:
    BROWSE = "BROWSE"
    CONNECTING = "CONNECTING"
    LOBBY = "LOBBY"
    CHAR_SELECT = "CHAR_SELECT"
    PLAYING = "PLAYING"
    GAME_OVER = "GAME_OVER"


# ---------------------------------------------------------------------------
# Main game loop
# ---------------------------------------------------------------------------


def main() -> None:
    pygame.init()
    clock = pygame.time.Clock()
    running = True
    phase = _Phase.BROWSE

    # ── Browse Games state ───────────────────────────────────────────
    listener = Listener()
    listener.start()
    browse = BrowseGamesRenderer()

    # ── Networking state ─────────────────────────────────────────────
    net: NetworkClient | None = None
    lobby_renderer: LobbyRenderer | None = None
    char_select_renderer: CharSelectRenderer | None = None
    char_sent: str | None = None

    # ── Game state ───────────────────────────────────────────────────
    renderer: Renderer | None = None
    handler = InputHandler()
    state: GameState | None = None
    role_sent: str | None = None

    def _cleanup_net() -> None:
        nonlocal \
            net, \
            lobby_renderer, \
            renderer, \
            state, \
            role_sent, \
            char_select_renderer, \
            char_sent
        if net is not None:
            net.disconnect()
            net = None
        lobby_renderer = None
        char_select_renderer = None
        renderer = None
        state = None
        role_sent = None
        char_sent = None

    while running:
        dt = clock.tick(_SETTINGS.fps) / 1000.0

        # ==============================================================
        # EVENTS
        # ==============================================================
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break

            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if phase == _Phase.BROWSE:
                    running = False
                    break
                elif phase in (
                    _Phase.CONNECTING,
                    _Phase.LOBBY,
                    _Phase.PLAYING,
                    _Phase.CHAR_SELECT,
                ):
                    _cleanup_net()
                    phase = _Phase.BROWSE
                    browse = BrowseGamesRenderer()
                    listener.start()
                    continue
                elif phase == _Phase.GAME_OVER:
                    _cleanup_net()
                    phase = _Phase.BROWSE
                    browse = BrowseGamesRenderer()
                    listener.start()
                    continue

            # ── BROWSE events ────────────────────────────────────────
            if phase == _Phase.BROWSE:
                browse.handle_event(event)
                if browse.selected_game is not None:
                    game = browse.selected_game
                    listener.stop()

                    net = NetworkClient()
                    net.connect(game.host_ip, game.port, display_name="Player")
                    lobby_renderer = LobbyRenderer()
                    phase = _Phase.CONNECTING
                    continue

            # ── LOBBY events ─────────────────────────────────────────
            elif phase == _Phase.LOBBY:
                assert lobby_renderer is not None and net is not None
                lobby_renderer.handle_event(event)

                # Send role selection when cursor moves
                current_role = lobby_renderer.selected_role
                if current_role != role_sent:
                    net.send_select_role(current_role)
                    role_sent = current_role

                # Ready-up
                if lobby_renderer.wants_ready:
                    net.send_select_role(lobby_renderer.selected_role)
                    net.send_ready()
                    lobby_renderer.reset_ready()

                # Host start
                if lobby_renderer.wants_start:
                    net._schedule({"type": "start_game"})
                    lobby_renderer.reset_start()

            # ── CHAR_SELECT events ───────────────────────────────────
            elif phase == _Phase.CHAR_SELECT:
                assert char_select_renderer is not None and net is not None
                char_select_renderer.handle_event(event)

                if char_select_renderer.wants_select:
                    cur_char = char_select_renderer.current_character_name
                    if cur_char != char_sent:
                        net.send_select_character(cur_char)
                        char_sent = cur_char
                    char_select_renderer.reset_wants_select()

            # ── PLAYING events ───────────────────────────────────────
            elif phase == _Phase.PLAYING:
                if event.type == pygame.KEYDOWN:
                    handler.process_event(event)

            # ── GAME_OVER events ─────────────────────────────────────
            elif phase == _Phase.GAME_OVER:
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        _cleanup_net()
                        phase = _Phase.BROWSE
                        browse = BrowseGamesRenderer()
                        listener.start()

        if not running:
            break

        # ==============================================================
        # UPDATE + RENDER
        # ==============================================================

        if phase == _Phase.BROWSE:
            browse.update(dt)
            browse.draw(listener.games)

        elif phase == _Phase.CONNECTING:
            assert net is not None
            if net.connected:
                phase = _Phase.LOBBY
            elif net.error:
                _cleanup_net()
                phase = _Phase.BROWSE
                browse = BrowseGamesRenderer()
                listener.start()
            else:
                # Draw connecting screen
                surf = pygame.display.get_surface()
                if surf is not None:
                    surf.fill((12, 12, 30))
                    fnt = pygame.font.SysFont("consolas,monospace", 30, bold=True)
                    dots = "." * ((pygame.time.get_ticks() // 500 % 3) + 1)
                    lbl = fnt.render(f"Connecting{dots}", True, (255, 214, 0))
                    w, h = surf.get_size()
                    surf.blit(lbl, lbl.get_rect(center=(w // 2, h // 2)))

        elif phase == _Phase.LOBBY:
            assert net is not None and lobby_renderer is not None
            lobby_renderer.update(dt)
            lobby_data = net.lobby
            lobby_renderer.draw(lobby_data)

            # Server entered CHAR_SELECT phase
            if net.phase == "CHAR_SELECT":
                char_select_renderer = CharSelectRenderer()
                phase = _Phase.CHAR_SELECT

            # Fallback: server jumped straight to PLAYING
            elif net.phase == "PLAYING":
                renderer = Renderer(cell_size=_SETTINGS.cell_size)
                phase = _Phase.PLAYING
                my_role = net.my_role
                if my_role:
                    label = ROLE_LABELS.get(my_role, my_role)
                    pygame.display.set_caption(f"Pac-Math  \u2022  YOU ARE {label}")

        elif phase == _Phase.CHAR_SELECT:
            assert net is not None and char_select_renderer is not None
            char_select_renderer.update(dt)
            cs_data = net.char_select
            char_select_renderer.draw(cs_data, my_role=net.my_role)

            # Server started the game (timer expired or all locked in)
            if net.phase == "PLAYING":
                renderer = Renderer(cell_size=_SETTINGS.cell_size)
                phase = _Phase.PLAYING
                my_role = net.my_role
                if my_role:
                    label = ROLE_LABELS.get(my_role, my_role)
                    pygame.display.set_caption(f"Pac-Math  \u2022  YOU ARE {label}")

        elif phase == _Phase.PLAYING:
            assert net is not None
            # Send input
            intended = handler.consume()
            if intended is not None:
                net.send_input(intended.name)

            # Receive state
            state_dict = net.pop_state()
            if state_dict is not None:
                state = GameState.from_dict(state_dict)

            # Detect game-over
            if net.phase == "GAME_OVER":
                phase = _Phase.GAME_OVER

            # Render
            if state is not None:
                if renderer is None:
                    renderer = Renderer(cell_size=_SETTINGS.cell_size)
                my_role = net.my_role
                renderer.draw(
                    state,
                    role_label=ROLE_LABELS.get(my_role or ""),
                )

        elif phase == _Phase.GAME_OVER:
            if renderer is not None and state is not None:
                renderer.draw(state)
            # Overlay
            surf = pygame.display.get_surface()
            if surf is not None:
                fnt = pygame.font.SysFont("consolas,monospace", 36, bold=True)
                lbl = fnt.render("GAME OVER", True, (220, 60, 60))
                w, h = surf.get_size()
                surf.blit(lbl, lbl.get_rect(center=(w // 2, h // 2)))
                hint = pygame.font.SysFont("consolas,monospace", 16).render(
                    "ENTER to return  •  ESC to quit", True, (160, 160, 160)
                )
                surf.blit(hint, hint.get_rect(center=(w // 2, h // 2 + 40)))

        pygame.display.flip()

    # ── Cleanup ──────────────────────────────────────────────────────
    _cleanup_net()
    listener.stop()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
