# PAC-MATH

**The Principia of Darkness**

> *In an age of forbidden equations and spectral geometries, four luminaries
> of mathematics — Sir Isaac Newton, Gottfried Wilhelm Leibniz, Hypatia of
> Alexandria, and Thales of Miletus — find themselves drawn into a labyrinth
> of neon glyphs.  They must consume every luminous pellet before the
> phantoms of ignorance close in.*

PAC-MATH is a **1 v 4 asymmetric LAN multiplayer** reimagining of the
arcade classic, wrapped in a neon-Gothic aesthetic called *The Principia of
Darkness*.  One player controls Pac-Man; four others take on the roles of
ghosts— each named after a legendary mathematician.

---

## Features

| Category | Detail |
|---|---|
| **Asymmetric Multiplayer** | 1 Pac-Man vs. up to 4 Ghost players over LAN via WebSocket. |
| **Zero-Conf Discovery** | UDP broadcast on port `5556` — no manual IP entry required. |
| **Character Selection** | 10-second role-discovery phase with animated sprite preview and live timer bar. Roles assigned randomly if unclaimed. |
| **Multiple Map Themes** | Classic Shapes, Gothic, Ancient Greek, Math, and Abstract tile-sets. |
| **Multiple Character Themes** | Classic, Newton, Thales, Leibniz, and Hypatia sprite sheets. |
| **Ghost AI** | Blinky (chase), Pinky (ambush), Inky (flank), Clyde (scatter) — each with unique targeting. |
| **Neon-Gothic UI** | Deep Onyx backgrounds, Obsidian panels, Neon Cyan accents, scanline-wipe transitions, bracket-flash selectors. |
| **PyInstaller Ready** | `resource_path()` helper resolves assets from `sys._MEIPASS` when frozen. |
| **CI/CD** | GitHub Actions builds a Windows `.exe` on every push and publishes releases on version tags. |

---

## Architecture

```
pacmath/
├── config/          # GameSettings, theme enums, resource_path()
│   └── settings.py
├── engine/          # Pure-logic core (no Pygame)
│   ├── ai.py        # Ghost targeting strategies
│   ├── constants.py # Cell enum, directions
│   ├── discovery.py # UDP LAN discovery
│   ├── game_state.py
│   ├── grid.py
│   └── physics.py
├── entities/        # Entity hierarchy
│   ├── entity.py
│   ├── ghost.py
│   └── pacman.py
├── maps/
│   └── classic.py   # Procedural maze layout
├── net/
│   └── client.py    # WebSocket client helpers
├── ui/              # Pygame rendering (menu, game, overlays)
│   ├── input_handler.py
│   ├── menu.py      # MenuRenderer, BrowseGamesRenderer, LobbyRenderer, CharSelectRenderer
│   └── renderer.py  # In-game drawing layer
├── assets/
│   ├── characters/  # Sprite sheets (hypatia, leibniz, newton, thales)
│   ├── maps/        # Tile-set PNGs (gothic, greek, math)
│   └── ui/          # Logo, arrow sprites
├── server.py        # Authoritative WebSocket game server
├── run_local.py     # Single-player / LAN client entry point
├── run_multiplayer.py
├── main.py
└── requirements.txt
```

The engine and entities layers contain **zero Pygame imports**; all
rendering is isolated behind `ui/renderer.py` and `ui/menu.py`.  The server
is fully authoritative — clients send inputs and receive state snapshots.

---

## Quick Start

### From source

```bash
# Clone and enter the project
git clone https://github.com/<your-org>/pacmath.git
cd pacmath

# Create a virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt

# Launch the game
python run_local.py
```

### Multiplayer (LAN)

1. **Host** — run `python server.py` on one machine.
2. **Players** — run `python run_local.py` on each client, select
   *Browse LAN Games* from the menu, and join the discovered session.
3. The 10-second character-selection phase assigns roles automatically.

### Pre-built executable

Download the latest `PacMath.exe` from the
[**Releases**](../../releases/latest) page — no Python installation
required.

---

## Deployment & CI/CD

A GitHub Actions workflow (`.github/workflows/build.yml`) automates the
release pipeline:

| Trigger | Action |
|---|---|
| Push to `main` | Builds `PacMath.exe` via PyInstaller on `windows-latest` and uploads it as a build artifact. |
| Push a version tag (`v*`) | Same build **plus** creates a GitHub Release with the `.exe` attached and auto-generated release notes. |

### Creating a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow installs Python 3.12, project dependencies, and PyInstaller,
then produces a single-file Windows executable with all assets bundled via
`--add-data "assets;assets"`.  The `resource_path()` helper in
`config/settings.py` ensures the bundled assets are resolved correctly at
runtime through `sys._MEIPASS`.

---

## Controls

| Key | Action |
|---|---|
| Arrow keys / WASD | Move |
| Enter | Confirm menu selection |
| Escape | Pause / Back |

---

## Requirements

- Python 3.10+
- Pygame 2.6+
- websockets 12+

See [requirements.txt](requirements.txt) for the exact specification.

---

## License

This project is provided for educational and personal use.
