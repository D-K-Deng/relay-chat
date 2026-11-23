import sys
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

if sys.platform == "win32":
    from scripts.windows_update import install, replace_app


@unittest.skipUnless(sys.platform == "win32", "Windows file sharing")
class DesktopUpdate(unittest.TestCase):
    def test_running_image_keeps_its_frontend_until_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            exe = folder / "RelayChat.exe"
            shutil.copy2(Path(os.environ["SystemRoot"]) / "System32/cmd.exe", exe)
            runtime = folder / "_internal"
            runtime.mkdir()
            (runtime / "active.txt").touch()
            staged = folder / "staged"
            staged.mkdir()
            (staged / "_internal").mkdir()
            (staged / "RelayChat.exe").write_bytes(b"new executable")
            backup = folder / "previous"
            backup.mkdir()
            with subprocess.Popen([exe, "/d", "/q"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW) as process:
                try:
                    with self.assertRaises(PermissionError):
                        replace_app(staged, folder, backup)
                    self.assertTrue((runtime / "active.txt").exists())
                    self.assertFalse(list(backup.iterdir()))
                finally:
                    process.communicate(b"exit\r\n", timeout=5)
            replace_app(staged, folder, backup)
            self.assertEqual(exe.read_bytes(), b"new executable")

    def test_locked_launcher_then_update_in_place(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            exe = folder / "RelayChat.exe"
            runtime = folder / "_internal"
            exe.write_bytes(b"old executable")
            runtime.mkdir()
            (runtime / "old.txt").touch()
            config = folder / ".env.local"
            config.write_text("fixture configuration")
            with zipfile.ZipFile(folder / "RelayChat-Windows-x64.zip", "w") as archive:
                archive.writestr("RelayChat/RelayChat.exe", b"new executable")
                archive.writestr("RelayChat/_internal/web/app.js", b"new frontend")
                archive.writestr("RelayChat/README.md", b"portable guide")
            rename = Path.rename
            def locked_runtime(path, target):
                if path == runtime:
                    raise PermissionError("Runtime in use")
                return rename(path, target)
            with patch.object(Path, "rename", locked_runtime):
                self.assertEqual(install(folder, wait=False), "locked")
            self.assertEqual(exe.read_bytes(), b"old executable")
            done = []
            with exe.open("rb"):
                self.assertEqual(install(folder, wait=False), "locked")
                self.assertEqual(exe.read_bytes(), b"old executable")
                self.assertTrue((runtime / "old.txt").exists())
                waiting = threading.Thread(target=lambda: done.append(install(folder, wait=True)))
                waiting.start()
            waiting.join(10)
            self.assertEqual(done, ["installed"])
            self.assertEqual(exe.read_bytes(), b"new executable")
            self.assertEqual((runtime / "web/app.js").read_bytes(), b"new frontend")
            self.assertFalse((runtime / "old.txt").exists())
            self.assertEqual(config.read_text(), "fixture configuration")
            self.assertFalse((folder / "README.md").exists())
