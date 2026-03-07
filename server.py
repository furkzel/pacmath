"""server.py — Authoritative WebSocket game server for Pac-Math.

Two phases:

1. **LOBBY** — players connect, pick a character slot, and ready-up.
   The Host's client has a "START GAME" button; the server waits for a
   ``start_game`` message from the host before launching.

2. **PLAYING** — the server owns the true ``GameState``, ticks physics at
   60 FPS, and broadcasts the serialised state to all clients.  Clients
   send only their ``intended_direction``.

Protocol (JSON messages)
------------------------

### Client → Server

* ``{"type": "join", "name": "<display_name>"}``
  → server responds with ``assign`` then broadcasts ``lobby``.
* ``{"type": "select_role", "role": "<pacman|ghost_blinky|...>"}``
  → server validates, updates slot, broadcasts ``lobby``.
* ``{"type": "ready"}``
  → marks player as ready, broadcasts ``lobby``.
* ``{"type": "start_game"}``           ← **host only**
  → transitions to PLAYING; broadcasts ``start``.
* ``{"type": "input", "direction": "LEFT"}``
  → applied next tick.

### Server → Client

* ``{"type": "assign", "player_id": "p1", "is_host": true}``
* ``{"type": "lobby", "your_id": "p1", "phase": "LOBBY", "slots": {...}, "player_count": N}``
* ``{"type": "start", "state": {…}}``
* ``{"type": "state", "state": {…}, "tick": N}``
* ``{"type": "error", "msg": "…"}``
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Final

import websockets
from websockets.asyncio.server import Server, ServerConnection

from config.settings import GameSettings
from engine import ai, physics
from engine.game_state import GameState
from engine.grid import Grid
from entities.entity import Direction, Position
from entities.ghost import Ghost
from entities.pacman import PacMan
from maps.classic import CLASSIC_MAP, GHOST_SPAWNS, PACMAN_SPAWN

# ---------------------------------------------------------------------------
# Constants (exported — other modules import these)
# ---------------------------------------------------------------------------

DEFAULT_PORT: Final[int] = 8765

ALL_ROLES: Final[tuple[str, ...]] = (
    "pacman",
    "ghost_blinky",
    "ghost_pinky",
    "ghost_inky",
    "ghost_clyde",
)

ROLE_LABELS: Final[dict[str, str]] = {
    "pacman": "PAC-MAN",
    "ghost_blinky": "BLINKY",
    "ghost_pinky": "PINKY",
    "ghost_inky": "INKY",
    "ghost_clyde": "CLYDE",
}

_GHOST_NAMES: Final[tuple[str, ...]] = ("blinky", "pinky", "inky", "clyde")

_SETTINGS: Final = GameSettings(
    fps=60,
    cell_size=22,
    pacman_speed=4.0,
    ghost_speed=3.2,
    starting_lives=3,
)


# ---------------------------------------------------------------------------
# Server phases
# ---------------------------------------------------------------------------


class Phase(Enum):
    LOBBY = auto()
    CHAR_SELECT = auto()
    PLAYING = auto()


_CHAR_SELECT_DURATION: Final[float] = 10.0

#: Character theme names available for selection (must match CharacterTheme enum).
_AVAILABLE_CHARACTERS: Final[tuple[str, ...]] = (
    "CLASSIC",
    "NEWTON",
    "THALES",
    "LEIBNIZ",
    "HYPATIA",
)


# ---------------------------------------------------------------------------
# Player record
# ---------------------------------------------------------------------------


@dataclass
class Player:
    id: str
    name: str
    ws: ServerConnection
    is_host: bool = False
    role: str | None = None
    ready: bool = False
    character: str | None = None  # CharacterTheme name selected during CHAR_SELECT


# ---------------------------------------------------------------------------
# Lobby state
# ---------------------------------------------------------------------------


@dataclass
class LobbyState:
    """Tracks connected players and their role selections."""

    players: dict[str, Player] = field(default_factory=dict)
    #: role → player_id or None
    slots: dict[str, str | None] = field(
        default_factory=lambda: {r: None for r in ALL_ROLES}
    )

    def add_player(self, player: Player) -> None:
        self.players[player.id] = player

    def remove_player(self, player_id: str) -> None:
        player = self.players.pop(player_id, None)
        if player is None:
            return
        for role, occupant in self.slots.items():
            if occupant == player_id:
                self.slots[role] = None

    def try_select_role(self, player_id: str, role: str) -> bool:
        if role not in self.slots:
            return False
        # Free any previously-held slot by this player.
        for r, occupant in self.slots.items():
            if occupant == player_id:
                self.slots[r] = None
        if self.slots[role] is not None:
            return False
        self.slots[role] = player_id
        player = self.players.get(player_id)
        if player:
            player.role = role
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "slots": {
                role: {
                    "player_id": pid,
                    "name": self.players[pid].name
                    if pid and pid in self.players
                    else None,
                    "ready": self.players[pid].ready
                    if pid and pid in self.players
                    else False,
                }
                if pid
                else None
                for role, pid in self.slots.items()
            },
            "player_count": len(self.players),
        }


# ---------------------------------------------------------------------------
# Game state factory
# ---------------------------------------------------------------------------


def _build_game_state(lobby: LobbyState) -> GameState:
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
            is_human_controlled=lobby.slots.get(f"ghost_{name}") is not None,
        )
        state.register_entity(ghost)

    return state


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


class PacMathServer:
    """Async WebSocket server with LOBBY + PLAYING phases."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._lobby = LobbyState()
        self._phase = Phase.LOBBY
        self._state: GameState | None = None
        self._tick: int = 0
        self._next_id: int = 1
        self._server: Server | None = None
        self._pending_inputs: dict[str, Direction] = {}
        self._running: bool = True
        self._char_select_timer: float = 0.0
        self._char_select_task: asyncio.Task[None] | None = None

    # ── helpers ───────────────────────────────────────────────────────

    @property
    def player_count(self) -> int:
        return len(self._lobby.players)

    async def _send(self, player: Player, msg: dict[str, Any]) -> None:
        try:
            await player.ws.send(json.dumps(msg))
        except Exception:
            pass

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        raw = json.dumps(msg)
        tasks = [p.ws.send(raw) for p in list(self._lobby.players.values())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_lobby(self) -> None:
        data = self._lobby.to_dict()
        for player in list(self._lobby.players.values()):
            await self._send(
                player,
                {
                    "type": "lobby",
                    "your_id": player.id,
                    "is_host": player.is_host,
                    "your_role": player.role,
                    "phase": self._phase.name,
                    **data,
                },
            )

    # ── role assignment + character selection ─────────────────────────

    async def _assign_roles_and_start_char_select(self) -> None:
        """Randomly assign roles to connected players, enter CHAR_SELECT."""
        players = list(self._lobby.players.values())
        if not players:
            return

        # Reset all slots
        for role in ALL_ROLES:
            self._lobby.slots[role] = None
        for p in players:
            p.role = None
            p.character = None

        # Shuffle players for random assignment
        shuffled = players[:]
        random.shuffle(shuffled)

        # Assign roles in order: pacman first, then ghosts
        available_roles = list(ALL_ROLES)
        for i, player in enumerate(shuffled):
            if i < len(available_roles):
                role = available_roles[i]
                self._lobby.slots[role] = player.id
                player.role = role

        self._phase = Phase.CHAR_SELECT
        self._char_select_timer = _CHAR_SELECT_DURATION

        # Broadcast role assignments to all players
        all_roles_map = {p.id: p.role for p in players if p.role}
        for player in players:
            await self._send(
                player,
                {
                    "type": "role_assigned",
                    "your_role": player.role,
                    "all_roles": all_roles_map,
                },
            )

        # Broadcast initial char_select state
        await self._broadcast_char_select()

        # Start countdown
        self._char_select_task = asyncio.ensure_future(self._char_select_countdown())
        print(
            f"[server] Roles assigned, character selection started "
            f"({_CHAR_SELECT_DURATION}s)"
        )

    async def _broadcast_char_select(self) -> None:
        """Send current character-selection state to every player."""
        selections: dict[str, dict[str, Any]] = {}
        for p in self._lobby.players.values():
            if p.role:
                selections[p.id] = {
                    "role": p.role,
                    "character": p.character,
                    "name": p.name,
                }

        for player in list(self._lobby.players.values()):
            await self._send(
                player,
                {
                    "type": "char_select_update",
                    "your_id": player.id,
                    "your_role": player.role,
                    "timer": round(self._char_select_timer, 1),
                    "selections": selections,
                    "available_characters": list(_AVAILABLE_CHARACTERS),
                },
            )

    async def _char_select_countdown(self) -> None:
        """10-second countdown → auto-assign → start game."""
        interval = 0.5  # broadcast every 0.5 s
        while self._char_select_timer > 0 and self._phase == Phase.CHAR_SELECT:
            await asyncio.sleep(interval)
            self._char_select_timer = max(0.0, self._char_select_timer - interval)
            if self._phase == Phase.CHAR_SELECT:
                await self._broadcast_char_select()

        if self._phase != Phase.CHAR_SELECT:
            return  # phase changed externally (e.g. all disconnected)

        # Auto-assign character to Pac-Man player if they didn't choose
        for p in self._lobby.players.values():
            if p.role == "pacman" and not p.character:
                p.character = random.choice(_AVAILABLE_CHARACTERS)

        # Broadcast final selections
        final: dict[str, dict[str, Any]] = {}
        for p in self._lobby.players.values():
            if p.role:
                final[p.id] = {
                    "role": p.role,
                    "character": p.character,
                    "name": p.name,
                }
        await self._broadcast(
            {
                "type": "char_select_end",
                "selections": final,
            }
        )

        await self._start_game()

    # ── connection handler ────────────────────────────────────────────

    async def _handler(self, ws: ServerConnection) -> None:
        player: Player | None = None
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")

                # ── JOIN ─────────────────────────────────────────────
                if mtype == "join" and player is None:
                    if len(self._lobby.players) >= 5:
                        await ws.send(
                            json.dumps({"type": "error", "msg": "Server full (5/5)"})
                        )
                        return

                    pid = f"p{self._next_id}"
                    self._next_id += 1
                    name = str(msg.get("name", pid))[:20]
                    is_host = len(self._lobby.players) == 0
                    player = Player(id=pid, name=name, ws=ws, is_host=is_host)
                    self._lobby.add_player(player)

                    await self._send(
                        player,
                        {
                            "type": "assign",
                            "player_id": pid,
                            "is_host": is_host,
                        },
                    )

                    label = name
                    tag = " (HOST)" if is_host else ""
                    print(
                        f"[server] {label}{tag} joined  ({len(self._lobby.players)}/5)"
                    )
                    await self._broadcast_lobby()

                    # Auto-trigger role assignment when 5 players connect
                    if len(self._lobby.players) >= 5 and self._phase == Phase.LOBBY:
                        await self._assign_roles_and_start_char_select()

                    continue

                if player is None:
                    await ws.send(
                        json.dumps({"type": "error", "msg": "Send join first"})
                    )
                    continue

                # ── LOBBY messages ───────────────────────────────────
                if self._phase == Phase.LOBBY:
                    if mtype == "select_role":
                        role = str(msg.get("role", ""))
                        ok = self._lobby.try_select_role(player.id, role)
                        if not ok:
                            await self._send(
                                player,
                                {
                                    "type": "error",
                                    "msg": f"Role '{role}' unavailable.",
                                },
                            )
                        await self._broadcast_lobby()

                    elif mtype == "ready":
                        if player.role is not None:
                            player.ready = True
                            await self._broadcast_lobby()
                        else:
                            await self._send(
                                player,
                                {
                                    "type": "error",
                                    "msg": "Select a role first.",
                                },
                            )

                    elif mtype == "start_game":
                        if not player.is_host:
                            await self._send(
                                player,
                                {
                                    "type": "error",
                                    "msg": "Only the host can start.",
                                },
                            )
                        else:
                            await self._assign_roles_and_start_char_select()

                # ── CHAR_SELECT messages ─────────────────────────────
                elif self._phase == Phase.CHAR_SELECT:
                    if mtype == "select_character":
                        char_name = str(msg.get("character", ""))
                        if char_name in _AVAILABLE_CHARACTERS:
                            player.character = char_name
                            await self._broadcast_char_select()
                        else:
                            await self._send(
                                player,
                                {
                                    "type": "error",
                                    "msg": f"Character '{char_name}' unavailable.",
                                },
                            )

                # ── PLAYING messages ─────────────────────────────────
                elif self._phase == Phase.PLAYING:
                    if mtype == "input":
                        dir_name = str(msg.get("direction", "NONE"))
                        try:
                            direction = Direction[dir_name]
                        except KeyError:
                            direction = Direction.NONE
                        self._pending_inputs[player.id] = direction

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if player is not None:
                self._lobby.remove_player(player.id)
                self._pending_inputs.pop(player.id, None)
                print(
                    f"[server] {player.name} disconnected  "
                    f"({len(self._lobby.players)}/5)"
                )
                if self._phase == Phase.LOBBY:
                    await self._broadcast_lobby()

    # ── game start + tick loop ────────────────────────────────────────

    async def _start_game(self) -> None:
        self._phase = Phase.PLAYING
        self._state = _build_game_state(self._lobby)
        self._tick = 0

        # Build character selections map for clients
        char_selections: dict[str, str | None] = {}
        for p in self._lobby.players.values():
            if p.role:
                char_selections[p.role] = p.character

        await self._broadcast(
            {
                "type": "start",
                "state": self._state.to_dict(),
                "character_selections": char_selections,
            }
        )
        print("[server] Game started!")
        asyncio.ensure_future(self._tick_loop())

    async def _tick_loop(self) -> None:
        interval = 1.0 / _SETTINGS.fps

        while self._running and self._phase == Phase.PLAYING and self._state:
            t0 = asyncio.get_event_loop().time()
            state = self._state

            # Apply inputs
            for player in list(self._lobby.players.values()):
                role = player.role
                if role is None:
                    continue
                direction = self._pending_inputs.pop(player.id, None)
                if direction is None:
                    continue
                entity = state.entities.get(role)
                if entity is None:
                    continue
                if isinstance(entity, PacMan):
                    entity.intended_direction = direction
                elif isinstance(entity, Ghost) and entity.is_human_controlled:
                    entity.direction = direction
                    entity.last_intersection_tile = (-1, -1)

            # AI + physics
            if state.is_active:
                ai.tick_wave_timer(state, interval)
                ai.update_ghosts(state)

            pacman = state.entities.get("pacman")
            if isinstance(pacman, PacMan) and not pacman.is_powered_up:
                state.ghost_eat_streak = 0

            physics.update(state, interval)

            # Broadcast
            self._tick += 1
            await self._broadcast(
                {
                    "type": "state",
                    "state": state.to_dict(),
                    "tick": self._tick,
                }
            )

            # Pace
            elapsed = asyncio.get_event_loop().time() - t0
            if interval - elapsed > 0:
                await asyncio.sleep(interval - elapsed)

            # Game over → back to lobby
            if not state.is_active:
                await self._broadcast(
                    {
                        "type": "game_over",
                        "state": state.to_dict(),
                    }
                )
                self._phase = Phase.LOBBY
                for p in self._lobby.players.values():
                    p.ready = False
                    p.character = None
                if self._char_select_task is not None:
                    self._char_select_task.cancel()
                    self._char_select_task = None
                await self._broadcast_lobby()
                print("[server] Game over — returning to lobby.")
                break

    # ── entry points ──────────────────────────────────────────────────

    async def run(self) -> None:
        self._server = await websockets.serve(
            self._handler,
            self._host,
            self._port,
        )
        print(f"[server] Listening on ws://{self._host}:{self._port}")
        print("[server] Waiting for players…")

        try:
            await self._server.wait_closed()
        finally:
            self._running = False

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()


# ---------------------------------------------------------------------------
# Blocking helper (usable from a daemon thread)
# ---------------------------------------------------------------------------


def run_server(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    """Blocking convenience wrapper."""
    server = PacMathServer(host=host, port=port)
    asyncio.run(server.run())


def make_server(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> PacMathServer:
    """Create a server instance without starting it (for player_count access)."""
    return PacMathServer(host=host, port=port)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _port = DEFAULT_PORT
    if "--port" in sys.argv:
        _idx = sys.argv.index("--port")
        if _idx + 1 < len(sys.argv):
            _port = int(sys.argv[_idx + 1])

    run_server("0.0.0.0", _port)
