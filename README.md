# RAMDisplay

System tray memory monitor for Windows 10/11.

Hover over the tray icon to see real-time memory metrics,
updated every second — matching Task Manager -> Performance -> Memory.

![GitHub release](https://img.shields.io/github/v/release/JohnXu22786/RAMDisplay)
![GitHub all releases](https://img.shields.io/github/downloads/JohnXu22786/RAMDisplay/total)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/JohnXu22786/RAMDisplay/release.yml)

---

## Features

- **Real-time monitoring** — memory data refreshes every second
- **Color-coded tray icon** — green (<50%), amber (<80%), red (>=80%) usage indicator with % text
- **Hover tooltip** — shows all key metrics at a glance:
  - In use (with Compressed, if available)
  - Available
  - Committed (current / limit)
  - Cached (standby list total)
  - Paged pool & Non-paged pool
- **Right-click context menu**:
  - **About** — version, author, links
  - **Auto-start with Windows** — toggle via registry (`HKCU\...\Run`)
  - **Check for Updates** — queries GitHub Releases API
- **Auto-update check** — silently checks for new versions on startup
- **Zero configuration** — just run it

## Usage

### Option 1: Download the latest release

Download `RAMDisplay-vX.Y.Z.exe` from the [Releases page](https://github.com/JohnXu22786/RAMDisplay/releases).

Run it directly — no installation needed.

### Option 2: Run from source

```bash
# Install dependencies
pip install -r requirements.txt

# Run (with console window)
python ramdisplay.py

# Run (background, no console)
pythonw ramdisplay.py
```

Right-click the tray icon and select **Exit** to quit.

## Build from source

```bash
python build.py          # build RAMDisplay.exe (current version)
python build.py --bump   # bump patch version, then build
```

The standalone `.exe` will be in the `dist/` folder.

## Automatic CD / Versioning

On every push to `main`, the CI pipeline (GitHub Actions):

1. Auto-bumps the **patch** version in `ramdisplay.py`
2. Commits the version bump back to main
3. Builds `RAMDisplay-vX.Y.Z.exe` with PyInstaller
4. Publishes a **GitHub Release** with the versioned exe

You can also trigger manually with `minor` or `major` bumps via
the Actions tab -> Build & Release -> Run workflow.

## Requirements

- **OS**: Windows 10 or Windows 11 (64-bit)
- **Python 3.13+** (for source runs)
- Dependencies listed in `requirements.txt`

## Data sources

| Metric | Windows API |
|---|---|
| Physical / Available | `GetPerformanceInfo` / `GlobalMemoryStatusEx` |
| Committed | `GetPerformanceInfo` (CommitTotal / CommitLimit) |
| Cached | Standby cache PDH counters (sum of 3 priority classes) |
| Paged / Non-paged pool | `GetPerformanceInfo` (KernelPaged / KernelNonpaged) |
| Compressed | PDH counter `\Memory\Compressed Memory Count` |

## License

MIT
