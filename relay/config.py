import os
import sys
from pathlib import Path


ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
if getattr(sys, "frozen", False) and ROOT.name == "dist" and (ROOT.parent / "app.py").is_file():
    ROOT = ROOT.parent
ASSETS = Path(getattr(sys, "_MEIPASS", ROOT)) / "web"
DEFAULT_MODEL = "gpt-5.4-mini"
MAC_APP = ROOT.parent.parent.parent if sys.platform == "darwin" and ROOT.parent.name == "Resources" and ROOT.parent.parent.name == "Contents" else None


def data_dir():
    if os.getenv("RELAY_DATA_DIR"):
        return Path(os.environ["RELAY_DATA_DIR"]).expanduser()
    if sys.platform == "win32":
        parent = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        parent = Path.home() / "Library/Application Support"
    else:
        parent = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share")
    return parent / "RelayChat"


def key_file():
    if MAC_APP:
        # Finder can launch a downloaded app from a temporary, read-only location.
        # Start.command remembers the real setup folder without copying its key.
        marker = data_dir() / "setup-folder.txt"
        folder = Path(marker.read_text(encoding="utf-8").rstrip("\r\n")) if marker.is_file() else MAC_APP.parent
        return folder / ".env.local"
    return ROOT / ".env.local"


def api_key():
    path = key_file()
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "OPENAI_API_KEY":
                value = value.strip().strip("\"'")
                if value:
                    return value, ".env.local"
    value = os.getenv("OPENAI_API_KEY", "").strip()
    return value, "environment" if value else ""


def codex_dir():
    return Path(os.getenv("CODEX_HOME") or Path.home() / ".codex").expanduser()
