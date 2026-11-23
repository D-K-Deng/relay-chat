"""Install a checkout's Windows release at the path used by desktop shortcuts."""
import argparse
import msvcrt
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path


def replace_app(staged, destination, backup):
    """Hide the launcher first and expose it again once its runtime is in place."""
    launcher = destination / "RelayChat.exe"
    if launcher.exists():
        # Windows may allow a running image to be renamed. It disallows opening
        # the mapped executable for writing. Open without truncating or writing.
        with launcher.open("r+b"):
            pass
    moved = []
    try:
        for name in ("RelayChat.exe", "_internal"):
            active = destination / name
            if active.exists():
                active.rename(backup / name)
                moved.append((backup / name, active))
        for name in ("_internal", "RelayChat.exe"):
            (staged / name).rename(destination / name)
            moved.append((destination / name, staged / name))
    except OSError:
        for source, target in reversed(moved):
            source.rename(target)
        raise


def install(destination, *, wait):
    archive = destination / "RelayChat-Windows-x64.zip"
    receipt = destination / ".windows-build-installed"
    # Windows automatically releases this lock if a build or a waiting launcher exits.
    with (destination / ".windows-build-update.lock").open("a+b") as lock:
        lock.seek(0)
        while True:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except PermissionError:
                if not wait:
                    return "updating"
                time.sleep(1)
        while True:
            edition = archive.stat().st_mtime_ns
            if receipt.is_file() and receipt.read_text() == str(edition) and (destination / "RelayChat.exe").is_file():
                return "installed"
            with tempfile.TemporaryDirectory(dir=destination, prefix=".relay-update-", ignore_cleanup_errors=True) as temp:
                work = Path(temp)
                backup = work / "previous"
                backup.mkdir()
                with zipfile.ZipFile(archive) as package:
                    for member in package.namelist():
                        if member == "RelayChat/RelayChat.exe" or member.startswith("RelayChat/_internal/"):
                            package.extract(member, work)
                while archive.stat().st_mtime_ns == edition:
                    try:
                        replace_app(work / "RelayChat", destination, backup)
                    except PermissionError:
                        if not wait:
                            return "locked"
                        time.sleep(1)
                    else:
                        receipt.write_text(str(edition))
                        break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "dist"
    if args.launch:
        print("Applying the latest local build. If Relay is open, exit it to finish updating.", flush=True)
    install(destination, wait=True)
    if args.launch:
        subprocess.Popen([destination / "RelayChat.exe"], cwd=destination)


if __name__ == "__main__":
    main()
