"""Input Handler — translates raw Pygame key events into engine Directions.

Architectural contract
----------------------
* This is the only module allowed to import pygame in the UI layer
  (besides run_local.py).
* It returns engine types (:class:`~entities.entity.Direction`) — never
  pygame constants — so the engine remains Pygame-free.

Usage::

    from ui.input_handler import InputHandler

    handler = InputHandler()

    for event in pygame.event.get():
        new_dir = handler.process_event(event)
        if new_dir is not None:
            pacman.direction = new_dir
"""

from __future__ import annotations

import pygame

from entities.entity import Direction

# ---------------------------------------------------------------------------
# Key → Direction mapping table
# ---------------------------------------------------------------------------

_KEY_MAP: dict[int, Direction] = {
    # Arrow keys
    pygame.K_UP: Direction.UP,
    pygame.K_DOWN: Direction.DOWN,
    pygame.K_LEFT: Direction.LEFT,
    pygame.K_RIGHT: Direction.RIGHT,
    # WASD
    pygame.K_w: Direction.UP,
    pygame.K_s: Direction.DOWN,
    pygame.K_a: Direction.LEFT,
    pygame.K_d: Direction.RIGHT,
}


class InputHandler:
    """Processes Pygame events and converts them to engine :class:`~entities.entity.Direction` values.

    Implements a **one-frame input buffer**: the last direction requested
    since the previous :meth:`consume` call is stored, so no keypress is
    silently dropped if the player pressed a key between engine ticks.

    Usage in the game loop::

        handler = InputHandler()
        ...
        for event in pygame.event.get():
            handler.process_event(event)

        intended = handler.consume()   # None if no key pressed
        if intended is not None:
            pacman.direction = intended
    """

    def __init__(self) -> None:
        self._buffered: Direction | None = None

    def process_event(self, event: pygame.event.Event) -> Direction | None:
        """Inspect one Pygame event and update the internal buffer.

        Args:
            event: A :class:`pygame.event.Event` instance from the event queue.

        Returns:
            The :class:`Direction` if this event was a recognised direction
            key press, otherwise ``None``.
        """
        if event.type != pygame.KEYDOWN:
            return None
        direction = _KEY_MAP.get(event.key)
        if direction is not None:
            self._buffered = direction
        return direction

    def consume(self) -> Direction | None:
        """Return and clear the buffered direction.

        Returns:
            The most recently buffered :class:`Direction`, or ``None`` if no
            direction key has been pressed since the last call.
        """
        direction, self._buffered = self._buffered, None
        return direction
