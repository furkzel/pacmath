"""client.py — Async WebSocket client for Pac-Math LAN play.

Runs a background asyncio event-loop in a daemon thread so the Pygame main
loop can poll for state updates without blocking.

Usage from the game loop::

    client = NetworkClient()
    client.connect("192.168.1.5", 7865, display_name="Alice")

    # Each frame:
    lobby = client.lobby          # dict or None
    game_state = client.pop_state()  # latest GameState dict or None
    client.send_input(Direction.LEFT)

    # To disconnect:
    client.disconnect()
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Final

import websockets

from server import DEFAULT_PORT

# ---------------------------------------------------------------------------
# Network client
# ---------------------------------------------------------------------------


class NetworkClient:
    """Non-blocking WebSocket client running on a background thread.

    The Pygame main-loop calls light-weight, thread-safe methods to send
    inputs and read the latest server state without ever calling ``await``.
    """

    def __init__(self) -> None:
        # ── Background event-loop ─────────────────────────────────────
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws: Any = None  # websockets client connection

        # ── Thread-safe shared data ───────────────────────────────────
        self._lock = threading.Lock()
        self._lobby: dict[str, Any] | None = None
        self._latest_state: dict[str, Any] | None = None
        self._my_id: str | None = None
        self._my_role: str | None = None
        self._phase: str = "LOBBY"
        self._error: str | None = None
        self._connected: bool = False
        self._char_select: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Public properties (thread-safe reads)
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def lobby(self) -> dict[str, Any] | None:
        """Latest lobby snapshot (or ``None`` before the first update)."""
        with self._lock:
            return self._lobby

    @property
    def my_id(self) -> str | None:
        with self._lock:
            return self._my_id

    @property
    def my_role(self) -> str | None:
        with self._lock:
            return self._my_role

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def char_select(self) -> dict[str, Any] | None:
        """Latest character-selection state from the server, or ``None``."""
        with self._lock:
            return self._char_select

    def pop_state(self) -> dict[str, Any] | None:
        """Consume the most recent GameState dict (returns ``None`` if none)."""
        with self._lock:
            s = self._latest_state
            self._latest_state = None
            return s

    def peek_state(self) -> dict[str, Any] | None:
        """Read the most recent GameState dict without consuming it."""
        with self._lock:
            return self._latest_state

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(
        self, host: str, port: int = DEFAULT_PORT, display_name: str = "Player"
    ) -> None:
        """Start background thread → connect → send join message."""
        if self._thread is not None:
            return  # already connected

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(host, port, display_name),
            daemon=True,
        )
        self._thread.start()

    def disconnect(self) -> None:
        """Gracefully shut down the background event-loop and thread."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self._loop = None
        with self._lock:
            self._connected = False
            self._lobby = None
            self._latest_state = None
            self._my_id = None
            self._my_role = None
            self._phase = "LOBBY"
            self._char_select = None

    # ------------------------------------------------------------------
    # Send helpers (fire-and-forget from the Pygame thread)
    # ------------------------------------------------------------------

    def send_select_role(self, role: str) -> None:
        self._schedule({"type": "select_role", "role": role})

    def send_ready(self) -> None:
        self._schedule({"type": "ready"})

    def send_input(self, direction_name: str) -> None:
        """Send the player's intended direction (e.g. ``"LEFT"``)."""
        self._schedule({"type": "input", "direction": direction_name})

    def send_select_character(self, character: str) -> None:
        """Send character selection during CHAR_SELECT phase."""
        self._schedule({"type": "select_character", "character": character})

    def _schedule(self, msg: dict[str, Any]) -> None:
        """Thread-safe: schedule a send on the background loop."""
        loop = self._loop
        ws = self._ws
        if loop is None or ws is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(asyncio.ensure_future, self._async_send(msg))

    async def _async_send(self, msg: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(msg))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background thread internals
    # ------------------------------------------------------------------

    def _run_loop(self, host: str, port: int, name: str) -> None:
        asyncio.set_event_loop(self._loop)
        assert self._loop is not None
        try:
            self._loop.run_until_complete(self._connect_and_listen(host, port, name))
        except Exception:
            pass
        finally:
            with self._lock:
                self._connected = False

    async def _connect_and_listen(self, host: str, port: int, name: str) -> None:
        uri = f"ws://{host}:{port}"
        try:
            async with websockets.connect(uri) as ws:
                self._ws = ws
                with self._lock:
                    self._connected = True
                    self._error = None

                # Send join
                await ws.send(json.dumps({"type": "join", "name": name}))

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    self._handle_message(msg)

        except (OSError, websockets.exceptions.WebSocketException) as exc:
            with self._lock:
                self._error = str(exc)
                self._connected = False

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Dispatch a decoded server message into shared state."""
        t = msg.get("type")

        if t == "lobby":
            with self._lock:
                self._lobby = msg
                self._my_id = msg.get("your_id", self._my_id)
                self._my_role = msg.get("your_role", self._my_role)
                self._phase = msg.get("phase", self._phase)

        elif t == "start":
            with self._lock:
                self._phase = "PLAYING"
                self._latest_state = msg.get("state")

        elif t == "state":
            with self._lock:
                self._latest_state = msg.get("state")

        elif t == "game_over":
            with self._lock:
                self._latest_state = msg.get("state")
                self._phase = "GAME_OVER"

        elif t == "role_assigned":
            with self._lock:
                self._my_role = msg.get("your_role", self._my_role)
                self._phase = "CHAR_SELECT"

        elif t == "char_select_update":
            with self._lock:
                self._char_select = msg
                self._my_id = msg.get("your_id", self._my_id)
                self._my_role = msg.get("your_role", self._my_role)
                self._phase = "CHAR_SELECT"

        elif t == "char_select_end":
            with self._lock:
                self._char_select = msg
                # Phase will flip to PLAYING when the "start" message arrives

        elif t == "error":
            with self._lock:
                self._error = msg.get("msg")
