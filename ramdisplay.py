"""
RAMDisplay -- System Tray Memory Monitor for Windows 10/11

Hover over the tray icon to see real-time memory metrics
(updated every second) matching Task Manager -> Performance -> Memory.

Right-click for: About, Auto-start, Check for Updates, Exit.

Usage:
    python ramdisplay.py        (with console window)
    pythonw ramdisplay.py       (no console, background)
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading
import time
import urllib.request
import winreg

import pystray
from PIL import Image, ImageDraw, ImageFont


# -----------------------------------------------------------------------
#  App metadata
# -----------------------------------------------------------------------

VERSION = "0.1.3"
AUTHOR = "JohnXu22786"
GITHUB_URL = "https://github.com/JohnXu22786/RAMDisplay"
APP_NAME = "RAMDisplay"

# -----------------------------------------------------------------------
#  Windows API structures
# -----------------------------------------------------------------------

class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.wintypes.DWORD),
        ("ProcessCount", ctypes.wintypes.DWORD),
        ("ThreadCount", ctypes.wintypes.DWORD),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.wintypes.DWORD),
        ("dwMemoryLoad", ctypes.wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


_kernel32 = ctypes.windll.kernel32
_psapi = ctypes.windll.psapi
_user32 = ctypes.windll.user32


def _get_perf_info() -> PERFORMANCE_INFORMATION | None:
    pi = PERFORMANCE_INFORMATION()
    pi.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
    if _psapi.GetPerformanceInfo(ctypes.byref(pi), ctypes.sizeof(pi)):
        return pi
    return None


def _get_mem_status() -> MEMORYSTATUSEX | None:
    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if _kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        return ms
    return None


# -----------------------------------------------------------------------
#  Formatting helper
# -----------------------------------------------------------------------

def _fmt(b: float | int | None, dec: int = 1) -> str:
    """Format bytes into a human-friendly string (B / KB / MB / GB / TB)."""
    if b is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024.0:
            return f"{b:.{dec}f} {unit}"
        b /= 1024.0
    return f"{b:.{dec}f} PB"


# -----------------------------------------------------------------------
#  Memory data collector
# -----------------------------------------------------------------------

def _get_counter(path: str) -> int | None:
    """Query a single PDH performance counter; return integer value or None."""
    try:
        import win32pdh
        q = win32pdh.OpenQuery()
        ctr = win32pdh.AddCounter(q, path)
        win32pdh.CollectQueryData(q)
        _, val = win32pdh.GetFormattedCounterValue(ctr, win32pdh.PDH_FMT_LARGE)
        win32pdh.CloseQuery(q)
        return val
    except Exception:
        return None


def _get_cached_bytes() -> int | None:
    """
    Return the total standby cache size (matching Task Manager's Cached value)
    by summing the three standby priority classes.
    """
    parts = [
        _get_counter(r"\Memory\Standby Cache Reserve Bytes"),
        _get_counter(r"\Memory\Standby Cache Normal Priority Bytes"),
        _get_counter(r"\Memory\Standby Cache Core Bytes"),
    ]
    if any(v is None for v in parts):
        return None
    return sum(parts)


def collect() -> tuple[float, str]:
    """
    Query system memory metrics and return (usage_percent, tooltip_text).

    Tooltip matches Task Manager's Memory view:
      - In use (Compressed)
      - Available
      - Committed (Current / Limit)
      - Cached
      - Paged pool / Non-paged pool
    """
    perf = _get_perf_info()
    mem = _get_mem_status()

    # -- Physical memory ------------------------------------------------
    if perf and perf.PageSize:
        ps = perf.PageSize
        total = perf.PhysicalTotal * ps
        avail = perf.PhysicalAvailable * ps
    elif mem:
        total = mem.ullTotalPhys
        avail = mem.ullAvailPhys
    else:
        total = 0
        avail = 0

    in_use = total - avail
    percent = (in_use / total * 100.0) if total else 0.0

    # -- Build tooltip -------------------------------------------------
    lines: list[str] = []

    # In use (+ compressed, if available)
    use_line = f"In use: {_fmt(in_use)}"
    compressed = _get_counter(r"\Memory\Compressed Memory Count")
    if compressed is not None:
        use_line += f"  (Compressed: {_fmt(compressed)})"
    lines.append(use_line)

    # Available
    lines.append(f"Available: {_fmt(avail)}")

    # Committed
    if perf and perf.PageSize:
        ct = perf.CommitTotal * perf.PageSize
        cl = perf.CommitLimit * perf.PageSize
        lines.append(f"Committed: {_fmt(ct)} / {_fmt(cl)}")

    # Cached  (prefer standby-cache sum, fall back to SystemCache)
    cached = _get_cached_bytes()
    if cached is not None:
        lines.append(f"Cached: {_fmt(cached)}")
    elif perf and perf.PageSize:
        lines.append(f"Cached: {_fmt(perf.SystemCache * perf.PageSize)} (system cache)")

    # Paged pool / Non-paged pool
    if perf and perf.PageSize:
        lines.append(
            f"Paged pool: {_fmt(perf.KernelPaged * perf.PageSize)}  "
            f"Non-paged pool: {_fmt(perf.KernelNonpaged * perf.PageSize)}"
        )

    return percent, "\n".join(lines)


# -----------------------------------------------------------------------
#  Tray icon drawing
# -----------------------------------------------------------------------

def make_icon(percent: float) -> Image.Image:
    """Create a 64x64 RGBA tray icon colored by memory usage."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Color gradient: green < 50%, amber < 80%, red >= 80%
    if percent < 50:
        color = (76, 175, 80)
    elif percent < 80:
        color = (255, 193, 7)
    else:
        color = (244, 67, 54)

    m = 4
    draw.ellipse(
        [m, m, size - m, size - m],
        fill=color + (200,),
        outline=color,
        width=2,
    )

    # Draw usage percentage in the center
    try:
        font = ImageFont.truetype("segoeui.ttf", 20)
    except Exception:
        font = ImageFont.load_default()

    text = f"{int(percent)}%"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2, (size - th) / 2),
        text,
        fill=(255, 255, 255, 255),
        font=font,
    )

    return img


# -----------------------------------------------------------------------
#  Auto-start (registry)
# -----------------------------------------------------------------------

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = APP_NAME


def _get_app_path() -> str:
    """Get the command line to register for auto-start.

    - Frozen exe: just the exe path.
    - Script:    python.exe + script path.
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def is_autostart_enabled() -> bool:
    """Check whether the auto-start registry entry exists and is current."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
        winreg.CloseKey(key)
        return value == _get_app_path()
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool) -> None:
    """Enable or disable auto-start via HKCU Run registry key."""
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
    )
    try:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, _get_app_path())
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


# -----------------------------------------------------------------------
#  Update check (GitHub Releases API)
# -----------------------------------------------------------------------

def _parse_version(v: str) -> tuple[int, ...]:
    """Convert a dotted version string to a comparable tuple."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def _get_latest_version() -> str | None:
    """Fetch the latest release tag from GitHub. Returns None on failure."""
    url = f"{GITHUB_URL}/releases/latest"
    api_url = "https://api.github.com/repos/JohnXu22786/RAMDisplay/releases/latest"
    try:
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": f"{APP_NAME}/{VERSION}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            return tag.lstrip("v")
    except Exception:
        return None


def check_for_updates() -> str | None:
    """Check GitHub for a newer release. Returns the latest version or None."""
    latest = _get_latest_version()
    if latest is None:
        return None
    current = _parse_version(VERSION)
    remote = _parse_version(latest)
    if remote > current:
        return latest
    return None


# -----------------------------------------------------------------------
#  Auto-update (download & self-replace)
# -----------------------------------------------------------------------

_stop_icon: pystray.Icon | None = None  # set by main() for background thread


def _do_update_check() -> None:
    """Background thread: check for update, ask user, download & replace."""
    global _stop_icon
    latest = check_for_updates()
    if latest is None:
        _user32.MessageBoxW(
            0, "You are up to date (v" + VERSION + ").",
            APP_NAME + " - Update Check", 0,
        )
        return

    rc = _user32.MessageBoxW(
        0,
        "A new version is available!\n\n"
        "Current:  v" + VERSION + "\n"
        "Latest:   v" + latest + "\n\n"
        "Download and install now?",
        APP_NAME + " - Update Available",
        0x04 | 0x20 | 0x10000,  # MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND
    )
    if rc != 6:  # IDYES
        return

    # --- Download --------------------------------------------------
    if not getattr(sys, "frozen", False):
        _user32.MessageBoxW(
            0, "Auto-update only works with the .exe version.\n"
               "Please download manually from:\n" + GITHUB_URL + "/releases",
            APP_NAME + " - Update", 0,
        )
        return

    exe_name = APP_NAME + "-v" + latest + ".exe"
    url = (GITHUB_URL + "/releases/download/v" + latest + "/" + exe_name)
    temp_exe = os.path.join(os.environ["TEMP"], exe_name)

    try:
        # Notify: downloading
        _user32.MessageBoxW(
            0, "Downloading " + exe_name + " ...\n\n"
               "The app will restart automatically when done.",
            APP_NAME + " - Downloading", 0,
        )

        urllib.request.urlretrieve(url, temp_exe)

        # Verify the downloaded file exists
        if not os.path.isfile(temp_exe):
            raise RuntimeError("Download failed - file not found")

        current_exe = sys.executable

        # Create update batch script
        bat_path = os.path.join(os.environ["TEMP"], "update_ramdisplay.bat")
        with open(bat_path, "w", encoding="ascii") as f:
            f.write(
                "@echo off\r\n"
                "title Updating RAMDisplay...\r\n"
                ":wait\r\n"
                'tasklist /FI "IMAGENAME eq ' + APP_NAME + '-v*.exe" '
                "2>NUL | find /I /N \"" + APP_NAME + "-\" >NUL\r\n"
                'if "%ERRORLEVEL%"=="0" (\r\n'
                "    timeout /T 1 /NOBREAK >NUL\r\n"
                "    goto wait\r\n"
                ")\r\n"
                'move /Y "' + temp_exe + '" "' + current_exe + '"\r\n'
                'start "" "' + current_exe + '"\r\n'
                "del \"%~f0\"\r\n"
            )

        # Launch the updater (hidden) and exit
        ctypes.windll.kernel32.WinExec(bat_path, 0)  # SW_HIDE
        if _stop_icon:
            _stop_icon.stop()  # triggers exit

    except Exception as e:
        _user32.MessageBoxW(
            0, "Update failed:\n" + str(e),
            APP_NAME + " - Error", 0x10,  # MB_ICONERROR
        )


# -----------------------------------------------------------------------
#  About dialog
# -----------------------------------------------------------------------

def show_about() -> None:
    """Display About dialog (non-blocking, own thread)."""
    threading.Thread(
        target=_user32.MessageBoxW,
        args=(
            0,
            APP_NAME + " v" + VERSION + "\n\n"
            "A lightweight system tray memory monitor\n"
            "for Windows 10 / 11.\n\n"
            "Author: " + AUTHOR + "\n"
            "License: MIT\n"
            + GITHUB_URL + "\n\n"
            "Data refreshes every second.\n"
            "Built with Python, psutil, pystray, Pillow.",
            "About " + APP_NAME + " v" + VERSION,
            0,
        ),
        daemon=True,
    ).start()


# -----------------------------------------------------------------------
#  Entry point
# -----------------------------------------------------------------------

def main() -> None:
    """Create the tray icon, build the menu, start the updater thread."""

    # -- Menu actions -----------------------------------------------
    def _on_about(icon: pystray.Icon) -> None:
        show_about()

    def _on_autostart(icon: pystray.Icon) -> None:
        set_autostart(not is_autostart_enabled())
        icon.update_menu()

    def _on_check_updates(icon: pystray.Icon) -> None:
        global _stop_icon
        _stop_icon = icon
        threading.Thread(target=_do_update_check, daemon=True).start()

    def _on_exit(icon: pystray.Icon) -> None:
        icon.stop()

    # -- Build menu -------------------------------------------------
    menu = pystray.Menu(
        pystray.MenuItem("About", _on_about, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Auto-start with Windows",
            _on_autostart,
            checked=lambda item: is_autostart_enabled(),
        ),
        pystray.MenuItem("Check for Updates", _on_check_updates),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", _on_exit),
    )

    icon = pystray.Icon(APP_NAME, make_icon(0), APP_NAME + " v" + VERSION, menu)

    # -- Background updater thread ----------------------------------
    # Updates icon graphic and tooltip text every second.

    def updater() -> None:
        while True:
            try:
                pct, tip = collect()
                icon.icon = make_icon(pct)
                icon.title = tip
            except Exception:
                pass
            time.sleep(1)

    threading.Thread(target=updater, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if not getattr(sys, "frozen", False):
            raise
