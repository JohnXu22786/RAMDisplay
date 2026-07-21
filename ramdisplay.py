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

VERSION = "0.1.4"
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
    text = f"{int(percent)}%"
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


def _do_update_check() -> None:
    global _stop_icon
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
    try:
        _user32.MessageBoxW(0, "Downloading " + exe_name + " ...\n\n"
                            "The app will restart automatically when done.",
                            APP_NAME + " - Downloading", 0)
        urllib.request.urlretrieve(url, temp_exe)
        if not os.path.isfile(temp_exe):
            raise RuntimeError("Download failed - file not found")
        current_exe = sys.executable
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
        ctypes.windll.kernel32.WinExec(bat_path, 0)
        if _stop_icon:
            _stop_icon.stop()
    except Exception as e:
        _user32.MessageBoxW(0, "Update failed:\n" + str(e),
                            APP_NAME + " - Error", 0x10)


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

    # -- Build window ------------------------------------------------
    root = tk.Tk()
    _memory_panel_window = root
    root.title("Memory")
    root.geometry("380x500")
    root.resizable(False, False)
    root.configure(bg="#1e1e1e")

    def _on_close() -> None:
        global _memory_panel_window
        _memory_panel_window = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))

    # -- Usage bar ---------------------------------------------------
    bar_frame = tk.Frame(root, bg="#1e1e1e")
    bar_frame.pack(fill=tk.X, padx=16, pady=(16, 0))
    bar_canvas = tk.Canvas(bar_frame, height=24, bg="#2d2d2d",
                           highlightthickness=0, bd=0)
    bar_canvas.pack(fill=tk.X)
    bar_handle = bar_canvas.create_rectangle(0, 0, 0, 24, fill="#4caf50", width=0)

    # -- Big percentage ----------------------------------------------
    pct_label = tk.Label(root, text="--%", font=("Segoe UI", 40, "bold"),
                         fg="white", bg="#1e1e1e")
    pct_label.pack(pady=(4, 0))

    # -- In-use / Total ----------------------------------------------
    usage_label = tk.Label(root, text="-- / -- GB",
                           font=("Segoe UI", 11), fg="#cccccc", bg="#1e1e1e")
    usage_label.pack()

    # -- Separator ---------------------------------------------------
    sep = tk.Frame(root, height=1, bg="#3d3d3d")
    sep.pack(fill=tk.X, padx=16, pady=12)

    # -- Details grid ------------------------------------------------
    details = tk.Frame(root, bg="#1e1e1e")
    details.pack(fill=tk.BOTH, padx=20)

    rows = [
        ("In use (Compressed)", ""),
        ("Available", ""),
        ("Committed", ""),
        ("Cached", ""),
        ("Paged pool", ""),
        ("Non-paged pool", ""),
        ("Total", ""),
    ]
    val_labels: list[tk.Label] = []

    for i, (label, _) in enumerate(rows):
        lbl = tk.Label(details, text=label, font=("Segoe UI", 10),
                       fg="#999999", bg="#1e1e1e", anchor="w")
        lbl.grid(row=i, column=0, sticky="w", pady=3)
        val = tk.Label(details, text="...", font=("Segoe UI", 10, "bold"),
                       fg="white", bg="#1e1e1e", anchor="e")
        val.grid(row=i, column=1, sticky="e", pady=3, padx=(20, 0))
        val_labels.append(val)

    # -- Update loop -------------------------------------------------
    def _update_panel() -> None:
        try:
            d = collect()
            pct = d["percent"]

            bar_w = int(bar_canvas.winfo_width() * pct / 100)
            if pct < 50:
                bar_fill = "#4caf50"
            elif pct < 80:
                bar_fill = "#ffc107"
            else:
                bar_fill = "#f44336"
            bar_canvas.itemconfig(bar_handle, fill=bar_fill)
            bar_canvas.coords(bar_handle, 0, 0, max(bar_w, 1), 24)

            pct_label.config(text=f"{int(pct)}%")
            usage_label.config(
                text=f"{d['in_use'] / 1024**3:.1f} / {d['total'] / 1024**3:.1f} GB"
            )

            vals = [
                _fmt(d["in_use"]) + ("  (" + _fmt(d["compressed"]) + ")" if d["compressed"] else ""),
                _fmt(d["available"]),
                _fmt(d["commit_total"]) + " / " + _fmt(d["commit_limit"]) if d["commit_total"] else "N/A",
                _fmt(d["cached"]),
                _fmt(d["paged_pool"]),
                _fmt(d["nonpaged_pool"]),
                _fmt(d["total"]),
            ]
            for i, v in enumerate(vals):
                val_labels[i].config(text=v)
        except Exception:
            pass
        try:
            root.after(1000, _update_panel)
        except Exception:
            pass

    _update_panel()
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
