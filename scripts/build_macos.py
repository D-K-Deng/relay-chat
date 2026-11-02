"""Assemble Finder apps with relocatable CPython, on macOS or with Zig cc."""
import argparse
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from release_files import GUIDES

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build/macos-cache"
DIST = ROOT / "dist"
RELEASE = "20261026"
PYTHON = "3.12.16"
CONTENTS = "RelayChat/RelayChat.app/Contents/"
TARGETS = {"arm64": ("aarch64", "AppleSilicon"), "x86_64": ("x86_64", "Intel")}


def runtime_archive(target):
    name = f"cpython-{PYTHON}+{RELEASE}-{target}-apple-darwin-install_only_stripped.tar.gz"
    archive = CACHE / name
    if not archive.is_file():
        url = "https://github.com/astral-sh/python-build-standalone/releases/download/" + RELEASE + "/" + urllib.parse.quote(name)
        download = archive.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as response, download.open("wb") as out:
            shutil.copyfileobj(response, out)
        download.replace(archive)
    return archive


def build_launcher(arch, zig):
    target = TARGETS[arch][0]
    launcher = CACHE / ("RelayChat-" + arch)
    if zig:
        cc = [zig, "cc", "-target", target + "-macos.11.0"]
    elif sys.platform == "darwin":
        cc = ["xcrun", "clang", "-arch", arch, "-mmacosx-version-min=11.0"]
    else:
        raise SystemExit("Pass --zig PATH_TO_ZIG to cross-build outside macOS.")
    subprocess.run([*cc, "-Os", str(ROOT / "assets/macos/launcher.c"), "-o", str(launcher)], check=True)
    return launcher


def add(archive, name, data, mode=stat.S_IFREG | 0o644):
    entry = zipfile.ZipInfo(name)
    entry.create_system = 3
    entry.external_attr = mode << 16
    archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)


def add_runtime(archive, source):
    # Write Unix permissions and relative symlinks into the ZIP even on Windows.
    with tarfile.open(source, "r:gz") as runtime:
        for member in runtime:
            relative = member.name.removeprefix("python/")
            name = CONTENTS + "Resources/runtime/" + relative
            if member.issym():
                add(archive, name, member.linkname.encode(), stat.S_IFLNK | 0o777)
            elif member.isfile() or member.islnk():
                with runtime.extractfile(member) as stream:
                    add(archive, name, stream.read(), stat.S_IFREG | member.mode)


def package(arch, launcher):
    target, label = TARGETS[arch]
    source = runtime_archive(target)
    info = {
        "CFBundleDevelopmentRegion": "en", "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleIdentifier": "local.relaychat.desktop", "CFBundleName": "RelayChat",
        "CFBundleDisplayName": "Relay Chat", "CFBundleExecutable": "RelayChat",
        "CFBundleIconFile": "relay.icns", "CFBundleShortVersionString": "1.1.0",
        "CFBundleVersion": "2", "LSMinimumSystemVersion": "11.0", "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    output = DIST / f"RelayChat-macOS-{label}.zip"
    building = output.with_suffix(".part")
    with zipfile.ZipFile(building, "w") as archive:
        add(archive, CONTENTS + "Info.plist", plistlib.dumps(info))
        add(archive, CONTENTS + "MacOS/RelayChat", launcher.read_bytes(), stat.S_IFREG | 0o755)
        add(archive, CONTENTS + "Resources/relay.icns", (ROOT / "assets/relay.icns").read_bytes())
        notices = ROOT / "assets/macos/runtime-notices.txt"
        add(archive, CONTENTS + "Resources/runtime-notices.txt", notices.read_bytes())
        add(archive, "RelayChat/Start.command", (ROOT / "Start.command").read_bytes().replace(b"\r\n", b"\n"), stat.S_IFREG | 0o755)
        for name in GUIDES:
            add(archive, "RelayChat/" + name, (ROOT / name).read_bytes())
        for pattern in ("app.py", "relay/*.py", "web/*.js", "web/*.css", "web/*.html", "web/*.svg", "web/prompt.txt"):
            for path in sorted(ROOT.glob(pattern)):
                add(archive, CONTENTS + "Resources/app/" + path.relative_to(ROOT).as_posix(), path.read_bytes())
        add_runtime(archive, source)
    building.replace(output)
    print(output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=TARGETS, help="default: build both Mac packages")
    parser.add_argument("--zig", help="path to Zig for cross-compiling the Finder launcher")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(exist_ok=True)
    for arch in ([args.arch] if args.arch else TARGETS):
        package(arch, build_launcher(arch, args.zig))
