"""
RAMDisplay -- System Tray Memory Monitor for Windows 10/11

Hover over the tray icon to see real-time memory metrics
(updated every second) matching Task Manager -> Performance -> Memory.

Left-click: Memory usage panel (Win11 Task Manager style)
Right-click: About, Auto-start, Check for Updates, Exit.

Usage:
    python ramdisplay.py
    pythonw ramdisplay.py       (no console)
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

VERSION = "0.1.6"
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
    parts = [
        _get_counter(r"\Memory\Standby Cache Reserve Bytes"),
        _get_counter(r"\Memory\Standby Cache Normal Priority Bytes"),
        _get_counter(r"\Memory\Standby Cache Core Bytes"),
    ]
    if any(v is None for v in parts):
        return None
    return sum(parts)


# -----------------------------------------------------------------------
#  Memory data: returns a dict with all metrics
# -----------------------------------------------------------------------

MemInfo = dict


def collect() -> MemInfo:
    """
    Collect all memory metrics.
    Returns a dict with keys:
      percent, in_use, compressed, available, total,
      commit_total, commit_limit, cached,
      paged_pool, nonpaged_pool, tip
    """
    info: MemInfo = {
        "percent": 0.0,
        "in_use": 0,
        "compressed": None,
        "available": 0,
        "total": 0,
        "commit_total": 0,
        "commit_limit": 0,
        "cached": 0,
        "paged_pool": 0,
        "nonpaged_pool": 0,
        "tip": "",
    }

    perf = _get_perf_info()
    mem = _get_mem_status()

    if perf and perf.PageSize:
        ps = perf.PageSize
        total = perf.PhysicalTotal * ps
        avail = perf.PhysicalAvailable * ps
        info["commit_total"] = perf.CommitTotal * ps
        info["commit_limit"] = perf.CommitLimit * ps
        info["cached"] = perf.SystemCache * ps
        info["paged_pool"] = perf.KernelPaged * ps
        info["nonpaged_pool"] = perf.KernelNonpaged * ps
    elif mem:
        total = mem.ullTotalPhys
        avail = mem.ullAvailPhys
    else:
        total = 0
        avail = 0

    info["total"] = total
    info["available"] = avail
    info["in_use"] = total - avail
    info["percent"] = (info["in_use"] / total * 100.0) if total else 0.0
    info["compressed"] = _get_counter(r"\Memory\Compressed Memory Count")

    standby = _get_cached_bytes()
    if standby is not None:
        info["cached"] = standby

    lines: list[str] = []
    use_line = f"In use: {_fmt(info['in_use'])}"
    if info["compressed"] is not None:
        use_line += f"  (Compressed: {_fmt(info['compressed'])})"
    lines.append(use_line)
    lines.append(f"Available: {_fmt(info['available'])}")
    if info["commit_total"]:
        lines.append(f"Committed: {_fmt(info['commit_total'])} / {_fmt(info['commit_limit'])}")
    if info["cached"]:
        lines.append(f"Cached: {_fmt(info['cached'])}")
    if info["paged_pool"]:
        lines.append(f"Paged pool: {_fmt(info['paged_pool'])}  Non-paged pool: {_fmt(info['nonpaged_pool'])}")
    info["tip"] = "\n".join(lines)
    return info


# -----------------------------------------------------------------------
#  Tray icon drawing -- large centred number, solid background
# -----------------------------------------------------------------------

def make_icon(percent: float) -> Image.Image:
    """Create a 64x64 tray icon: just the percentage number, big & clear."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if percent < 50:
        color = (76, 175, 80)
    elif percent < 80:
        color = (255, 193, 7)
    else:
        color = (244, 67, 54)

    draw.rounded_rectangle(
        [1, 1, size - 2, size - 2], radius=10,
        fill=color + (230,),
    )
    text = f"{int(percent)}"
    try:
        font = ImageFont.truetype("segoeui.ttf", 30)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2, (size - th) / 2),
        text, fill=(255, 255, 255, 255), font=font,
    )
    return img


# -----------------------------------------------------------------------
#  Auto-start (registry)
# -----------------------------------------------------------------------

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = APP_NAME


def _get_app_path() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def is_autostart_enabled() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
        winreg.CloseKey(key)
        return value == _get_app_path()
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool) -> None:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
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
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def _get_latest_version() -> str | None:
    api_url = "https://api.github.com/repos/JohnXu22786/RAMDisplay/releases/latest"
    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": f"{APP_NAME}/{VERSION}",
                     "Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            return tag.lstrip("v")
    except Exception:
        return None


def check_for_updates() -> str | None:
    latest = _get_latest_version()
    if latest is None:
        return None
    if _parse_version(latest) > _parse_version(VERSION):
        return latest
    return None


# -----------------------------------------------------------------------
#  Auto-update (download & self-replace)
# -----------------------------------------------------------------------

_stop_icon: pystray.Icon | None = None

# Shared state between download thread and updater thread
_download_progress: dict = {
    "active": False,
    "version": "",
    "percent": 0,
    "failed": False,
    "error": "",
}


def _do_update_check() -> None:
    global _stop_icon, _download_progress

    latest = check_for_updates()
    if latest is None:
        _user32.MessageBoxW(0, "You are up to date (v" + VERSION + ").",
                            APP_NAME + " - Update Check", 0)
        return

    rc = _user32.MessageBoxW(
        0,
        "A new version is available!\n\n"
        "Current:  v" + VERSION + "\nLatest:   v" + latest + "\n\n"
        "Download and install now?",
        APP_NAME + " - Update Available",
        0x04 | 0x20 | 0x10000,
    )
    if rc != 6:
        return

    if not getattr(sys, "frozen", False):
        _user32.MessageBoxW(
            0, "Auto-update only works with the .exe version.\n"
               "Please download manually from:\n" + GITHUB_URL + "/releases",
            APP_NAME + " - Update", 0)
        return

    exe_name = APP_NAME + "-v" + latest + ".exe"
    url = GITHUB_URL + "/releases/download/v" + latest + "/" + exe_name
    temp_exe = os.path.join(os.environ["TEMP"], exe_name)

    # ---- Download with progress + auto-retry ----
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        _download_progress["active"] = True
        _download_progress["version"] = latest
        _download_progress["percent"] = 0
        _download_progress["failed"] = False
        _download_progress["error"] = ""

        if attempt > 1:
            # Set tooltip to show retry
            _download_progress["percent"] = -1  # signal "retrying"

        try:
            # Use urllib with a progress callback
            def _reporthook(count, block_size, total_size):
                if total_size > 0:
                    p = int(count * block_size * 100 / total_size)
                    if p > 100:
                        p = 100
                    _download_progress["percent"] = p
                else:
                    _download_progress["percent"] = 0

            urllib.request.urlretrieve(url, temp_exe, _reporthook)

            # Verify download
            if not os.path.isfile(temp_exe) or os.path.getsize(temp_exe) == 0:
                raise RuntimeError("Downloaded file is empty or missing")

            # Success — proceed to install
            _download_progress["active"] = False
            _install_update(temp_exe, latest)
            return

        except Exception as e:
            _download_progress["active"] = False
            err_msg = str(e)

            if attempt < max_attempts:
                rc = _user32.MessageBoxW(
                    0,
                    "Download failed (attempt " + str(attempt) + "/" + str(max_attempts) + "):\n"
                    + err_msg + "\n\nRetry?",
                    APP_NAME + " - Download Failed",
                    0x04 | 0x10 | 0x10000,  # Yes/No, Error icon
                )
                if rc != 6:  # IDYES
                    return
                # Clean up partial file
                try:
                    os.remove(temp_exe)
                except Exception:
                    pass
            else:
                _user32.MessageBoxW(
                    0,
                    "Download failed after " + str(max_attempts) + " attempts:\n"
                    + err_msg,
                    APP_NAME + " - Update Failed",
                    0x10,
                )
                return


def _install_update(temp_exe: str, version: str) -> None:
    """Create batch updater and trigger self-replace."""
    global _download_progress
    _download_progress["active"] = False

    current_exe = sys.executable
    bat_path = os.path.join(os.environ["TEMP"], "update_ramdisplay.bat")

    try:
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
        ctypes.windll.kernel32.WinExec(bat_path, 0)
        if _stop_icon:
            _stop_icon.stop()
    except Exception as e:
        _user32.MessageBoxW(
            0, "Failed to start updater:\n" + str(e),
            APP_NAME + " - Error", 0x10,
        )


# -----------------------------------------------------------------------
#  About dialog
# -----------------------------------------------------------------------

def show_about() -> None:
    threading.Thread(
        target=_user32.MessageBoxW,
        args=(
            0,
            APP_NAME + " v" + VERSION + "\n\n"
            "A lightweight system tray memory monitor\n"
            "for Windows 10 / 11.\n\n"
            "Author: " + AUTHOR + "\n"
            "License: AGPL-3.0\n"
            + GITHUB_URL + "\n\n"
            "Data refreshes every second.\n"
            "Built with Python, psutil, pystray, Pillow.",
            "About " + APP_NAME + " v" + VERSION,
            0,
        ),
        daemon=True,
    ).start()


# -----------------------------------------------------------------------
#  Memory usage panel (tkinter) -- Win11 Task Manager style
# -----------------------------------------------------------------------

_memory_panel_window = None


def _open_memory_panel() -> None:
    global _memory_panel_window

    if _memory_panel_window is not None:
        try:
            _memory_panel_window.lift()
            _memory_panel_window.focus_force()
            return
        except Exception:
            _memory_panel_window = None

    try:
        import tkinter as tk
    except ImportError:
        show_about()
        return

    BG = "#202020"
    FG = "#ffffff"
    FG_DIM = "#888888"
    FG_VAL = "#ffffff"
    GRAPH_BG = "#0e1621"
    GRAPH_LINE = "#3a9adb"
    GRAPH_GRID = "#2a2a3a"
    BAR_OUTLINE = "#555555"
    COMP_INUSE = "#1a3a5c"
    COMP_CACHED = "#2176ae"
    COMP_FREE = "#101020"

    root = tk.Tk()
    _memory_panel_window = root
    root.title("Memory")
    root.configure(bg=BG)
    root.resizable(False, False)

    # --- Header: "Memory" + total RAM -----------------------------
    hdr = tk.Frame(root, bg=BG)
    hdr.pack(fill=tk.X, padx=16, pady=(12, 0))
    tk.Label(hdr, text="Memory", font=("Segoe UI", 20, "bold"),
             fg=FG, bg=BG).pack(side=tk.LEFT)
    hdr_right = tk.Frame(hdr, bg=BG)
    hdr_right.pack(side=tk.RIGHT)
    total_gb = [0.0]  # mutable; set on first update

    total_lbl = tk.Label(hdr_right, text="-- GB",
                         font=("Segoe UI", 11), fg=FG_DIM, bg=BG)
    total_lbl.pack(side=tk.RIGHT)

    # --- Memory usage subtitle ------------------------------------
    tk.Label(root, text="Memory usage", font=("Segoe UI", 10),
             fg=FG_DIM, bg=BG).pack(anchor="w", padx=16, pady=(8, 2))

    # --- Graph canvas (60-second history) -------------------------
    graph_frame = tk.Frame(root, bg=BAR_OUTLINE, bd=1, relief="solid")
    graph_frame.pack(fill=tk.X, padx=16)

    graph_canvas = tk.Canvas(graph_frame, height=140, bg=GRAPH_BG,
                             highlightthickness=0, bd=0)
    graph_canvas.pack(fill=tk.X, padx=1, pady=1)

    # Labels under graph
    graph_labels = tk.Frame(root, bg=BG)
    graph_labels.pack(fill=tk.X, padx=16)
    tk.Label(graph_labels, text="60 seconds", font=("Segoe UI", 9),
             fg=FG_DIM, bg=BG).pack(side=tk.LEFT)
    tk.Label(graph_labels, text="0", font=("Segoe UI", 9),
             fg=FG_DIM, bg=BG).pack(side=tk.RIGHT)

    # --- Memory composition subtitle ------------------------------
    tk.Label(root, text="Memory composition", font=("Segoe UI", 10),
             fg=FG_DIM, bg=BG).pack(anchor="w", padx=16, pady=(10, 2))

    # --- Composition bar (outlined) -------------------------------
    comp_bar_frame = tk.Frame(root, bg=BAR_OUTLINE, bd=1, relief="solid")
    comp_bar_frame.pack(fill=tk.X, padx=16)
    comp_canvas = tk.Canvas(comp_bar_frame, height=22, bg=COMP_FREE,
                            highlightthickness=0, bd=0)
    comp_canvas.pack(fill=tk.X, padx=1, pady=1)

    # --- Data grid (two columns) ----------------------------------
    data_frame = tk.Frame(root, bg=BG)
    data_frame.pack(fill=tk.X, padx=16, pady=(12, 16))

    val_lbls: dict[str, tk.Label] = {}

    def _make_data_cell(parent, row, col, label_text):
        """Create a label + value cell in the grid."""
        f = tk.Frame(parent, bg=BG)
        f.grid(row=row, column=col, sticky="w", padx=(0, 30), pady=2)
        tk.Label(f, text=label_text, font=("Segoe UI", 9),
                 fg=FG_DIM, bg=BG, anchor="w").pack(anchor="w")
        val = tk.Label(f, text="--", font=("Segoe UI", 12, "bold"),
                       fg=FG_VAL, bg=BG, anchor="w")
        val.pack(anchor="w")
        return val

    val_lbls["In use"] = _make_data_cell(data_frame, 0, 0, "In use (Compressed)")
    val_lbls["Available"] = _make_data_cell(data_frame, 0, 1, "Available")
    val_lbls["Committed"] = _make_data_cell(data_frame, 1, 0, "Committed")
    val_lbls["Cached"] = _make_data_cell(data_frame, 1, 1, "Cached")
    val_lbls["Paged pool"] = _make_data_cell(data_frame, 2, 0, "Paged pool")
    val_lbls["Non-paged pool"] = _make_data_cell(data_frame, 2, 1, "Non-paged pool")

    # --- History buffer -------------------------------------------
    history: list[float] = [0.0] * 60

    # --- Close handler --------------------------------------------
    def _on_close() -> None:
        global _memory_panel_window
        _memory_panel_window = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))

    # --- Draw graph -----------------------------------------------
    def _draw_graph():
        graph_canvas.delete("all")
        w = graph_canvas.winfo_width()
        h = graph_canvas.winfo_height()
        if w < 2:
            w = 450
            h = 140

        # Grid lines (horizontal at 25, 50, 75, 100%)
        for i in range(1, 5):
            y = h - h * i / 4
            graph_canvas.create_line(0, y, w, y, fill=GRAPH_GRID, dash=(2, 4))

        # Build polygon points
        points: list[tuple[float, float]] = []
        for i, val in enumerate(history):
            x = w * i / max(len(history) - 1, 1)
            y = h - h * min(val, 100) / 100
            points.append((x, y))

        if len(points) >= 2:
            # Filled area
            poly = [(0, h)] + points + [(w, h)]
            flat = []
            for p in poly:
                flat.extend(p)
            graph_canvas.create_polygon(flat, fill=GRAPH_LINE, outline="",
                                        stipple="gray25")
            # Line on top
            line_flat = []
            for p in points:
                line_flat.extend(p)
            graph_canvas.create_line(line_flat, fill=GRAPH_LINE, width=2)

    # --- Draw composition bar -------------------------------------
    def _draw_comp_bar(d: MemInfo):
        comp_canvas.delete("all")
        w = comp_canvas.winfo_width()
        h = comp_canvas.winfo_height()
        if w < 2:
            w = 450
            h = 22

        total = d["total"]
        if total == 0:
            return

        inuse_w = w * d["in_use"] / total
        cached_w = w * d["cached"] / total

        # In use segment
        comp_canvas.create_rectangle(0, 0, max(inuse_w, 1), h,
                                     fill=COMP_INUSE, outline="")
        # Cached segment
        comp_canvas.create_rectangle(inuse_w, 0, inuse_w + cached_w, h,
                                     fill=COMP_CACHED, outline="")
        # Rest is free (already COMP_FREE background)

        # Thin vertical separator lines
        comp_canvas.create_line(inuse_w, 0, inuse_w, h, fill="#444444", width=1)
        comp_canvas.create_line(inuse_w + cached_w, 0, inuse_w + cached_w, h,
                                fill="#444444", width=1)

    # --- Update loop ----------------------------------------------
    def _update():
        try:
            d = collect()
            pct = d["percent"]
            total_gb[0] = d["total"] / (1024 ** 3)
            total_lbl.config(text=f"{total_gb[0]:.0f} GB")

            # History
            history.append(pct)
            if len(history) > 60:
                history.pop(0)

            _draw_graph()
            _draw_comp_bar(d)

            # Data values
            inuse_val = _fmt(d["in_use"])
            if d["compressed"]:
                inuse_val += " (" + _fmt(d["compressed"]) + ")"
            val_lbls["In use"].config(text=inuse_val)
            val_lbls["Available"].config(text=_fmt(d["available"]))
            val_lbls["Committed"].config(
                text=_fmt(d["commit_total"]) + " / " + _fmt(d["commit_limit"])
            )
            val_lbls["Cached"].config(text=_fmt(d["cached"]))
            val_lbls["Paged pool"].config(text=_fmt(d["paged_pool"]))
            val_lbls["Non-paged pool"].config(text=_fmt(d["nonpaged_pool"]))

        except Exception:
            pass
        try:
            root.after(1000, _update)
        except Exception:
            pass

    _update()
    root.mainloop()


# -----------------------------------------------------------------------
#  Entry point
# -----------------------------------------------------------------------

def main() -> None:
    """Create the tray icon, build the menu, start the updater thread."""

    def _on_open_panel(icon: pystray.Icon) -> None:
        threading.Thread(target=_open_memory_panel, daemon=True).start()

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

    # "default=True" -> triggered by left-click on the tray icon
    menu = pystray.Menu(
        pystray.MenuItem("Memory Panel", _on_open_panel, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("About", _on_about),
        pystray.MenuItem("Auto-start with Windows",
                         _on_autostart,
                         checked=lambda item: is_autostart_enabled()),
        pystray.MenuItem("Check for Updates", _on_check_updates),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", _on_exit),
    )

    icon = pystray.Icon(APP_NAME, make_icon(0), APP_NAME + " v" + VERSION, menu)

    # -- Background updater thread ---------------------------------
    def updater() -> None:
        while True:
            try:
                dp = _download_progress
                if dp["active"]:
                    if dp["percent"] < 0:
                        icon.title = "Retrying download of v" + dp["version"] + " ..."
                    else:
                        icon.title = (
                            "Downloading v" + dp["version"] + " ... "
                            + str(dp["percent"]) + "%"
                        )
                else:
                    d = collect()
                    icon.icon = make_icon(d["percent"])
                    icon.title = d["tip"]
            except Exception:
                pass
            time.sleep(1)

    threading.Thread(target=updater, daemon=True).start()

    # -- Non-blocking startup update check -------------------------
    def _startup_check() -> None:
        try:
            latest = check_for_updates()
            if latest is not None:
                icon.title = (
                    "Update v" + latest + " available!\n"
                    "Right-click -> Check for Updates"
                )
        except Exception:
            pass

    threading.Thread(target=_startup_check, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if not getattr(sys, "frozen", False):
            raise
