"""Build the standalone Windows app and a shareable release from public files only."""
import os
import shutil
import subprocess
import sys
import zipfile
from importlib.util import find_spec
from pathlib import Path
from release_files import GUIDES

root = Path(__file__).resolve().parents[1]
if sys.platform != "win32":
    raise SystemExit("Build the Windows executable on Windows.")
from windows_update import install

if find_spec("PySide6") is None:
    raise SystemExit("Install the desktop dependencies with python -m pip install -r requirements-desktop.txt.")
stage = root / "build/windows-output"
windows = Path(os.environ["SystemRoot"])
# PyInstaller scans PATH for DLLs. An unrelated ICU from other desktop software
# can shadow Windows ICU and prevent QtCore from loading.
build_env = dict(os.environ, PATH=os.pathsep.join(map(str, (
    Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs", windows / "System32", windows,
))))
subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir",
                "--windowed", "--name", "RelayChat", "--icon", str(root / "assets/relay.ico"),
                "--distpath", str(stage), "--workpath", "build/windows-freeze", "--specpath", "build/windows-spec",
                "--exclude-module", "PySide6.QtQuick", "--exclude-module", "PySide6.QtQml",
                "--copy-metadata", "PySide6-Addons", "--copy-metadata", "PySide6-Essentials", "--copy-metadata", "shiboken6",
                "--add-data", str(root / "web") + ":web", "--add-data", str(root / "assets/relay.ico") + ":assets",
                str(root / "app.py")], cwd=root, env=build_env, check=True)
app = stage / "RelayChat"
release = root / "dist/RelayChat-Windows-x64.zip"
release.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(release.with_suffix(".part"), "w", zipfile.ZIP_DEFLATED, compresslevel=4) as z:
    for file in app.rglob("*"):
        if file.is_file():
            z.write(file, "RelayChat/" + file.relative_to(app).as_posix())
    for name in GUIDES:
        z.write(root / name, "RelayChat/" + name)
release.with_suffix(".part").replace(release)
shutil.copy2(root / "scripts/open_windows_build.cmd", release.parent / "Open Relay Chat.cmd")
state = install(release.parent, wait=False)
if state == "locked":
    subprocess.Popen([sys.executable, str(root / "scripts/windows_update.py")],
                     creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if state != "installed":
    print("The new build will replace dist/RelayChat.exe when the running app exits.")
    print("To apply it and open Relay, use dist/Open Relay Chat.cmd.")
print(release)
