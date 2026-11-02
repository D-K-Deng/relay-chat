"""Build the standalone Windows app and a shareable release from public files only."""
import subprocess
import sys
import zipfile
from pathlib import Path
from release_files import GUIDES

root = Path(__file__).resolve().parents[1]
if sys.platform != "win32":
    raise SystemExit("Build the Windows executable on Windows.")
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
                "--hide-console", "hide-early", "--name", "RelayChat", "--icon", "assets/relay.ico",
                "--add-data", "web;web", "app.py"], cwd=root, check=True)
release = root / "dist/RelayChat-Windows-x64.zip"
with zipfile.ZipFile(release, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    z.write(root / "dist/RelayChat.exe", "RelayChat/RelayChat.exe")
    for name in GUIDES:
        z.write(root / name, "RelayChat/" + name)
print(release)
