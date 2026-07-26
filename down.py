#!/usr/bin/env python3
"""
Oryvex Media Downloader — GUI Edition (FULLY FIXED v1.0.0)
============================================================
YouTube · Spotify · SoundCloud · TikTok · Instagram · Twitter/X · Pinterest · RadioJavan

CHANGELOG v1.0.0 (guaranteed cleanup on cancel):
  - Uses a unique temporary filename prefix for each download attempt.
  - On cancellation, all files with that prefix (including .part/.ytdl variants) are deleted.
  - No more "old files" left behind when cancelling.

Run:
    pip install PySide6 requests yt-dlp yt-dlp-ejs beautifulsoup4 instaloader
    python down.py
"""

import importlib
import json
import re
import shutil
import time
import subprocess
import sys
import threading
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

VERSION = "1.0.0-gui"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CONFIG_DIR_NAME = ".oryvex_downloader"

# ══════════════════════════════════════════════════════════════════════════
#  Bootstrap — make sure PySide6 + backend deps exist
# ══════════════════════════════════════════════════════════════════════════

def _ensure(pkg: str, import_name: Optional[str] = None) -> bool:
    name = import_name or pkg
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg],
                           check=True)
            importlib.import_module(name)
            return True
        except Exception:
            return False

def ensure_node_in_path():
    candidate_paths = [
        r"C:\Program Files\nodejs",
        r"C:\Program Files (x86)\nodejs",
        os.path.expanduser("~/AppData/Local/Programs/nodejs"),
        os.path.expanduser("~/AppData/Roaming/nvm/current"),
        os.path.expanduser("~/.deno/bin"),
        os.path.expanduser("~/AppData/Local/deno/bin"),
        os.path.expanduser("~/scoop/shims"),
    ]
    for path in candidate_paths:
        if os.path.exists(path) and path not in os.environ.get("PATH", ""):
            os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


def ensure_bundled_tools_in_path():
    """If running as a frozen EXE, add the EXE's directory to PATH
    so that bundled ffmpeg.exe and deno.exe are found automatically."""
    if getattr(sys, "frozen", False):
        # PyInstaller puts everything in sys._MEIPASS (onefile) 
        # or next to the exe (onedir / COLLECT mode)
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")
        # Also check _MEIPASS for onefile mode
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and meipass not in os.environ.get("PATH", ""):
            os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")

ensure_bundled_tools_in_path()

ensure_node_in_path()

if not getattr(sys, "frozen", False):
    if not _ensure("PySide6"):
        print("FATAL: PySide6 could not be installed automatically.\n"
              "Install it manually with:  pip install PySide6")
        sys.exit(1)
    for _pkg, _mod in [("requests", "requests"), ("yt-dlp", "yt_dlp"),
                        ("yt-dlp-ejs", "yt_dlp_ejs"),
                        ("beautifulsoup4", "bs4"), ("instaloader", "instaloader")]:
        _ensure(_pkg, _mod)

from PySide6.QtCore import QObject, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGraphicsDropShadowEffect, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QRadioButton, QScrollArea, QSizeGrip, QSizePolicy, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QDialog,
    QDialogButtonBox, QGroupBox, QGridLayout
)

try:
    import requests
except ImportError:
    requests = None
try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled
except ImportError:
    yt_dlp = None
    DownloadCancelled = Exception
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
try:
    import instaloader
except ImportError:
    instaloader = None


# ══════════════════════════════════════════════════════════════════════════
#  Global event bus
# ══════════════════════════════════════════════════════════════════════════

class EventBus(QObject):
    message = Signal(str, str)
    progress = Signal(float, float, int, str)
    cookie_state_changed = Signal()

BUS = EventBus()

def print_info(msg: str) -> None:    BUS.message.emit(str(msg), "info")
def print_success(msg: str) -> None: BUS.message.emit(str(msg), "success")
def print_warning(msg: str) -> None: BUS.message.emit(str(msg), "warning")
def print_error(msg: str) -> None:   BUS.message.emit(str(msg), "error")


# ══════════════════════════════════════════════════════════════════════════
#  Logger for yt-dlp
# ══════════════════════════════════════════════════════════════════════════

class SilentLogger:
    _SURFACE_PATTERNS = [
        "no longer valid",
        "sign in to confirm",
        "not a bot",
        "po token",
        "potoken",
        "requires a po token",
        "http error 403",
        "unable to download webpage",
    ]

    def _maybe_surface(self, msg: Any, as_error: bool) -> None:
        text = str(msg)
        low = text.lower()
        if any(p in low for p in self._SURFACE_PATTERNS):
            (print_error if as_error else print_warning)(text)

    def debug(self, msg):
        pass

    def warning(self, msg):
        self._maybe_surface(msg, as_error=False)

    def error(self, msg):
        self._maybe_surface(msg, as_error=True)


def _yt_dlp_quiet_opts() -> Dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": SilentLogger(),
    }


# ══════════════════════════════════════════════════════════════════════════
#  Enums / platform metadata
# ══════════════════════════════════════════════════════════════════════════

class ServiceType(str, Enum):
    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    SOUNDCLOUD = "soundcloud"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    PINTEREST = "pinterest"
    RADIOJAVAN = "radiojavan"
    GENERIC = "generic"


PLATFORM_PATTERNS: Dict[ServiceType, List[str]] = {
    ServiceType.YOUTUBE: [
        r"youtube\.com/watch\?v=", r"youtu\.be/", r"youtube\.com/shorts/",
        r"music\.youtube\.com/watch", r"youtube\.com/playlist\?list=",
        r"youtube\.com/embed/", r"youtube\.com/v/",
    ],
    ServiceType.SPOTIFY: [
        r"open\.spotify\.com/track/", r"open\.spotify\.com/album/",
        r"open\.spotify\.com/playlist/", r"open\.spotify\.com/artist/",
        r"spotify\.link/", r"spotify:track:",
    ],
    ServiceType.SOUNDCLOUD: [
        r"soundcloud\.com/[\w-]+/[\w-]+", r"soundcloud\.com/[\w-]+/sets/",
        r"m\.soundcloud\.com/",
    ],
    ServiceType.TIKTOK: [
        r"tiktok\.com/@\w+/video/", r"vm\.tiktok\.com/", r"tiktok\.com/t/",
    ],
    ServiceType.INSTAGRAM: [
        r"instagram\.com/p/", r"instagram\.com/reel/",
        r"instagram\.com/stories/", r"instagram\.com/tv/", r"instagr\.am/",
    ],
    ServiceType.TWITTER: [
        r"twitter\.com/\w+/status/", r"x\.com/\w+/status/",
        r"mobile\.twitter\.com/\w+/status/", r"twitter\.com/i/spaces/",
    ],
    ServiceType.PINTEREST: [
        r"pinterest\.com/pin/", r"pin\.it/", r"pinterest\.com\.au/pin/",
        r"pinterest\.ca/pin/",
    ],
    ServiceType.RADIOJAVAN: [
        r"radiojavan\.com/mp3/", r"radiojavan\.com/mp4/",
        r"play\.radiojavan\.com/", r"rj\.app/", r"radiojavan\.com/song/",
        r"radiojavan\.com/video/", r"radiojavan\.com/playlist/",
        r"radiojavan\.com/album/", r"radiojavan\.com/podcast/",
    ],
}

SERVICE_EMOJIS = {
    ServiceType.YOUTUBE: "🎬", ServiceType.SPOTIFY: "🎵",
    ServiceType.SOUNDCLOUD: "☁️", ServiceType.TIKTOK: "🎶",
    ServiceType.INSTAGRAM: "📷", ServiceType.TWITTER: "🐦",
    ServiceType.PINTEREST: "📌", ServiceType.RADIOJAVAN: "🎧",
    ServiceType.GENERIC: "🔗",
}
SERVICE_NAMES = {
    ServiceType.YOUTUBE: "YouTube", ServiceType.SPOTIFY: "Spotify",
    ServiceType.SOUNDCLOUD: "SoundCloud", ServiceType.TIKTOK: "TikTok",
    ServiceType.INSTAGRAM: "Instagram", ServiceType.TWITTER: "Twitter / X",
    ServiceType.PINTEREST: "Pinterest", ServiceType.RADIOJAVAN: "RadioJavan",
    ServiceType.GENERIC: "Unrecognized link",
}


def detect_service(url: str) -> ServiceType:
    u = url.lower().strip()
    for svc, patterns in PLATFORM_PATTERNS.items():
        for p in patterns:
            if re.search(p, u):
                return svc
    return ServiceType.GENERIC


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _build_js_runtimes() -> Optional[Dict[str, Dict]]:
    runtimes: Dict[str, Dict] = {}

    deno_path = shutil.which("deno")
    runtimes["deno"] = {"path": deno_path} if deno_path else {"path": None}

    node_path = shutil.which("node")
    if node_path:
        runtimes["node"] = {"path": node_path}

    qjs_path = shutil.which("qjs")
    if qjs_path:
        runtimes["quickjs"] = {"path": qjs_path}

    return runtimes if runtimes else None


# ══════════════════════════════════════════════════════════════════════════
#  Config
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Config:
    download_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    config_dir: Path = field(default_factory=lambda: Path.home() / CONFIG_DIR_NAME)
    cookie_dir: Path = field(default_factory=lambda: Path.home() / CONFIG_DIR_NAME / "cookies")
    max_concurrent: int = 3
    retry_count: int = 3
    timeout: int = 30
    audio_only: bool = False
    no_playlist: bool = False
    embed_metadata: bool = True
    write_thumbnail: bool = True
    verbose: bool = False
    preferred_quality: str = "best"
    use_browser_cookies: bool = False
    browser_name: str = "chrome"

    def __post_init__(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_dir.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════
#  Cookie manager
# ══════════════════════════════════════════════════════════════════════════

class CookieManager:
    def __init__(self, config: Config):
        self.config = config
        self.cookie_dir = config.cookie_dir
        self.cookie_dir.mkdir(parents=True, exist_ok=True)

    def get_cookie_path(self, service: ServiceType = ServiceType.YOUTUBE) -> Path:
        return self.cookie_dir / f"{service.value}_cookies.txt"

    def has_cookies(self, service: ServiceType = ServiceType.YOUTUBE) -> bool:
        p = self.get_cookie_path(service)
        return p.exists() and p.stat().st_size > 0

    def is_ready(self) -> bool:
        return self.config.use_browser_cookies or self.has_cookies()

    def validate_cookies(self, service: ServiceType = ServiceType.YOUTUBE) -> bool:
        if yt_dlp is None:
            print_error("yt-dlp not installed")
            return False
        cookie_path = self.get_cookie_path(service)
        if not cookie_path.exists() or cookie_path.stat().st_size == 0:
            return False
        opts: Dict[str, Any] = {
            **_yt_dlp_quiet_opts(),
            "cookiefile": str(cookie_path),
            "socket_timeout": 30, "http_headers": {"User-Agent": USER_AGENT},
            "extract_flat": True, "playlistend": 1,
            "extractor_args": {"youtube": {"player_client": ["web"]}},
        }
        js_rt = _build_js_runtimes()
        if js_rt:
            opts["js_runtimes"] = js_rt
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info("ytsearch1:test", download=False)
                if info and info.get("entries"):
                    print_success("Cookies validated successfully")
                    return True
                print_error("Cookies appear invalid (no search results).")
                return False
        except Exception as e:
            msg = str(e).lower()
            if "no longer valid" in msg:
                print_error("Cookies have expired / rotated by YouTube. Export a fresh cookies.txt.")
            elif "sign in" in msg or "login" in msg or "bot" in msg:
                print_error("Cookies require login or have expired.")
            else:
                print_error(f"Cookie validation failed: {e}")
            return False

    def ensure_ready(self, service: ServiceType = ServiceType.YOUTUBE) -> bool:
        if self.config.use_browser_cookies:
            return True
        if self.has_cookies(service):
            if self.validate_cookies(service):
                return True
            self.delete_cookies(service)
        return False

    def delete_cookies(self, service: ServiceType = ServiceType.YOUTUBE) -> bool:
        p = self.get_cookie_path(service)
        if p.exists():
            p.unlink()
            print_info(f"Deleted cookies: {p}")
            BUS.cookie_state_changed.emit()
            return True
        return False

    def full_reset(self) -> bool:
        try:
            if self.config.config_dir.exists():
                shutil.rmtree(self.config.config_dir)
        except Exception as e:
            print_error(f"Failed to delete {self.config.config_dir}: {e}")
            return False
        self.config.config_dir.mkdir(parents=True, exist_ok=True)
        self.config.cookie_dir.mkdir(parents=True, exist_ok=True)
        print_success(f"Deleted and recreated: {self.config.config_dir}")
        BUS.cookie_state_changed.emit()
        return True

    def get_cookies(self, service: ServiceType = ServiceType.YOUTUBE) -> Optional[Path]:
        return self.get_cookie_path(service) if self.has_cookies(service) else None

    def import_cookie_file(self, file_path: Path,
                           service: ServiceType = ServiceType.YOUTUBE) -> bool:
        if not file_path.exists():
            print_error(f"File not found: {file_path}")
            return False
        dest = self.get_cookie_path(service)
        try:
            shutil.copy2(file_path, dest)
            print_info(f"Imported cookies to {dest}")
            print_info("Validating cookies...")
            if self.validate_cookies(service):
                print_success("Cookies are valid.")
                BUS.cookie_state_changed.emit()
                return True
            print_error("Cookie file appears invalid or expired.")
            dest.unlink()
            BUS.cookie_state_changed.emit()
            return False
        except Exception as e:
            print_error(f"Failed to import: {e}")
            return False

    def cookie_location_text(self, service: ServiceType = ServiceType.YOUTUBE) -> str:
        p = self.get_cookie_path(service)
        if p.exists():
            return f"{p}  ({p.stat().st_size} bytes)"
        return f"{p}  (does not exist)"


# ══════════════════════════════════════════════════════════════════════════
#  Base downloader
# ══════════════════════════════════════════════════════════════════════════

class Downloader:
    def __init__(self, config: Config, cookie_manager: Optional[CookieManager] = None):
        self.config = config
        self.cookie_manager = cookie_manager
        self._last_progress = -1.0
        self.progress_label = ""
        self._cancel_event = threading.Event()
        self._available_formats: Optional[List[Dict]] = None
        self._fatal_error: Optional[str] = None
        self._force_cookiefile: bool = False
        self._is_running = False

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @staticmethod
    def _classify_auth_error(msg: str) -> Optional[str]:
        m = (msg or "").lower()
        if "could not copy" in m and "cookie database" in m:
            return "cookie_lock"
        if "no longer valid" in m:
            return "cookies_expired"
        if "sign in" in m or ("confirm" in m and "bot" in m):
            return "bot_check"
        if "po token" in m or "potoken" in m:
            return "po_token_missing"
        return None

    def _report_fatal_auth_error(self, kind: str) -> None:
        if kind == "cookie_lock" and self.cookie_manager and self.cookie_manager.has_cookies():
            if not self._force_cookiefile:
                self._force_cookiefile = True
                print_warning(
                    "Could not read the browser's cookie database (browser is open) — "
                    "falling back to your saved cookies.txt file instead."
                )
            return

        if kind == "po_token_missing":
            print_warning(
                "YouTube is asking for a PO Token on this video. Regular videos are "
                "gated by this far more aggressively than Shorts — that mismatch is "
                "the most common reason Shorts download at full quality while normal "
                "videos don't. Fresh, valid cookies from a real logged-in browser "
                "session help a lot; full support also benefits from a PO Token "
                "provider plugin such as bgutil-ytdlp-pot-provider."
            )
            return

        if self._fatal_error:
            return
        self._fatal_error = kind
        if kind == "cookie_lock":
            print_error(
                "Could not read your browser's cookie database because the browser is currently "
                "open. Close the browser completely and try again, or import a cookies.txt file."
            )
        elif kind == "cookies_expired":
            print_error(
                "Your YouTube cookies have expired or were rotated (YouTube does this "
                "periodically as a security measure). Export a fresh cookies.txt from a "
                "logged-in browser session and re-import it on the Cookies tab — this is "
                "a very common cause of a video suddenly only offering lower-quality "
                "formats."
            )
        elif kind == "bot_check":
            print_error(
                "YouTube is blocking this request as a bot check — your cookies are missing, "
                "expired, or invalid. Export a fresh cookies.txt and import it on the Cookies tab."
            )

    def _show_progress(self, progress: float, speed: float = 0,
                       eta: Optional[int] = None) -> None:
        if progress > self._last_progress + 0.4 or progress >= 100:
            self._last_progress = progress
            BUS.progress.emit(float(progress), float(speed or 0),
                              int(eta) if eta is not None else -1, self.progress_label)

    def _progress_hook(self, d: Dict[str, Any]) -> None:
        if self.is_cancelled():
            raise DownloadCancelled("Cancelled by user")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                pct = (downloaded / total) * 100
                speed = d.get("speed", 0) or 0
                mbps = speed / (1024 * 1024) if speed else 0
                self._show_progress(pct, mbps, d.get("eta"))
        elif status == "finished":
            self._show_progress(100.0, 0)

    def get_available_formats(self, url: str) -> Optional[List[Dict]]:
        if yt_dlp is None:
            return None
        opts: Dict[str, Any] = {
            **_yt_dlp_quiet_opts(),
            "extract_flat": False,
            "socket_timeout": self.config.timeout,
            "http_headers": {"User-Agent": USER_AGENT},
            "noplaylist": self.config.no_playlist,
            "extractor_args": {"youtube": {"player_client": ["web"]}},
        }
        js_rt = _build_js_runtimes()
        if js_rt:
            opts["js_runtimes"] = js_rt
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = (info or {}).get("formats")
                if not formats:
                    return None
                for f in formats:
                    res = f.get("resolution")
                    if res and "x" in str(res):
                        parts = res.split("x")
                        if len(parts) == 2:
                            f.setdefault("width", int(parts[0]) if parts[0].isdigit() else None)
                            f.setdefault("height", int(parts[1]) if parts[1].isdigit() else None)
                return formats
        except Exception as e:
            kind = self._classify_auth_error(str(e))
            if kind:
                self._report_fatal_auth_error(kind)
            elif self.config.verbose:
                print_warning(f"Failed to get formats: {e}")
            return None

    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None) -> bool:
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════════════════
#  Format Selection Dialog
# ══════════════════════════════════════════════════════════════════════════

class FormatSelectionDialog(QDialog):
    def __init__(self, formats: List[Dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Available Formats")
        self.setMinimumSize(1000, 600)
        self.selected_format: Optional[str] = None
        self._format_client_map: Dict[str, str] = {}

        layout = QVBoxLayout(self)
        label = QLabel("Choose a quality / format to download:")
        label.setObjectName("HeadingLabel")
        layout.addWidget(label)
        hint = QLabel("Double-click a row, or select it and press OK. "
                      "Rows marked \"+audio\" are video-only streams — "
                      "the app automatically merges them with the best "
                      "available audio track (requires ffmpeg). "
                      "Rows with both video and audio download directly. "
                      "The Client column shows the full set of YouTube "
                      "clients that were queried together to build this "
                      "list — the download re-uses that same set, so the "
                      "quality you pick here is what you'll actually get.")
        hint.setObjectName("SubLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "ID", "EXT", "Resolution", "FPS", "TBR", "VCODEC", "ACODEC", "Info", "Size", "Client"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.accept)
        self._populate_table(formats)
        layout.addWidget(self.table, 1)
        if self.table.rowCount() > 0:
            self.table.selectRow(0)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setStyleSheet("""
            QDialog { background: #141925; }
            QLabel { color: #e1e8f0; }
            QTableWidget { background: transparent; color: #e1e8f0;
                           gridline-color: rgba(255,255,255,0.05); }
            QTableWidget::item { padding: 4px; }
            QTableWidget::item:selected { background: rgba(52,109,219,0.35); }
            QHeaderView::section { background: rgba(255,255,255,0.04);
                                  color: rgba(255,255,255,0.5);
                                  border: none; padding: 6px; }
            QPushButton { padding: 8px 16px; border-radius: 8px; }
        """)

    def _populate_table(self, formats: List[Dict]):
        filtered = []
        for f in formats:
            fmt_id = str(f.get("format_id", ""))
            ext = f.get("ext", "")
            if fmt_id.startswith("sb") or ext == "mhtml":
                continue
            client = f.get("_client", "unknown")
            self._format_client_map[fmt_id] = client
            filtered.append(f)

        def sort_key(f: Dict) -> Tuple[int, int, float]:
            height = f.get("height") or 0
            fps = f.get("fps") or 0
            tbr = f.get("tbr") or f.get("abr") or 0
            return (height, fps, tbr)

        ordered = sorted(filtered, key=sort_key, reverse=True)
        self._ordered_formats = ordered
        self.table.setRowCount(len(ordered))

        for i, fmt in enumerate(ordered):
            self.table.setItem(i, 0, QTableWidgetItem(str(fmt.get("format_id", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(fmt.get("ext", "")))

            height = fmt.get("height")
            width = fmt.get("width")
            if width and height:
                res = f"{width}x{height}"
            elif height:
                res = f"{height}p"
            elif fmt.get("vcodec") in (None, "none"):
                res = "audio only"
            else:
                res = ""
            self.table.setItem(i, 2, QTableWidgetItem(res))

            fps = fmt.get("fps", "")
            self.table.setItem(i, 3, QTableWidgetItem(str(fps) if fps else ""))

            tbr = fmt.get("tbr", "")
            self.table.setItem(i, 4, QTableWidgetItem(
                f"{tbr:.0f}" if isinstance(tbr, (int, float)) else ""))

            vcodec = fmt.get("vcodec", "none")
            self.table.setItem(i, 5, QTableWidgetItem(vcodec if vcodec != "none" else ""))

            acodec = fmt.get("acodec", "none")
            self.table.setItem(i, 6, QTableWidgetItem(acodec if acodec != "none" else ""))

            info_parts = []
            if fmt.get("format_note"):
                info_parts.append(str(fmt["format_note"]))
            if fmt.get("language"):
                info_parts.append(str(fmt["language"]))
            if fmt.get("dynamic_range"):
                info_parts.append(str(fmt["dynamic_range"]))
            if fmt.get("vbr"):
                info_parts.append(f"{fmt['vbr']}k")
            if fmt.get("abr"):
                info_parts.append(f"{fmt['abr']}k")
            has_video = vcodec not in (None, "none")
            has_audio = acodec not in (None, "none")
            if has_video and not has_audio:
                info_parts.append("+audio (auto-merge)")
            elif has_video and has_audio:
                info_parts.append("✓ complete")
            self.table.setItem(i, 7, QTableWidgetItem(" · ".join(info_parts)))

            filesize = fmt.get("filesize") or fmt.get("filesize_approx", 0)
            size_str = self._format_size(filesize) if filesize else ""
            self.table.setItem(i, 8, QTableWidgetItem(size_str))

            client = fmt.get("_client", "unknown")
            self.table.setItem(i, 9, QTableWidgetItem(client))

    def _format_size(self, size_bytes: float) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def get_selected_format(self) -> Tuple[Optional[str], Optional[str]]:
        if self.exec() == QDialog.Accepted:
            selected = self.table.currentRow()
            if selected >= 0:
                fmt_id = self.table.item(selected, 0).text()
                client = self._format_client_map.get(fmt_id, "unknown")
                return fmt_id, client
        return None, None


# ══════════════════════════════════════════════════════════════════════════
#  YouTube Downloader — v1.0.0 (guaranteed cleanup on cancel)
# ══════════════════════════════════════════════════════════════════════════

class YouTubeDownloader(Downloader):

    COMBINED_CLIENTS: List[str] = ["web", "tv", "android", "ios"]

    def __init__(self, config: Config, cookie_manager: Optional[CookieManager] = None):
        super().__init__(config, cookie_manager)
        self._available_formats_cache: Optional[List[Dict]] = None
        self._ffmpeg_warned = False
        self._selected_format_id: Optional[str] = None
        self._selected_client: Optional[str] = None
        # NEW: unique temporary file prefix and download directory for cleanup
        self._temp_prefix: Optional[str] = None
        self._download_output_dir: Optional[Path] = None

    def _warn_if_no_ffmpeg(self, format_str: str) -> None:
        if self._ffmpeg_warned:
            return
        needs_merge = "+" in format_str or format_str.startswith(("bestvideo", "bestaudio"))
        if needs_merge and not ffmpeg_available():
            self._ffmpeg_warned = True
            print_warning(
                "ffmpeg not found on PATH. Merging video+audio requires ffmpeg — "
                "install it (e.g. 'winget install Gyan.FFmpeg') or the download may fail."
            )

    def _cookie_options(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {}
        if self.cookie_manager:
            use_browser = self.config.use_browser_cookies and not self._force_cookiefile
            if use_browser:
                opts["cookiesfrombrowser"] = (self.config.browser_name, None, None)
            else:
                cp = self.cookie_manager.get_cookies()
                if cp and cp.exists():
                    opts["cookiefile"] = str(cp)
        return opts

    def _base_ydl_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            **_yt_dlp_quiet_opts(),
            "socket_timeout": self.config.timeout,
            "http_headers": {"User-Agent": USER_AGENT},
            "noplaylist": self.config.no_playlist,
        }
        opts.update(self._cookie_options())

        js_rt = _build_js_runtimes()
        if js_rt:
            opts["js_runtimes"] = js_rt

        return opts

    # ────────────────────────────────────────────────────────────────────
    #  get_available_formats — ONE combined-client call
    # ────────────────────────────────────────────────────────────────────
    def get_available_formats(self, url: str) -> Optional[List[Dict]]:
        self._fatal_error = None
        self._available_formats_cache = None

        if self.cookie_manager and not self.cookie_manager.ensure_ready():
            print_warning(
                "No valid cookies — proceeding anonymously. Regular videos are "
                "throttled by YouTube much more aggressively than Shorts when "
                "there's no valid login session, which is a common reason a "
                "normal video's format list looks worse than a Short's."
            )

        base = self._base_ydl_opts()
        opts = dict(base)
        opts["extract_flat"] = False
        opts["extractor_args"] = {"youtube": {"player_client": self.COMBINED_CLIENTS}}

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            kind = self._classify_auth_error(str(e))
            if kind and kind != "po_token_missing":
                self._report_fatal_auth_error(kind)
                return None
            if self.config.verbose:
                print_warning(f"Format fetch failed: {str(e)[:150]}")
            return None

        if not info or not info.get("formats"):
            return None

        client_label = "+".join(self.COMBINED_CLIENTS)
        cleaned: List[Dict] = []
        for f in info["formats"]:
            fmt_id = str(f.get("format_id", ""))
            if fmt_id.startswith("sb") or f.get("ext") == "mhtml":
                continue

            if f.get("resolution") and "x" in str(f["resolution"]):
                try:
                    w, h = f["resolution"].split("x")
                    f["width"] = int(w) if w.isdigit() else None
                    f["height"] = int(h) if h.isdigit() else None
                except Exception:
                    pass

            if f.get("filesize") is None and f.get("filesize_approx"):
                f["filesize"] = f["filesize_approx"]

            f["_client"] = client_label
            cleaned.append(f)

        cleaned.sort(key=lambda f: (
            f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0
        ), reverse=True)

        self._available_formats_cache = cleaned
        if cleaned:
            print_info(f"Found {len(cleaned)} formats (clients: {client_label})")
        return cleaned or None

    def _lookup_format(self, format_id: str) -> Optional[Dict]:
        if not self._available_formats_cache:
            return None
        for f in self._available_formats_cache:
            if str(f.get("format_id")) == str(format_id):
                return f
        return None

    def _resolve_format_string(self, format_id: str) -> str:
        fmt = self._lookup_format(format_id)
        if not fmt:
            return format_id

        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        has_video = vcodec not in (None, "none")
        has_audio = acodec not in (None, "none")

        if has_video and has_audio:
            print_info(f"Format {format_id} is complete (video+audio)")
            return format_id

        if has_video and not has_audio:
            fps = fmt.get("fps", 0) or 0
            if fps >= 60:
                print_info(f"Format {format_id} is 60fps video-only, merging with bestaudio")
                return f"{format_id}+bestaudio"
            print_info(f"Format {format_id} is video-only, merging with bestaudio")
            return f"{format_id}+bestaudio/best"

        if not has_video and has_audio:
            print_info(f"Format {format_id} is audio-only")
            return format_id

        return f"{format_id}+bestaudio/best/{format_id}"

    def _fallback_format_string(self, format_id: str) -> str:
        fmt = self._lookup_format(format_id)
        if not fmt:
            return "bestvideo+bestaudio/best"

        is_audio_only = fmt.get("vcodec") in (None, "none")
        if is_audio_only:
            return "bestaudio/best"

        height = fmt.get("height")
        fps = fmt.get("fps")

        if height and fps:
            return (f"bestvideo[height<={height}][fps<={fps}]+bestaudio/best[height<={height}][fps<={fps}]"
                    f"/best[height<={height}][fps<={fps}]/best")
        if height:
            return (f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
                    f"/best[height<={height}]/best")
        return "bestvideo+bestaudio/best"

    # ────────────────────────────────────────────────────────────────────
    #  Cleanup using the unique temp prefix
    # ────────────────────────────────────────────────────────────────────
    def _cleanup_temp_file(self) -> None:
        """Deletes ALL files whose name starts with `self._temp_prefix` inside `self._download_output_dir`."""
        if self._temp_prefix is None or self._download_output_dir is None:
            return
        # Delete any file matching the prefix
        for f in self._download_output_dir.glob(f"{self._temp_prefix}*"):
            try:
                f.unlink()
                print_info(f"Deleted temporary file: {f}")
            except Exception as e:
                print_warning(f"Failed to delete {f}: {e}")

    @staticmethod
    def _is_network_timeout(err: str) -> bool:
        m = (err or "").lower()
        return (
            "timed out" in m
            or "timeout" in m
            or "connection refused" in m
            or "failed to establish a new connection" in m
            or "max retries exceeded" in m
            or "connection aborted" in m
        )

    # ────────────────────────────────────────────────────────────────────
    #  download method with multi‑client fallback and guaranteed cleanup
    # ────────────────────────────────────────────────────────────────────
    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        if yt_dlp is None:
            print_error("yt-dlp not installed")
            return False

        self._is_running = True
        self._cancel_event.clear()
        self._fatal_error = None
        self._selected_format_id = format_id
        self._selected_client = preferred_client
        # Reset temp prefix and output directory
        self._temp_prefix = None
        self._download_output_dir = None

        output_dir = (output_path if output_path and output_path.is_dir()
                      else self.config.download_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._download_output_dir = output_dir

        # Generate a unique temporary filename prefix for this attempt
        temp_id = uuid.uuid4().hex[:8]
        temp_base = f"temp_{temp_id}"
        self._temp_prefix = temp_base

        if self.cookie_manager and not self.cookie_manager.ensure_ready():
            print_warning(
                "Continuing without valid cookies — this can force YouTube to serve "
                "a lower-quality format set, especially on regular (non-Shorts) videos."
            )

        base_opts = self._base_ydl_opts()
        base_opts.update({
            "progress_hooks": [self._progress_hook],
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": min(self.config.timeout, 15),
            "http_chunk_size": 10 * 1024 * 1024,
            "outtmpl": str(output_dir / f"{temp_base}.%(ext)s"),
            "merge_output_format": "mp4",
        })

        if is_audio_only or self.config.audio_only:
            base_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]

        # Helper to attempt a download with a given client list
        def attempt(format_str: str, client_list: List[str]) -> Tuple[bool, Optional[str], Optional[Path]]:
            opts = dict(base_opts)
            opts["extractor_args"] = {"youtube": {"player_client": client_list}}
            opts["format"] = format_str
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                if info:
                    # Determine the actual filename from info
                    title = info.get("title", "Unknown")
                    # Find the downloaded temp file(s) with our prefix
                    possible = list(output_dir.glob(f"{temp_base}.*"))
                    if possible:
                        # Use the first one (should be the final file)
                        temp_file = possible[0]
                        # Rename to proper title
                        final_name = f"{title}.{temp_file.suffix[1:]}"
                        final_path = output_dir / final_name
                        # Avoid overwriting if exists
                        counter = 1
                        while final_path.exists():
                            final_path = output_dir / f"{title}_{counter}.{temp_file.suffix[1:]}"
                            counter += 1
                        temp_file.rename(final_path)
                        print_success(f"Downloaded: {final_path.name}")
                        return True, None, final_path
                    else:
                        print_success(f"Downloaded: {info.get('title', 'Unknown')}")
                        return True, None, None
                return False, "no info returned", None
            except DownloadCancelled:
                # Clean up the temp file immediately
                self._cleanup_temp_file()
                return False, "cancelled", None
            except Exception as e:
                err = str(e)
                # Clean up on any error
                self._cleanup_temp_file()
                return False, err, None

        # 1) First try: exact format with combined clients
        if format_id:
            format_str = self._resolve_format_string(format_id)
            self._warn_if_no_ffmpeg(format_str)
            print_info(f"Attempt 1: {format_id} (clients: {'+'.join(self.COMBINED_CLIENTS)})")
            ok, err, final_path = attempt(format_str, self.COMBINED_CLIENTS)
            if ok:
                self._is_running = False
                return True
            if err == "cancelled":
                self._is_running = False
                return False
            if err and self._is_network_timeout(err):
                print_error(
                    "Network timeout reaching YouTube — check your internet "
                    "connection or VPN/proxy and try again."
                )
                self._is_running = False
                return False
            if err and "403" not in err.lower():
                # Non‑403 error – abort
                self._is_running = False
                return False
            print_warning("403 on combined clients – trying individual clients...")

        # 2) Retry the same format ID with each client individually
        if format_id:
            for client in self.COMBINED_CLIENTS:
                if self.is_cancelled():
                    self._cleanup_temp_file()
                    return False
                fmt_str = self._resolve_format_string(format_id)
                print_info(f"Attempt 2: {format_id} with client '{client}'")
                ok, err, final_path = attempt(fmt_str, [client])
                if ok:
                    self._is_running = False
                    return True
                if err == "cancelled":
                    self._is_running = False
                    return False
                if err and self._is_network_timeout(err):
                    print_error(
                        "Network timeout reaching YouTube — check your internet "
                        "connection or VPN/proxy and try again."
                    )
                    self._is_running = False
                    return False
                if err and "403" not in err.lower():
                    print_error(f"Client {client} failed: {err[:200]}")
                    self._is_running = False
                    return False
                time.sleep(0.5)

        # 3) Step down through quality levels
        quality_steps = [
            ("1080p", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"),
            ("720p",  "bestvideo[height<=720]+bestaudio/best[height<=720]/best"),
            ("480p",  "bestvideo[height<=480]+bestaudio/best[height<=480]/best"),
            ("360p",  "bestvideo[height<=360]+bestaudio/best[height<=360]/best"),
            ("best",  "bestvideo+bestaudio/best"),
        ]
        for label, fmt_str in quality_steps:
            if self.is_cancelled():
                self._cleanup_temp_file()
                return False
            print_info(f"Attempt 3: fallback to {label}")
            # Try combined clients
            ok, err, final_path = attempt(fmt_str, self.COMBINED_CLIENTS)
            if ok:
                self._is_running = False
                return True
            if err == "cancelled":
                self._is_running = False
                return False
            if err and self._is_network_timeout(err):
                print_error(
                    "Network timeout reaching YouTube — check your internet "
                    "connection or VPN/proxy and try again."
                )
                self._is_running = False
                return False
            if err and "403" not in err.lower():
                print_error(f"Fallback {label} failed: {err[:200]}")
                self._is_running = False
                return False
            # Try each client individually
            for client in self.COMBINED_CLIENTS:
                if self.is_cancelled():
                    self._cleanup_temp_file()
                    return False
                print_info(f"  → {label} with client '{client}'")
                ok2, err2, final_path2 = attempt(fmt_str, [client])
                if ok2:
                    self._is_running = False
                    return True
                if err2 == "cancelled":
                    self._is_running = False
                    return False
                if err2 and self._is_network_timeout(err2):
                    print_error(
                        "Network timeout reaching YouTube — check your internet "
                        "connection or VPN/proxy and try again."
                    )
                    self._is_running = False
                    return False
                if err2 and "403" not in err2.lower():
                    print_error(f"Client {client} with {label} failed: {err2[:200]}")
                    self._is_running = False
                    return False
                time.sleep(0.3)

        # All attempts exhausted
        print_error("All download attempts failed.")
        self._is_running = False
        return False


# ══════════════════════════════════════════════════════════════════════════
#  SoundCloud
# ══════════════════════════════════════════════════════════════════════════

class SoundCloudDownloader(Downloader):
    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        if yt_dlp is None:
            print_error("yt-dlp not installed"); return False
        self._is_running = True; self._cancel_event.clear()
        output_dir = (output_path if output_path and output_path.is_dir()
                      else self.config.download_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        bitrate = {"best":"320","high":"256","medium":"192","low":"128"}.get(
            quality or "best", "192")
        fmt = format_id or "bestaudio/best"
        opts: Dict[str, Any] = {
            **_yt_dlp_quiet_opts(),
            "progress_hooks": [self._progress_hook],
            "format": fmt,
            "postprocessors": [{"key": "FFmpegExtractAudio",
                                "preferredcodec": "mp3",
                                "preferredquality": bitrate}],
            "socket_timeout": self.config.timeout,
            "retries": self.config.retry_count,
            "http_headers": {"User-Agent": USER_AGENT},
            "noplaylist": self.config.no_playlist,
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        }
        if not ffmpeg_available():
            print_warning("ffmpeg not found — MP3 conversion may fail.")
        try:
            print_info(f"Downloading from SoundCloud @ {bitrate} kbps")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if info:
                print_success(f"Downloaded: {info.get('uploader','?')} - "
                              f"{info.get('title','?')}")
                self._is_running = False; return True
            self._is_running = False; return False
        except DownloadCancelled:
            print_warning("Download cancelled.")
            self._is_running = False; return False
        except Exception as e:
            print_error(f"SoundCloud download failed: {e}")
            self._is_running = False; return False


# ══════════════════════════════════════════════════════════════════════════
#  Spotify
# ══════════════════════════════════════════════════════════════════════════

class SpotifyDownloader(Downloader):
    def __init__(self, config: Config, cookie_manager: Optional[CookieManager] = None):
        super().__init__(config, cookie_manager)
        self._yt = YouTubeDownloader(config, cookie_manager)
        self._resolved_yt_url: Optional[str] = None

    def cancel(self) -> None:
        super().cancel(); self._yt.cancel()

    @staticmethod
    def _parse_artist_track(title: str) -> Tuple[str, str]:
        title = title.strip()
        if " - " in title:
            p = title.split(" - ", 1)
            if len(p) == 2:
                a, b = p[0].strip(), p[1].strip()
                if "feat" in b.lower() or "ft" in b.lower() or "&" in b:
                    return a, b
                if "feat" in a.lower() or "ft" in a.lower() or "&" in a:
                    return b, a
                return a, b
        m = re.search(r'^(.+?)\s*\((.+?)\)\s*$', title)
        if m:
            return m.group(2).strip(), m.group(1).strip()
        if ": " in title:
            p = title.split(": ", 1)
            if len(p) == 2:
                return p[0].strip(), p[1].strip()
        if " by " in title:
            p = title.split(" by ", 1)
            if len(p) == 2:
                return p[1].strip(), p[0].strip()
        return "", title.strip()

    def _search_yt(self, artist: str, track: str) -> List[Dict]:
        if yt_dlp is None:
            return []
        queries = []
        if track and artist:
            queries += [f"{track} - {artist}", f"{track} ({artist})",
                        f"{artist} - {track}"]
        if track:
            queries.append(track)
        for q in queries:
            if self.is_cancelled():
                return []
            print_info(f"Searching: {q}")
            opts: Dict[str, Any] = {
                **_yt_dlp_quiet_opts(),
                "extract_flat": "in_playlist", "playlistend": 20,
                "socket_timeout": 20,
                "http_headers": {"User-Agent": USER_AGENT},
                "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
            }
            js_rt = _build_js_runtimes()
            if js_rt:
                opts["js_runtimes"] = js_rt
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    res = ydl.extract_info(f"ytsearch20:{q}", download=False)
                    entries = (res or {}).get("entries", [])
                    if not entries:
                        continue
                    scored = []
                    tl, al = track.lower(), (artist or "").lower()
                    skip = ["trailer","movie","film","episode","gameplay",
                            "podcast","interview","news","reaction","review"]
                    for e in entries:
                        if not e:
                            continue
                        t = e.get("title", "").lower()
                        if any(s in t for s in skip):
                            continue
                        sc = 0
                        if tl in t: sc += 30
                        if al and al in t: sc += 25
                        elif al and al in (e.get("channel","") or "").lower(): sc += 15
                        if "official" in t: sc += 8
                        if "audio" in t or "music" in t: sc += 5
                        dur = e.get("duration", 0) or 0
                        if 120 <= dur <= 360: sc += 5
                        if "live" in t: sc -= 8
                        if "cover" in t: sc -= 10
                        if "karaoke" in t: sc -= 12
                        scored.append((sc, e))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    results = [e for s, e in scored if s > 10]
                    if results:
                        return results
            except Exception as e:
                print_info(f"Search failed: {e}")
        return []

    def _get_meta(self, url: str) -> Optional[Dict]:
        try:
            r = requests.get(f"https://open.spotify.com/oembed?url={url}",
                             timeout=self.config.timeout,
                             headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                t = r.json().get("title", "").replace(" - Spotify", "").strip()
                if t:
                    return {"title": t}
        except Exception:
            pass
        try:
            r = requests.get(url, timeout=self.config.timeout,
                             headers={"User-Agent": USER_AGENT})
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            for prop in ("og:title", "twitter:title"):
                m = (soup.find("meta", property=prop)
                     or soup.find("meta", attrs={"name": prop}))
                if m and m.get("content"):
                    t = m["content"].replace(" - Spotify", "").strip()
                    if t:
                        return {"title": t}
            m = soup.find("title")
            if m and m.text.strip():
                t = m.text.strip().replace(" - Spotify", "").strip()
                if t:
                    return {"title": t}
        except Exception:
            pass
        return None

    def _resolve_youtube_url(self, url: str) -> Optional[str]:
        if self._resolved_yt_url:
            return self._resolved_yt_url
        if requests is None or BeautifulSoup is None:
            print_error("Missing requests or beautifulsoup4")
            return None
        data = self._get_meta(url)
        if not data or not data.get("title"):
            print_error("Failed to fetch track metadata.")
            return None
        title = data["title"]
        artist, track = self._parse_artist_track(title)
        if artist and track:
            print_info(f"Artist: {artist}")
            print_info(f"Track:  {track}")
        else:
            track = title
            print_warning("Could not detect artist, searching by track only")
        results = self._search_yt(artist, track)
        if not results:
            print_error("No YouTube matches found")
            return None
        for e in results[:5]:
            yurl = e.get("webpage_url") or e.get("url")
            if yurl and "youtube.com" in yurl:
                self._resolved_yt_url = yurl
                return yurl
        return None

    def get_available_formats(self, url: str) -> Optional[List[Dict]]:
        yurl = self._resolve_youtube_url(url)
        if not yurl or self.is_cancelled():
            return None
        return self._yt.get_available_formats(yurl)

    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        try:
            yurl = self._resolve_youtube_url(url)
            if not yurl:
                return False
            if self.is_cancelled():
                return False
            return self._yt.download(yurl, output_path, quality=quality,
                                     is_audio_only=is_audio_only,
                                     format_id=format_id,
                                     preferred_client=preferred_client)
        except Exception as e:
            print_error(f"Spotify download failed: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════
#  TikTok
# ══════════════════════════════════════════════════════════════════════════

class TikTokDownloader(Downloader):
    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        if yt_dlp is None:
            print_error("yt-dlp not installed"); return False
        self._is_running = True; self._cancel_event.clear()
        output_dir = (output_path if output_path and output_path.is_dir()
                      else self.config.download_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        fmt = format_id or ("bestaudio/best" if is_audio_only
                            else "bestvideo+bestaudio/best")
        opts: Dict[str, Any] = {
            **_yt_dlp_quiet_opts(),
            "progress_hooks": [self._progress_hook],
            "format": fmt, "socket_timeout": self.config.timeout,
            "retries": self.config.retry_count,
            "http_headers": {"User-Agent": USER_AGENT},
            "noplaylist": self.config.no_playlist,
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        }
        if is_audio_only:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3", "preferredquality": "192"}]
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if info:
                print_success(f"Downloaded: {info.get('title', 'Unknown')}")
                self._is_running = False; return True
            self._is_running = False; return False
        except DownloadCancelled:
            print_warning("Download cancelled.")
            self._is_running = False; return False
        except Exception as e:
            print_error(f"TikTok download failed: {e}")
            self._is_running = False; return False


# ══════════════════════════════════════════════════════════════════════════
#  Twitter / X
# ══════════════════════════════════════════════════════════════════════════

class TwitterDownloader(Downloader):
    @staticmethod
    def _tweet_id(url: str) -> Optional[str]:
        for p in [r"twitter\.com/\w+/status/(\d+)",
                  r"x\.com/\w+/status/(\d+)",
                  r"mobile\.twitter\.com/\w+/status/(\d+)"]:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _space_id(url: str) -> Optional[str]:
        m = re.search(r"twitter\.com/i/spaces/([a-zA-Z0-9_]+)", url)
        return m.group(1) if m else None

    def get_available_formats(self, url: str) -> Optional[List[Dict]]:
        return None

    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        if requests is None:
            print_error("requests not installed"); return False
        self._is_running = True; self._cancel_event.clear()
        sid = self._space_id(url)
        if sid:
            result = self._download_space(sid, output_path)
            self._is_running = False; return result
        tid = self._tweet_id(url)
        if not tid:
            print_error("Could not extract tweet ID")
            self._is_running = False; return False
        for api in [f"https://api.fxtwitter.com/status/{tid}",
                    f"https://api.vxtwitter.com/status/{tid}"]:
            try:
                r = requests.get(api, timeout=self.config.timeout,
                                 headers={"User-Agent": USER_AGENT})
                if r.status_code != 200:
                    continue
                data = r.json()
                tweet = data.get("tweet") or data
                media = tweet.get("media") or tweet.get("media_extended") or []
                if not media:
                    print_warning("No media found in tweet")
                    self._is_running = False; return False
                out = (output_path if output_path and output_path.is_dir()
                       else self.config.download_dir)
                out.mkdir(parents=True, exist_ok=True)
                ok = False
                for i, item in enumerate(media):
                    if self.is_cancelled():
                        self._is_running = False; return ok
                    mu = item.get("url") or item.get("src")
                    if not mu:
                        continue
                    ext = ".mp4" if item.get("type") in ("video", "gif") else ".jpg"
                    fp = out / f"twitter_{tid}_{i}{ext}"
                    if self._dl_file(mu, fp):
                        print_success(f"Downloaded: {fp.name}"); ok = True
                self._is_running = False; return ok
            except Exception:
                continue
        print_error("Failed to download from Twitter")
        self._is_running = False; return False

    def _download_space(self, sid: str,
                        output_path: Optional[Path] = None) -> bool:
        print_info(f"Downloading Twitter Space: {sid}")
        out = (output_path if output_path and output_path.is_dir()
               else self.config.download_dir)
        out.mkdir(parents=True, exist_ok=True)
        fp = out / f"twitter_space_{sid}.m4a"
        if not shutil.which("ffmpeg"):
            print_error("ffmpeg is required for Twitter Spaces.")
            return False
        try:
            gt = self._guest_token()
            if not gt:
                print_error("Failed to get guest token"); return False
            sd = self._space_data(sid, gt)
            if not sd:
                print_error("Failed to fetch space data"); return False
            su = ((sd.get("stream") or {}).get("location")
                  or (sd.get("stream") or {}).get("url"))
            if not su:
                print_error("No stream URL"); return False
            cmd = ["ffmpeg", "-y", "-i", su, "-c", "copy",
                   "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(fp)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode == 0 and fp.exists():
                print_success(f"Downloaded Space: {fp.name}"); return True
            print_error(f"ffmpeg failed: {r.stderr[:200]}"); return False
        except Exception as e:
            print_error(f"Space download failed: {e}"); return False

    @staticmethod
    def _guest_token() -> Optional[str]:
        try:
            r = requests.post(
                "https://api.twitter.com/1.1/guest/activate.json",
                headers={"User-Agent": USER_AGENT}, timeout=15)
            if r.status_code == 200:
                return r.json().get("guest_token")
        except Exception:
            pass
        return None

    @staticmethod
    def _space_data(sid: str, gt: str) -> Optional[Dict]:
        try:
            v = json.dumps({"id": sid, "isMetatagsQuery": False,
                            "withListeners": True})
            r = requests.get(
                "https://twitter.com/i/api/graphql/"
                "kZ9wfR8EBtiP0As3sFFrBA/AudioSpaceById",
                params={"variables": v},
                headers={
                    "Authorization":
                        "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAA"
                        "nNwIzUejRCOuH5E6I8xnZz4puTs%3D"
                        "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
                    "x-guest-token": gt,
                    "User-Agent": USER_AGENT},
                timeout=30)
            if r.status_code == 200:
                return r.json().get("data", {}).get("audioSpace")
        except Exception:
            pass
        return None

    def _dl_file(self, url: str, path: Path) -> bool:
        try:
            r = requests.get(url, stream=True, timeout=self.config.timeout)
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            dl = 0
            with open(path, "wb") as f:
                for c in r.iter_content(8192):
                    if self.is_cancelled():
                        return False
                    if c:
                        f.write(c); dl += len(c)
                        if total:
                            self._show_progress((dl / total) * 100)
            return path.exists() and path.stat().st_size > 0
        except Exception as e:
            print_error(f"Download failed: {e}"); return False


# ══════════════════════════════════════════════════════════════════════════
#  Instagram
# ══════════════════════════════════════════════════════════════════════════

class InstagramDownloader(Downloader):
    def get_available_formats(self, url: str) -> Optional[List[Dict]]:
        return None

    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        if instaloader is None:
            print_error("instaloader not installed"); return False
        self._is_running = True; self._cancel_event.clear()
        sc = None
        for p in [r"instagram\.com/p/([A-Za-z0-9_-]+)",
                  r"instagram\.com/reel/([A-Za-z0-9_-]+)",
                  r"instagram\.com/stories/[^/]+/([A-Za-z0-9_-]+)",
                  r"instagram\.com/tv/([A-Za-z0-9_-]+)"]:
            m = re.search(p, url)
            if m:
                sc = m.group(1); break
        if not sc:
            print_error("Could not extract Instagram shortcode")
            self._is_running = False; return False
        out = (output_path if output_path and output_path.is_dir()
               else self.config.download_dir)
        out.mkdir(parents=True, exist_ok=True)
        try:
            L = instaloader.Instaloader(
                dirname_pattern=str(out), download_videos=True,
                download_video_thumbnails=False, download_geotags=False,
                download_comments=False, save_metadata=False, quiet=True)
            post = instaloader.Post.from_shortcode(L.context, sc)
            L.download_post(post, target=sc)
            if list(out.glob(f"*{sc}*")):
                print_success(f"Downloaded: {sc}")
                self._is_running = False; return True
            self._is_running = False; return False
        except Exception as e:
            print_error(f"Instagram download failed: {e}")
            self._is_running = False; return False


# ══════════════════════════════════════════════════════════════════════════
#  Pinterest
# ══════════════════════════════════════════════════════════════════════════

class PinterestDownloader(Downloader):
    def get_available_formats(self, url: str) -> Optional[List[Dict]]:
        return None

    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        if requests is None or BeautifulSoup is None:
            print_error("Missing requests / bs4"); return False
        self._is_running = True; self._cancel_event.clear()
        out = (output_path if output_path and output_path.is_dir()
               else self.config.download_dir)
        out.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(url, timeout=self.config.timeout,
                             headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            img = None
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                img = og["content"]
            if not img:
                for sc in soup.find_all("script",
                                        type="application/ld+json"):
                    try:
                        d = json.loads(sc.string)
                        if isinstance(d, dict) and "image" in d:
                            im = d["image"]
                            img = im if isinstance(im, str) else im.get("url")
                            break
                    except Exception:
                        continue
            if not img:
                print_error("Could not find image URL")
                self._is_running = False; return False
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = out / f"pinterest_{ts}.jpg"
            ri = requests.get(img, stream=True, timeout=self.config.timeout)
            ri.raise_for_status()
            total = int(ri.headers.get("content-length", 0)); dl = 0
            with open(fp, "wb") as f:
                for c in ri.iter_content(8192):
                    if self.is_cancelled():
                        self._is_running = False; return False
                    if c:
                        f.write(c); dl += len(c)
                        if total:
                            self._show_progress((dl / total) * 100)
            print_success(f"Downloaded: {fp.name}")
            self._is_running = False; return True
        except Exception as e:
            print_error(f"Pinterest download failed: {e}")
            self._is_running = False; return False


# ══════════════════════════════════════════════════════════════════════════
#  RadioJavan
# ══════════════════════════════════════════════════════════════════════════

class RadioJavanDownloader(Downloader):
    @staticmethod
    def _media_name(url: str) -> Optional[str]:
        for p in [r"/mp3/([\w%-]+)", r"/mp4/([\w%-]+)", r"/song/([\w%-]+)",
                  r"/music_video/([\w%-]+)", r"/playlist/mp3/([\w%-]+)",
                  r"/podcast/([\w%-]+)", r"/album/([\w%-]+)",
                  r"rj\.app/m/([\w%-]+)", r"rj\.app/v/([\w%-]+)"]:
            m = re.search(p, url)
            if m:
                return unquote(m.group(1))
        return None

    @staticmethod
    def _type(url: str) -> str:
        return ("mp3" if ("/mp3/" in url or "/song/" in url
                         or "rj.app/m" in url) else "mp4")

    def get_available_formats(self, url: str) -> Optional[List[Dict]]:
        return None

    def download(self, url: str, output_path: Optional[Path] = None,
                 quality: Optional[str] = None, is_audio_only: bool = False,
                 format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None) -> bool:
        if requests is None:
            print_error("requests not installed"); return False
        self._is_running = True; self._cancel_event.clear()
        name = self._media_name(url)
        if not name:
            print_error("Could not extract media name")
            self._is_running = False; return False
        mt = self._type(url)
        ext = ".mp3" if mt == "mp3" else ".mp4"
        out = (output_path if output_path and output_path.is_dir()
               else self.config.download_dir)
        out.mkdir(parents=True, exist_ok=True)
        for host in ["https://www.radiojavan.com", "https://rj1.media",
                     "https://rj2.media", "https://rj3.media",
                     "https://rjmedia.app"]:
            if self.is_cancelled():
                self._is_running = False; return False
            dl_url = f"{host}/media/{mt}/{name}{ext}"
            try:
                h = requests.head(dl_url, timeout=self.config.timeout)
                if h.status_code != 200:
                    continue
                fp = out / f"{name}{ext}"
                r = requests.get(dl_url, stream=True,
                                 timeout=self.config.timeout)
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0)); dl = 0
                with open(fp, "wb") as f:
                    for c in r.iter_content(8192):
                        if self.is_cancelled():
                            self._is_running = False; return False
                        if c:
                            f.write(c); dl += len(c)
                            if total:
                                self._show_progress((dl / total) * 100)
                print_success(f"Downloaded: {fp.name}")
                self._is_running = False; return True
            except Exception:
                continue
        print_error(f"Failed to download: {name}")
        self._is_running = False; return False


# ══════════════════════════════════════════════════════════════════════════
#  Factory
# ══════════════════════════════════════════════════════════════════════════

def get_downloader(service: ServiceType, config: Config,
                   cookie_manager: Optional[CookieManager] = None
                   ) -> Optional[Downloader]:
    mapping = {
        ServiceType.YOUTUBE:    YouTubeDownloader,
        ServiceType.SOUNDCLOUD: SoundCloudDownloader,
        ServiceType.SPOTIFY:    SpotifyDownloader,
        ServiceType.TIKTOK:     TikTokDownloader,
        ServiceType.TWITTER:    TwitterDownloader,
        ServiceType.INSTAGRAM:  InstagramDownloader,
        ServiceType.PINTEREST:  PinterestDownloader,
        ServiceType.RADIOJAVAN: RadioJavanDownloader,
    }
    cls = mapping.get(service)
    return cls(config, cookie_manager) if cls else None


def quality_options_for(service: ServiceType, audio_flag: bool) -> List[str]:
    if service in (ServiceType.SOUNDCLOUD, ServiceType.RADIOJAVAN):
        return ["best", "high", "medium", "low"]
    if service == ServiceType.SPOTIFY and audio_flag:
        return ["best", "high", "medium", "low"]
    return ["best", "1080p", "720p", "480p", "worst"]


# ══════════════════════════════════════════════════════════════════════════
#  Background workers
# ══════════════════════════════════════════════════════════════════════════

class FormatFetchWorker(QThread):
    finished_signal = Signal(object, str)

    def __init__(self, downloader: Downloader, url: str):
        super().__init__()
        self.downloader = downloader
        self.url = url
        self._is_running = True

    def run(self):
        try:
            formats = self.downloader.get_available_formats(self.url)
            if self._is_running:
                self.finished_signal.emit(formats, "")
        except Exception as e:
            if self._is_running:
                self.finished_signal.emit(None, str(e))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False
        if self.downloader:
            self.downloader.cancel()


class DownloadWorker(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, downloader: Downloader, url: str, quality: str,
                 audio_only: bool, format_id: Optional[str] = None,
                 preferred_client: Optional[str] = None):
        super().__init__()
        self.downloader = downloader
        self.url = url
        self.quality = quality
        self.audio_only = audio_only
        self.format_id = format_id
        self.preferred_client = preferred_client
        self._is_running = True

    def run(self):
        try:
            ok = self.downloader.download(
                self.url, quality=self.quality,
                is_audio_only=self.audio_only,
                format_id=self.format_id,
                preferred_client=self.preferred_client)
            if self._is_running:
                self.finished_signal.emit(ok, "" if ok else "Download failed.")
        except Exception as e:
            if self._is_running:
                self.finished_signal.emit(False, str(e))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False
        if self.downloader:
            self.downloader.cancel()


# ══════════════════════════════════════════════════════════════════════════
#  Stylesheet — dark glass design
# ══════════════════════════════════════════════════════════════════════════

STYLE = """
* {
    outline: none;
    font-family: -apple-system, 'Segoe UI', system-ui, sans-serif;
}
QMainWindow { background: transparent; }

#AppRoot {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #1a1f2e, stop:0.5 #141925, stop:1 #0d1119);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 20px;
}

#TitleBar { background: transparent; }
#TitleLabel { color: #8b95a9; font-size: 13px; font-weight: 600;
              letter-spacing: 0.3px; }
#VersionLabel { color: rgba(255,255,255,0.25); font-size: 11px; }

#WindowControl, #CloseControl {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 8px; color: #8b95a9; font-size: 13px; font-weight: 400;
    padding: 0; min-width: 30px; min-height: 30px;
    max-width: 30px; max-height: 30px;
}
#WindowControl:hover { background: rgba(255,255,255,0.08); color: #e1e8f0; }
#CloseControl:hover  { background: #e74c3c; color: white;
                       border-color: #e74c3c; }

#Sidebar { background: rgba(255,255,255,0.02);
           border-right: 1px solid rgba(255,255,255,0.06); }

#NavButton {
    background: transparent; border: 1px solid transparent;
    border-radius: 12px; color: rgba(255,255,255,0.55);
    font-size: 14px; font-weight: 500; text-align: left;
    padding: 12px 16px;
}
#NavButton:hover { background: rgba(255,255,255,0.06); color: #e1e8f0; }
#NavButton[active="true"] {
    background: rgba(52, 109, 219, 0.16);
    border: 1px solid rgba(52, 109, 219, 0.30);
    color: #6b9fff;
}

#Card {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 14px;
}
#WarnCard {
    background: rgba(245, 200, 106, 0.08);
    border: 1px solid rgba(245, 200, 106, 0.25);
    border-radius: 12px;
}
#SectionLabel { color: rgba(100,180,255,0.65); font-size: 11px;
                font-weight: 700; letter-spacing: 1px; }
#FieldLabel   { color: rgba(255,255,255,0.45); font-size: 12px;
                font-weight: 500; }
#HeadingLabel { color: #f0f4fc; font-size: 20px; font-weight: 300; }
#SubLabel     { color: rgba(255,255,255,0.35); font-size: 13px; }
#WarnLabel    { color: #f5c86a; font-size: 12px; font-weight: 500; }

#ServiceBadge {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px; color: rgba(255,255,255,0.65);
    font-size: 13px; font-weight: 500; padding: 6px 12px;
}

QLineEdit, QComboBox, QSpinBox {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px; color: #e1e8f0; font-size: 14px;
    padding: 10px 12px;
    selection-background-color: rgba(52,109,219,0.4);
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: rgba(52,109,219,0.5);
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #171c29; border: 1px solid rgba(255,255,255,0.08);
    color: #e1e8f0; selection-background-color: rgba(52,109,219,0.35);
    outline: none; border-radius: 8px;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 16px; background: transparent;
}

QCheckBox, QRadioButton {
    color: rgba(255,255,255,0.7); font-size: 13px; spacing: 8px;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid rgba(255,255,255,0.20);
    background: rgba(255,255,255,0.03);
}
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: #346ddb; border-color: #346ddb;
}

#PrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #346ddb, stop:1 #2563eb);
    border: 1px solid rgba(52, 109, 219, 0.4);
    border-radius: 12px; color: white; font-size: 15px;
    font-weight: 600; padding: 12px 20px;
}
#PrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4080f0, stop:1 #3470e0);
}
#PrimaryButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #2050c0, stop:1 #1a45a8);
}
#PrimaryButton:disabled {
    background: rgba(255,255,255,0.06);
    color: rgba(255,255,255,0.25);
    border-color: rgba(255,255,255,0.06);
}

#SecondaryButton {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px; color: rgba(255,255,255,0.65);
    font-size: 13px; font-weight: 500; padding: 10px 16px;
}
#SecondaryButton:hover {
    background: rgba(255,255,255,0.08); color: #e1e8f0;
}
#SecondaryButton:disabled { color: rgba(255,255,255,0.20); }

#DangerButton {
    background: rgba(231, 76, 60, 0.10);
    border: 1px solid rgba(231, 76, 60, 0.25);
    border-radius: 12px; color: #ff8a75; font-size: 13px;
    font-weight: 500; padding: 10px 16px;
}
#DangerButton:hover {
    background: rgba(231, 76, 60, 0.22); color: #ffb0a0;
}

QProgressBar {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px; height: 14px; text-align: center;
    color: rgba(255,255,255,0.6); font-size: 10px;
}
QProgressBar::chunk {
    border-radius: 7px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #346ddb, stop:1 #6b9fff);
}

#LogConsole {
    background: rgba(0,0,0,0.25);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px; color: #c7cfdd;
    font-family: 'Cascadia Code','Consolas',monospace;
    font-size: 12px; padding: 8px;
}

QTableWidget {
    background: transparent; border: none; color: #e1e8f0;
    gridline-color: rgba(255,255,255,0.05);
}
QTableWidget::item { padding: 6px; }
QHeaderView::section {
    background: rgba(255,255,255,0.04);
    color: rgba(255,255,255,0.5); border: none;
    padding: 8px; font-size: 11px; font-weight: 700;
}

QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.12); border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.22); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 0; }

QMessageBox { background: #141925; }
QMessageBox QLabel { color: #e1e8f0; }
QDialog { background: #141925; }
"""


# ══════════════════════════════════════════════════════════════════════════
#  SweetAlert-style modal
# ══════════════════════════════════════════════════════════════════════════

class SweetAlert(QDialog):
    ICONS = {
        "success": ("✓", "#7ee0a8", "rgba(126,224,168,0.12)"),
        "error":   ("✕", "#ff8a75", "rgba(255,138,117,0.12)"),
        "warning": ("!", "#f5c86a", "rgba(245,200,106,0.12)"),
        "info":    ("i", "#6b9fff", "rgba(107,159,255,0.12)"),
    }

    def __init__(self, kind: str, title: str, text: str = "",
                 confirm_text: str = "OK", cancel_text: Optional[str] = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.result_value = False

        symbol, color, bg = self.ICONS.get(kind, self.ICONS["info"])

        container = QFrame()
        container.setObjectName("SweetAlertCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addWidget(container)

        lay = QVBoxLayout(container)
        lay.setContentsMargins(32, 30, 32, 26)
        lay.setSpacing(14)
        lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel(symbol)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setFixedSize(64, 64)
        icon_lbl.setStyleSheet(
            f"background:{bg}; border:2px solid {color}; border-radius:32px;"
            f"color:{color}; font-size:26px; font-weight:700;")
        lay.addWidget(icon_lbl, 0, Qt.AlignCenter)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            "color:#f0f4fc; font-size:17px; font-weight:600; background: transparent;")
        title_lbl.setWordWrap(True)
        lay.addWidget(title_lbl)

        if text:
            text_lbl = QLabel(text)
            text_lbl.setAlignment(Qt.AlignCenter)
            text_lbl.setWordWrap(True)
            text_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.55); font-size:13px; background: transparent;")
            lay.addWidget(text_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        if cancel_text:
            cancel_btn = make_secondary_button(cancel_text)
            cancel_btn.clicked.connect(self._on_cancel)
            btn_row.addWidget(cancel_btn)
        ok_btn = make_primary_button(confirm_text)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(ok_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        container.setStyleSheet(f"""
            #SweetAlertCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1f2e, stop:1 #0d1119);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 18px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(50)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 180))
        container.setGraphicsEffect(shadow)

        self.setFixedWidth(380)

    def _on_confirm(self):
        self.result_value = True
        self.accept()

    def _on_cancel(self):
        self.result_value = False
        self.reject()

    @classmethod
    def show(cls, kind: str, title: str, text: str = "", parent=None,
             confirm_text: str = "OK"):
        dlg = cls(kind, title, text, confirm_text=confirm_text, parent=parent)
        dlg.exec()

    @classmethod
    def confirm(cls, title: str, text: str = "", parent=None,
                confirm_text: str = "Yes", cancel_text: str = "Cancel") -> bool:
        dlg = cls("warning", title, text, confirm_text=confirm_text,
                   cancel_text=cancel_text, parent=parent)
        dlg.exec()
        return dlg.result_value


# ══════════════════════════════════════════════════════════════════════════
#  Startup configuration check
# ══════════════════════════════════════════════════════════════════════════

class LoadingModal(QDialog):
    def __init__(self, text: str = "Checking", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)

        container = QFrame()
        container.setObjectName("SweetAlertCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addWidget(container)

        lay = QVBoxLayout(container)
        lay.setContentsMargins(32, 30, 32, 26)
        lay.setSpacing(14)
        lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel("i")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setFixedSize(64, 64)
        icon_lbl.setStyleSheet(
            "background: rgba(107,159,255,0.12); border:2px solid #6b9fff; "
            "border-radius:32px; color:#6b9fff; font-size:26px; font-weight:700;")
        lay.addWidget(icon_lbl, 0, Qt.AlignCenter)

        self.text_label = QLabel(text)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet(
            "color:#f0f4fc; font-size:15px; font-weight:600; background: transparent;")
        lay.addWidget(self.text_label)

        container.setStyleSheet("""
            #SweetAlertCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1f2e, stop:1 #0d1119);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 18px;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(50)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 180))
        container.setGraphicsEffect(shadow)

        self.setFixedWidth(320)

        self._base_text = text
        self._dots_state = 0
        self._dots_timer = QTimer(self)
        self._dots_timer.timeout.connect(self._animate_dots)
        self._dots_timer.start(450)

    def set_text(self, text: str) -> None:
        self._base_text = text
        self.text_label.setText(text)

    def _animate_dots(self) -> None:
        self._dots_state = (self._dots_state + 1) % 4
        self.text_label.setText(self._base_text.rstrip(".") + "." * self._dots_state)

    def closeEvent(self, event) -> None:
        self._dots_timer.stop()
        super().closeEvent(event)


class ConfigRequiredModal(QDialog):
    def __init__(self, issues: List[Dict[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.chosen_target: Optional[str] = None
        self._first_target = issues[0]["target"] if issues else None

        container = QFrame()
        container.setObjectName("SweetAlertCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addWidget(container)

        lay = QVBoxLayout(container)
        lay.setContentsMargins(32, 30, 32, 26)
        lay.setSpacing(14)
        lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel("!")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setFixedSize(64, 64)
        icon_lbl.setStyleSheet(
            "background: rgba(245,200,106,0.12); border:2px solid #f5c86a; "
            "border-radius:32px; color:#f5c86a; font-size:26px; font-weight:700;")
        lay.addWidget(icon_lbl, 0, Qt.AlignCenter)

        title_lbl = QLabel("Setup Required")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            "color:#f0f4fc; font-size:17px; font-weight:600; background: transparent;")
        lay.addWidget(title_lbl)

        text_lbl = QLabel("The following need to be configured before downloading:")
        text_lbl.setAlignment(Qt.AlignCenter)
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:13px; background: transparent;")
        lay.addWidget(text_lbl)

        for issue in issues:
            row = QFrame()
            row.setStyleSheet(
                "background: rgba(255,255,255,0.04); border-radius: 10px;")
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(14, 10, 14, 10)
            row_lay.setSpacing(2)
            name_lbl = QLabel(issue["title"])
            name_lbl.setStyleSheet(
                "color:#f5c86a; font-size:13px; font-weight:600; background: transparent;")
            desc_lbl = QLabel(issue["message"])
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.6); font-size:12px; background: transparent;")
            row_lay.addWidget(name_lbl)
            row_lay.addWidget(desc_lbl)
            lay.addWidget(row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        cancel_btn = make_secondary_button("Cancel")
        cancel_btn.clicked.connect(self._on_later)
        btn_row.addWidget(cancel_btn)
        ok_btn = make_primary_button("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_fix_now)
        btn_row.addWidget(ok_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        container.setStyleSheet("""
            #SweetAlertCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1f2e, stop:1 #0d1119);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 18px;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(50)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 180))
        container.setGraphicsEffect(shadow)

        self.setFixedWidth(420)

    def _on_later(self) -> None:
        self.chosen_target = None
        self.accept()

    def _on_fix_now(self) -> None:
        self.chosen_target = self._first_target
        self.accept()


class ConfigCheckWorker(QThread):
    finished_signal = Signal(list)

    def __init__(self, config: "Config", cookie_manager: "CookieManager"):
        super().__init__()
        self.config = config
        self.cookie_manager = cookie_manager

    def run(self) -> None:
        issues: List[Dict[str, str]] = []

        if yt_dlp is None:
            issues.append({
                "title": "yt-dlp not installed",
                "message": "The download engine is missing. Restart the app "
                           "to auto-install it, or run: pip install yt-dlp",
                "target": "settings",
            })

        if not ffmpeg_available():
            issues.append({
                "title": "ffmpeg not found",
                "message": "Required to merge video+audio and convert to MP3. "
                           "Install ffmpeg and make sure it is on your PATH.",
                "target": "settings",
            })

        try:
            self.config.download_dir.mkdir(parents=True, exist_ok=True)
            test_file = self.config.download_dir / ".oryvex_write_test"
            test_file.write_text("ok")
            test_file.unlink()
        except Exception:
            issues.append({
                "title": "Download folder not writable",
                "message": f"Cannot write to {self.config.download_dir}. "
                           "Choose a different folder in Settings.",
                "target": "settings",
            })

        if (not self.config.use_browser_cookies
                and not self.cookie_manager.has_cookies()):
            issues.append({
                "title": "YouTube cookies not set",
                "message": "Import a cookies.txt file, or enable browser "
                           "cookies, on the Cookies tab for full quality "
                           "and reliable downloads.",
                "target": "cookies",
            })

        self.finished_signal.emit(issues)


# ══════════════════════════════════════════════════════════════════════════
#  UI helper widgets
# ══════════════════════════════════════════════════════════════════════════

class TitleBar(QWidget):
    def __init__(self, parent_window: QMainWindow):
        super().__init__()
        self.setObjectName("TitleBar")
        self._win = parent_window
        self._drag_pos: Optional[QPoint] = None
        self.setFixedHeight(52)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 12, 8)
        lay.setSpacing(8)

        title = QLabel("⬇  ORYVEX MEDIA DOWNLOADER")
        title.setObjectName("TitleLabel")
        ver = QLabel(f"v{VERSION}")
        ver.setObjectName("VersionLabel")

        lay.addWidget(title)
        lay.addWidget(ver)
        lay.addStretch(1)

        min_btn = QPushButton("–")
        min_btn.setObjectName("WindowControl")
        min_btn.clicked.connect(self._win.showMinimized)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseControl")
        close_btn.clicked.connect(self._win.close)

        lay.addWidget(min_btn)
        lay.addWidget(close_btn)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (event.globalPosition().toPoint()
                              - self._win.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


def make_card() -> QFrame:
    f = QFrame(); f.setObjectName("Card"); return f

def make_primary_button(text: str) -> QPushButton:
    b = QPushButton(text); b.setObjectName("PrimaryButton")
    b.setCursor(Qt.PointingHandCursor); return b

def make_secondary_button(text: str) -> QPushButton:
    b = QPushButton(text); b.setObjectName("SecondaryButton")
    b.setCursor(Qt.PointingHandCursor); return b

def make_danger_button(text: str) -> QPushButton:
    b = QPushButton(text); b.setObjectName("DangerButton")
    b.setCursor(Qt.PointingHandCursor); return b


# ══════════════════════════════════════════════════════════════════════════
#  Download Page
# ══════════════════════════════════════════════════════════════════════════

class DownloadPage(QWidget):
    def __init__(self, config: Config, cookie_manager: CookieManager):
        super().__init__()
        self.config = config
        self.cookie_manager = cookie_manager
        self.current_downloader: Optional[Downloader] = None
        self.worker: Optional[DownloadWorker] = None
        self.format_worker: Optional[FormatFetchWorker] = None
        self._current_service = ServiceType.GENERIC
        self._pending_url: str = ""
        self._selected_format_id: Optional[str] = None
        self._selected_client: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        heading = QLabel("New Download")
        heading.setObjectName("HeadingLabel")
        sub = QLabel("Paste a link, then choose your format from the list "
                     "we fetch for you.")
        sub.setObjectName("SubLabel")
        root.addWidget(heading)
        root.addWidget(sub)

        # ── Environment warning banner ──
        self.env_warning = QFrame()
        self.env_warning.setObjectName("WarnCard")
        ew_lay = QHBoxLayout(self.env_warning)
        ew_lay.setContentsMargins(14, 10, 14, 10)
        self.env_warning_label = QLabel("")
        self.env_warning_label.setObjectName("WarnLabel")
        self.env_warning_label.setWordWrap(True)
        ew_lay.addWidget(self.env_warning_label, 1)
        root.addWidget(self.env_warning)
        self._refresh_env_warning()

        # ── URL card ──
        url_card = make_card()
        ucl = QVBoxLayout(url_card)
        ucl.setContentsMargins(18, 16, 18, 16)
        ucl.setSpacing(10)

        lbl = QLabel("MEDIA URL")
        lbl.setObjectName("SectionLabel")
        ucl.addWidget(lbl)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "https://youtube.com/watch?v=… , spotify.com/track/… , "
            "tiktok.com/@…")
        self.url_input.textChanged.connect(self._on_url_changed)
        self.url_input.returnPressed.connect(self._start_download)
        self.service_badge = QLabel("🔗  No link yet")
        self.service_badge.setObjectName("ServiceBadge")
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.service_badge)
        ucl.addLayout(url_row)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(16)

        self.audio_checkbox = QCheckBox("Audio only (MP3)")
        opt_row.addWidget(self.audio_checkbox, 0, Qt.AlignVCenter)

        self.spotify_group_widget = QWidget()
        sg_lay = QHBoxLayout(self.spotify_group_widget)
        sg_lay.setContentsMargins(0, 0, 0, 0)
        self.radio_audio = QRadioButton("Audio (MP3)")
        self.radio_video = QRadioButton("Video")
        self.radio_audio.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.radio_audio)
        grp.addButton(self.radio_video)
        sg_lay.addWidget(self.radio_audio)
        sg_lay.addWidget(self.radio_video)
        opt_row.addWidget(self.spotify_group_widget, 0, Qt.AlignVCenter)
        self.spotify_group_widget.setVisible(False)

        opt_row.addStretch(1)
        ucl.addLayout(opt_row)
        root.addWidget(url_card)

        # ── Action row ──
        action_row = QHBoxLayout()
        self.download_btn = make_primary_button("⬇  Start Download")
        self.download_btn.clicked.connect(self._start_download)
        self.cancel_btn = make_secondary_button("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_download)
        action_row.addWidget(self.download_btn, 1)
        action_row.addWidget(self.cancel_btn)
        root.addLayout(action_row)

        # ── Progress ──
        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("SubLabel")
        self.status_label.setFixedWidth(220)
        prog_row.addWidget(self.progress_bar, 1)
        prog_row.addWidget(self.status_label)
        root.addLayout(prog_row)

        # ── Log console ──
        log_lbl = QLabel("ACTIVITY LOG")
        log_lbl.setObjectName("SectionLabel")
        root.addWidget(log_lbl)

        self.log_console = QTextEdit()
        self.log_console.setObjectName("LogConsole")
        self.log_console.setReadOnly(True)
        root.addWidget(self.log_console, 1)

        BUS.message.connect(self._on_log)
        BUS.progress.connect(self._on_progress)

    def _refresh_env_warning(self):
        missing = []
        if not ffmpeg_available():
            missing.append("ffmpeg (merge video+audio / MP3 conversion)")
        js_rt = _build_js_runtimes()
        has_real_rt = js_rt and any(
            v.get("path") is not None for v in js_rt.values())
        if not has_real_rt:
            missing.append("a JS runtime (Deno: https://deno.com)")
        try:
            import yt_dlp_ejs  # noqa: F401
        except ImportError:
            missing.append("yt-dlp-ejs (pip install yt-dlp-ejs)")
        if missing:
            self.env_warning.setVisible(True)
            self.env_warning_label.setText(
                "⚠ Missing: " + "; ".join(missing) +
                ". Without a JS runtime YouTube may hide some formats "
                "and quality picks can silently fall back to a lower "
                "one — install Deno for the full format list. Also: "
                "regular videos are throttled by YouTube's anti-bot / "
                "PO Token checks far more than Shorts, so keep your "
                "cookies fresh on the Cookies tab if quality looks low "
                "only on normal videos.")
        else:
            self.env_warning.setVisible(False)

    def _on_url_changed(self, text: str):
        svc = (detect_service(text.strip()) if text.strip()
               else ServiceType.GENERIC)
        self._current_service = svc
        emoji = SERVICE_EMOJIS.get(svc, "🔗")
        name = (SERVICE_NAMES.get(svc, "Unknown") if text.strip()
                else "No link yet")
        self.service_badge.setText(f"{emoji}  {name}")
        is_spotify = svc == ServiceType.SPOTIFY
        self.spotify_group_widget.setVisible(is_spotify)
        forced_audio = svc in (ServiceType.SOUNDCLOUD, ServiceType.RADIOJAVAN)
        self.audio_checkbox.setVisible(
            svc in (ServiceType.YOUTUBE, ServiceType.TIKTOK)
            and not forced_audio)

    def _audio_flag(self) -> bool:
        if self._current_service == ServiceType.SPOTIFY:
            return self.radio_audio.isChecked()
        if self._current_service in (ServiceType.SOUNDCLOUD,
                                     ServiceType.RADIOJAVAN):
            return True
        if self._current_service in (ServiceType.YOUTUBE, ServiceType.TIKTOK):
            return self.audio_checkbox.isChecked()
        return False

    def _start_download(self):
        url = self.url_input.text().strip()
        if not url:
            SweetAlert.show("warning", "No link yet",
                            "Paste a media URL before starting a download.",
                            parent=self.window())
            return
        svc = detect_service(url)
        downloader = get_downloader(svc, self.config, self.cookie_manager)
        if downloader is None:
            SweetAlert.show("error", "Unsupported link",
                            f"No downloader is available for: "
                            f"{SERVICE_NAMES.get(svc, url)}",
                            parent=self.window())
            return
        self._current_service = svc
        self._pending_url = url
        self.current_downloader = downloader
        self.download_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Fetching formats…")
        self._log_line(
            f"Detected: {SERVICE_EMOJIS.get(svc)} {SERVICE_NAMES.get(svc)}",
            "info")
        self._log_line("Fetching available formats…", "info")
        self.format_worker = FormatFetchWorker(downloader, url)
        self.format_worker.finished_signal.connect(self._on_formats_fetched)
        self.format_worker.start()

    def _on_formats_fetched(self, formats: Optional[List[Dict]], error: str):
        if error:
            self._log_line(f"Could not fetch formats: {error}", "warning")

        downloader = self.current_downloader
        if (downloader is not None
                and getattr(downloader, "_fatal_error", None)):
            fatal = downloader._fatal_error
            self.download_btn.setEnabled(True)
            self.status_label.setText("Blocked — see log")
            self.current_downloader = None
            if fatal == "cookie_lock":
                SweetAlert.show(
                    "error", "Browser cookies unavailable",
                    "Close your browser completely and try again, or "
                    "import a cookies.txt file on the Cookies tab.",
                    parent=self.window())
            elif fatal == "cookies_expired":
                SweetAlert.show(
                    "error", "Cookies expired",
                    "Your YouTube cookies were rotated/expired. Export a "
                    "fresh cookies.txt and import it on the Cookies tab.",
                    parent=self.window())
            elif fatal == "bot_check":
                SweetAlert.show(
                    "error", "Blocked by YouTube",
                    "This looks like a bot check. Export a fresh "
                    "cookies.txt and import it on the Cookies tab.",
                    parent=self.window())
            return

        audio_only = self._audio_flag()

        if not formats:
            self._log_line(
                "No format list — downloading with best available quality.", "info")
            self._launch_download(quality="best", format_id=None, preferred_client=None)
            return

        if audio_only:
            display_formats = [
                f for f in formats
                if f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none")]
            if not display_formats:
                display_formats = formats
        else:
            display_formats = formats

        dialog = FormatSelectionDialog(display_formats, self.window())
        selected_format, selected_client = dialog.get_selected_format()
        if not selected_format:
            self._log_line("No format selected — cancelled.", "info")
            self.download_btn.setEnabled(True)
            self.status_label.setText("Idle")
            self.current_downloader = None
            return

        self._selected_format_id = selected_format
        self._selected_client = selected_client
        self._log_line(f"Selected format: {selected_format} (clients: {selected_client})", "info")

        self._launch_download(quality="best", format_id=selected_format, preferred_client=selected_client)

    def _launch_download(self, quality: str, format_id: Optional[str], preferred_client: Optional[str]):
        downloader = self.current_downloader
        if downloader is None:
            self.download_btn.setEnabled(True)
            return
        audio_only = self._audio_flag()
        self.status_label.setText("Starting…")
        self.cancel_btn.setEnabled(True)
        self.worker = DownloadWorker(
            downloader, self._pending_url, quality, audio_only, format_id, preferred_client)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

    def _cancel_download(self):
        if self.current_downloader:
            self.current_downloader.cancel()
            self.status_label.setText("Cancelling…")
            self.cancel_btn.setEnabled(False)
        if self.format_worker and self.format_worker.isRunning():
            self.format_worker.stop()

    def _on_finished(self, ok: bool, err: str):
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if ok:
            self.status_label.setText("Completed ✓")
            self.progress_bar.setValue(100)
            self._log_line("Download completed successfully!", "success")
            SweetAlert.show("success", "Download complete",
                            f"Saved to: {self.config.download_dir}",
                            parent=self.window())
        else:
            self.status_label.setText("Failed" if err else "Idle")
            if err:
                self._log_line(err, "error")
                SweetAlert.show("error", "Download failed/canceled", err,
                                parent=self.window())
        self.current_downloader = None
        self.worker = None

    def _on_log(self, text: str, level: str):
        self._log_line(text, level)

    def _log_line(self, text: str, level: str):
        colors = {"info": "#8fb4ff", "success": "#7ee0a8",
                  "warning": "#f5c86a", "error": "#ff8a75"}
        color = colors.get(level, "#c7cfdd")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_console.append(
            f'<span style="color:{color}">[{ts}] {text}</span>')

    def _on_progress(self, pct: float, speed: float, eta: int, label: str):
        self.progress_bar.setValue(int(max(0, min(100, pct))))
        eta_s = f" · ETA {eta}s" if eta >= 0 else ""
        spd_s = f"{speed:.2f} MB/s" if speed > 0 else ""
        self.status_label.setText(f"{pct:5.1f}%  {spd_s}{eta_s}")


# ══════════════════════════════════════════════════════════════════════════
#  Settings Page
# ══════════════════════════════════════════════════════════════════════════

class SettingsPage(QWidget):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        heading = QLabel("Settings")
        heading.setObjectName("HeadingLabel")
        root.addWidget(heading)

        card = make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(14)

        cl.addWidget(self._section("DOWNLOAD DIRECTORY"))
        dir_row = QHBoxLayout()
        self.dir_input = QLineEdit(str(self.config.download_dir))
        browse_btn = make_secondary_button("Browse…")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dir_input, 1)
        dir_row.addWidget(browse_btn)
        cl.addLayout(dir_row)

        cl.addWidget(self._section("TRANSFER OPTIONS"))
        num_row = QHBoxLayout()
        self.concurrent_spin = self._spin(1, 10, self.config.max_concurrent)
        self.retry_spin = self._spin(0, 20, self.config.retry_count)
        self.timeout_spin = self._spin(5, 300, self.config.timeout)
        num_row.addLayout(
            self._labeled("Max concurrent", self.concurrent_spin))
        num_row.addLayout(self._labeled("Retry count", self.retry_spin))
        num_row.addLayout(self._labeled("Timeout (s)", self.timeout_spin))
        cl.addLayout(num_row)

        cl.addWidget(self._section("BROWSER COOKIES"))
        bc_row = QHBoxLayout()
        self.browser_checkbox = QCheckBox(
            "Use cookies directly from a browser")
        self.browser_checkbox.setChecked(self.config.use_browser_cookies)
        self.browser_combo = QComboBox()
        self.browser_combo.addItems(
            ["chrome", "firefox", "edge", "brave", "opera", "safari"])
        self.browser_combo.setCurrentText(self.config.browser_name)
        bc_row.addWidget(self.browser_checkbox, 1)
        bc_row.addWidget(self.browser_combo)
        cl.addLayout(bc_row)
        root.addWidget(card)

        # ── Environment card ──
        env_card = make_card()
        ecl = QVBoxLayout(env_card)
        ecl.setContentsMargins(20, 18, 20, 18)
        ecl.setSpacing(10)
        ecl.addWidget(self._section("ENVIRONMENT"))

        ffmpeg_ok = ffmpeg_available()
        js_rt = _build_js_runtimes()
        has_real_rt = js_rt and any(
            v.get("path") is not None for v in js_rt.values())
        rt_names = ", ".join(js_rt.keys()) if js_rt else "none"
        yt_dlp_ver = (getattr(yt_dlp.version, "__version__", "unknown")
                      if yt_dlp else "not installed")
        try:
            import yt_dlp_ejs
            ejs_ok = True
        except ImportError:
            ejs_ok = False

        for line in [
            f"ffmpeg: {'✓ found' if ffmpeg_ok else '✗ not found'}",
            f"JS runtime: {'✓ ' + rt_names if has_real_rt else '✗ not found'}",
            f"yt-dlp-ejs: {'✓ installed' if ejs_ok else '✗ missing (pip install yt-dlp-ejs)'}",
            f"yt-dlp version: {yt_dlp_ver}",
            f"YouTube clients used (list + download): {', '.join(YouTubeDownloader.COMBINED_CLIENTS)}",
        ]:
            l = QLabel(line)
            l.setObjectName("SubLabel")
            ecl.addWidget(l)
        root.addWidget(env_card)

        save_btn = make_primary_button("💾  Save Settings")
        save_btn.clicked.connect(self._save)
        root.addWidget(save_btn, 0, Qt.AlignLeft)
        root.addStretch(1)

    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("SectionLabel"); return lbl

    @staticmethod
    def _spin(lo: int, hi: int, val: int) -> QSpinBox:
        s = QSpinBox(); s.setRange(lo, hi); s.setValue(val); return s

    @staticmethod
    def _labeled(text: str, widget: QWidget) -> QVBoxLayout:
        lay = QVBoxLayout()
        lbl = QLabel(text); lbl.setObjectName("FieldLabel")
        lay.addWidget(lbl); lay.addWidget(widget)
        return lay

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Choose download folder", self.dir_input.text())
        if d:
            self.dir_input.setText(d)

    def _save(self):
        try:
            new_dir = Path(self.dir_input.text()).expanduser()
            new_dir.mkdir(parents=True, exist_ok=True)
            self.config.download_dir = new_dir
            self.config.max_concurrent = self.concurrent_spin.value()
            self.config.retry_count = self.retry_spin.value()
            self.config.timeout = self.timeout_spin.value()
            self.config.use_browser_cookies = self.browser_checkbox.isChecked()
            self.config.browser_name = self.browser_combo.currentText()
            print_success("Settings saved.")
            SweetAlert.show("success", "Settings saved",
                            parent=self.window())
        except Exception as e:
            print_error(f"Failed to save settings: {e}")
            SweetAlert.show("error", "Could not save settings", str(e),
                            parent=self.window())


# ══════════════════════════════════════════════════════════════════════════
#  Cookies Page
# ══════════════════════════════════════════════════════════════════════════

class CookiesPage(QWidget):
    def __init__(self, cookie_manager: CookieManager, config: Config):
        super().__init__()
        self.cookie_manager = cookie_manager
        self.config = config

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        heading = QLabel("YouTube Cookies")
        heading.setObjectName("HeadingLabel")
        sub = QLabel("Cookies let the downloader access age-restricted "
                     "or login-gated videos, and also reduce YouTube's "
                     "PO Token / anti-bot throttling on regular (non-Shorts) "
                     "videos. Keep them fresh — YouTube rotates them "
                     "periodically.")
        sub.setObjectName("SubLabel")
        sub.setWordWrap(True)
        root.addWidget(heading)
        root.addWidget(sub)

        card = make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(12)

        self.status_label = QLabel()
        self.status_label.setObjectName("FieldLabel")
        self.location_label = QLabel()
        self.location_label.setObjectName("SubLabel")
        self.location_label.setWordWrap(True)
        cl.addWidget(self.status_label)
        cl.addWidget(self.location_label)

        btn_row = QHBoxLayout()
        import_btn = make_secondary_button("📥 Import cookies.txt")
        validate_btn = make_secondary_button("✓ Validate")
        delete_btn = make_danger_button("🗑 Delete")
        import_btn.clicked.connect(self._import)
        validate_btn.clicked.connect(self._validate)
        delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(import_btn)
        btn_row.addWidget(validate_btn)
        btn_row.addWidget(delete_btn)
        cl.addLayout(btn_row)
        root.addWidget(card)

        card2 = make_card()
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(20, 18, 20, 18)
        c2.setSpacing(12)
        c2.addWidget(self._section("RESET"))
        reset_row = QHBoxLayout()
        reset_lbl = QLabel(
            "Delete all stored cookies and app data, starting fresh.")
        reset_lbl.setObjectName("SubLabel")
        reset_btn = make_danger_button("⚠ Reset All Data")
        reset_btn.clicked.connect(self._reset_all)
        reset_row.addWidget(reset_lbl, 1)
        reset_row.addWidget(reset_btn)
        c2.addLayout(reset_row)
        root.addWidget(card2)
        root.addStretch(1)

        BUS.cookie_state_changed.connect(self._refresh)
        self._refresh()

    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("SectionLabel"); return lbl

    def _refresh(self):
        ready = self.cookie_manager.has_cookies()
        self.status_label.setText(
            "Status: Available ✓" if ready else "Status: Not available")
        self.location_label.setText(
            self.cookie_manager.cookie_location_text())

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select cookies.txt", str(Path.home()),
            "Text files (*.txt);;All files (*)")
        if not path:
            return
        if self.cookie_manager.import_cookie_file(Path(path)):
            self._refresh()
            SweetAlert.show("success", "Cookies imported",
                            "Your cookies were imported and validated.",
                            parent=self.window())
        else:
            SweetAlert.show("error", "Import failed",
                            "The cookie file appears invalid or expired.",
                            parent=self.window())

    def _validate(self):
        print_info("Validating cookies…")
        ok = self.cookie_manager.validate_cookies()
        if ok:
            SweetAlert.show("success", "Cookies are valid",
                            parent=self.window())
        else:
            SweetAlert.show("error", "Cookies are invalid",
                            "They may be missing or expired — check the "
                            "activity log for details.",
                            parent=self.window())

    def _delete(self):
        if SweetAlert.confirm(
                "Delete cookies?",
                "This removes the stored YouTube cookie file.",
                parent=self.window(), confirm_text="Delete"):
            self.cookie_manager.delete_cookies()
            self._refresh()
            SweetAlert.show("success", "Cookies deleted", parent=self.window())

    def _reset_all(self):
        if SweetAlert.confirm(
                "Reset all data?",
                f"This permanently deletes:\n{self.config.config_dir}",
                parent=self.window(), confirm_text="Reset"):
            self.cookie_manager.full_reset()
            self._refresh()
            SweetAlert.show("success", "All data reset", parent=self.window())


# ══════════════════════════════════════════════════════════════════════════
#  Services Page
# ══════════════════════════════════════════════════════════════════════════

class ServicesPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        heading = QLabel("Supported Services")
        heading.setObjectName("HeadingLabel")
        root.addWidget(heading)

        table = QTableWidget()
        rows = [(svc, pats) for svc, pats in PLATFORM_PATTERNS.items()]
        table.setRowCount(len(rows))
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(
            ["Service", "", "Example URL Pattern"])
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        for r, (svc, pats) in enumerate(rows):
            table.setItem(r, 0, QTableWidgetItem(
                SERVICE_NAMES.get(svc, svc.value)))
            table.setItem(r, 1, QTableWidgetItem(
                SERVICE_EMOJIS.get(svc, "🔗")))
            table.setItem(r, 2, QTableWidgetItem(
                pats[0] if pats else ""))
        root.addWidget(table, 1)


# ══════════════════════════════════════════════════════════════════════════
#  Main Window
# ══════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(980, 680)
        self.setMinimumSize(820, 560)

        self.config = Config()
        self.cookie_manager = CookieManager(self.config)

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 160))
        root.setGraphicsEffect(shadow)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self))

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(14, 18, 14, 18)
        side_lay.setSpacing(6)

        self.stack = QStackedWidget()
        self.download_page = DownloadPage(self.config, self.cookie_manager)
        self.settings_page = SettingsPage(self.config)
        self.cookies_page = CookiesPage(self.cookie_manager, self.config)
        self.services_page = ServicesPage()

        for page in (self.download_page, self.settings_page,
                     self.cookies_page, self.services_page):
            self.stack.addWidget(page)

        self.nav_buttons: List[QPushButton] = []
        nav_items = [
            ("⬇  Download", self.download_page),
            ("⚙  Settings", self.settings_page),
            ("🍪  Cookies", self.cookies_page),
            ("📋  Services", self.services_page),
        ]
        for text, page in nav_items:
            btn = QPushButton(text)
            btn.setObjectName("NavButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _, p=page, b=btn: self._switch_page(p, b))
            side_lay.addWidget(btn)
            self.nav_buttons.append(btn)
        side_lay.addStretch(1)

        grip_row = QHBoxLayout()
        grip_row.addStretch(1)
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        grip_row.addWidget(grip)
        side_lay.addLayout(grip_row)

        body.addWidget(sidebar)
        body.addWidget(self.stack, 1)

        self.nav_buttons[0].setChecked(True)
        self.nav_buttons[0].setProperty("active", "true")
        self.stack.setCurrentWidget(self.download_page)

        self._loading_modal: Optional[LoadingModal] = None
        self._config_worker: Optional[ConfigCheckWorker] = None
        QTimer.singleShot(150, self._run_startup_checks)

    def _run_startup_checks(self) -> None:
        self._loading_modal = LoadingModal("Checking configuration", parent=self)
        self._loading_modal.show()
        self._config_worker = ConfigCheckWorker(self.config, self.cookie_manager)
        self._config_worker.finished_signal.connect(self._on_startup_checks_done)
        self._config_worker.start()

    def _on_startup_checks_done(self, issues: List[Dict[str, str]]) -> None:
        if self._loading_modal is not None:
            self._loading_modal.close()
            self._loading_modal = None

        if not issues:
            return

        modal = ConfigRequiredModal(issues, parent=self)
        modal.exec()
        if modal.chosen_target == "settings":
            self._switch_page(self.settings_page, self.nav_buttons[1])
        elif modal.chosen_target == "cookies":
            self._switch_page(self.cookies_page, self.nav_buttons[2])

    def _switch_page(self, page: QWidget, active_btn: QPushButton):
        for b in self.nav_buttons:
            b.setChecked(b is active_btn)
            b.setProperty("active",
                          "true" if b is active_btn else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        self.stack.setCurrentWidget(page)

    def closeEvent(self, event):
        if hasattr(self, "download_page"):
            if (self.download_page.format_worker
                    and self.download_page.format_worker.isRunning()):
                self.download_page.format_worker.stop()
                self.download_page.format_worker.wait(2000)
            if (self.download_page.worker
                    and self.download_page.worker.isRunning()):
                if self.download_page.current_downloader:
                    self.download_page.current_downloader.cancel()
                self.download_page.worker.wait(2000)
        event.accept()


# ══════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())