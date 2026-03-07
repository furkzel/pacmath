"""engine/discovery.py — UDP LAN discovery for Pac-Math servers.

Provides two non-blocking background-thread utilities:

* :class:`Broadcaster` — the *host* periodically sends a UDP broadcast
  packet every ~1.5 s so nearby clients can discover the game.
* :class:`Listener` — a *client* listens for these broadcast packets
  and maintains a thread-safe list of :class:`AvailableGame` entries.

Protocol
--------
The broadcast payload is a compact JSON object::

    {"name": "Alice's Game", "port": 8765, "players": 2, "max": 5}

Packets are sent to ``<broadcast>:5555`` (``SO_BROADCAST``).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Final

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISCOVERY_PORT: Final[int] = 5555
BROADCAST_INTERVAL: Final[float] = 1.5  # seconds between broadcasts
GAME_TIMEOUT: Final[float] = 5.0  # remove game if no ping for N seconds

_MAGIC: Final[bytes] = b"PACMATH1"  # prefix to filter stray packets


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AvailableGame:
    """A discovered LAN game, updated on every received broadcast."""

    host_name: str
    host_ip: str
    port: int
    players: int = 0
    max_players: int = 5
    last_seen: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Broadcaster (Host side)
# ---------------------------------------------------------------------------


class Broadcaster:
    """Background thread that sends a UDP broadcast every ~1.5 s.

    Usage::

        bc = Broadcaster("Alice's Game", game_port=8765)
        bc.start()
        ...
        bc.stop()
    """

    def __init__(
        self,
        host_name: str = "Pac-Math Game",
        game_port: int = 8765,
        player_count_fn: Any = None,
    ) -> None:
        self._host_name = host_name
        self._game_port = game_port
        self._player_count_fn = player_count_fn  # callable → int
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)

        try:
            while self._running:
                players = 0
                if callable(self._player_count_fn):
                    try:
                        players = int(self._player_count_fn())
                    except Exception:
                        pass

                payload = json.dumps(
                    {
                        "name": self._host_name,
                        "port": self._game_port,
                        "players": players,
                        "max": 5,
                    }
                ).encode("utf-8")

                packet = _MAGIC + payload
                try:
                    sock.sendto(packet, ("<broadcast>", DISCOVERY_PORT))
                except OSError:
                    pass

                # Sleep in small increments so stop() is responsive.
                deadline = time.monotonic() + BROADCAST_INTERVAL
                while self._running and time.monotonic() < deadline:
                    time.sleep(0.25)
        finally:
            sock.close()


# ---------------------------------------------------------------------------
# Listener (Client side)
# ---------------------------------------------------------------------------


class Listener:
    """Background thread that discovers LAN games via UDP broadcasts.

    Usage::

        listener = Listener()
        listener.start()
        games = listener.games   # thread-safe list of AvailableGame
        ...
        listener.stop()
    """

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._games: dict[str, AvailableGame] = {}  # key = "ip:port"

    @property
    def games(self) -> list[AvailableGame]:
        """Return a snapshot of currently-known games (thread-safe)."""
        now = time.monotonic()
        with self._lock:
            # Prune stale entries.
            stale = [
                k for k, g in self._games.items() if now - g.last_seen > GAME_TIMEOUT
            ]
            for k in stale:
                del self._games[k]
            return list(self._games.values())

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        with self._lock:
            self._games.clear()

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            # Port in use — can happen when host + client on same machine.
            # Try with SO_REUSEPORT on platforms that support it.
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # type: ignore[attr-defined]
                sock.bind(("", DISCOVERY_PORT))
            except (OSError, AttributeError):
                return  # give up silently
        sock.settimeout(0.5)

        try:
            while self._running:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not data.startswith(_MAGIC):
                    continue

                try:
                    payload = json.loads(data[len(_MAGIC) :].decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                ip = addr[0]
                port = int(payload.get("port", 8765))
                key = f"{ip}:{port}"

                game = AvailableGame(
                    host_name=str(payload.get("name", "Unknown"))[:30],
                    host_ip=ip,
                    port=port,
                    players=int(payload.get("players", 0)),
                    max_players=int(payload.get("max", 5)),
                    last_seen=time.monotonic(),
                )
                with self._lock:
                    self._games[key] = game
        finally:
            sock.close()
