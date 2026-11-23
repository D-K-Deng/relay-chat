"""Run the distributed app from an extracted release, with no external API calls."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relay.config import APP_VERSION


@contextmanager
def launch(command, env, folder, client, url, desktop):
    process = subprocess.Popen(command, env=env, cwd=folder)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Desktop launcher exited with code {process.returncode} before Relay was ready.")
            try:
                with client.open(url + "/api/status", timeout=1) as response:
                    status = json.load(response)
                if not desktop or status["desktop_window"]:
                    break
            except OSError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError("The packaged app did not finish loading within 45 seconds.")
        yield status
        with client.open(urllib.request.Request(url + "/api/shutdown", data=b"{}",
                         headers={"Content-Type": "application/json"})) as response:
            assert json.load(response)["ok"]
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def smoke(archive, work):
    distribution = work / "Download with spaces"
    distribution.mkdir()
    data = work / "Local user data"
    env = dict(os.environ, RELAY_DATA_DIR=str(data), CODEX_HOME=str(work / "Codex fixture"))
    env.pop("OPENAI_API_KEY", None)
    folder = distribution / "RelayChat"
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/ditto", "-x", "-k", archive, distribution], check=True)
        setup = folder / "Start.command"
    else:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(distribution)
        setup = folder / "RelayChat.exe"

    if sys.platform == "darwin":
        result = subprocess.check_output([setup, "--doctor"], env=env)
        assert b"not configured" in result, result
    else:
        # The Windows desktop app has no console or standard output.
        subprocess.run([setup, "--doctor"], env=env, check=True)
    with socket.socket() as free_port:
        free_port.bind(("127.0.0.1", 0))
        port = free_port.getsockname()[1]
    args = ["--port", str(port)]
    if sys.platform == "darwin":
        # LaunchServices may run the app away from its original setup folder.
        app = (folder / "RelayChat.app").rename(work / "RelayChat.app")
        command = ["/usr/bin/open", "-n", "-W", "--stderr", str(work / "desktop-stderr.log"),
                   "--env", "RELAY_DATA_DIR=" + str(data), "--env", "OPENAI_API_KEY=",
                   "-a", str(app), "--args", *args]
    else:
        command = [str(setup), *args]
    url = f"http://127.0.0.1:{port}"
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with launch(command, env, distribution, client, url, desktop=True) as status:
        assert status["product"] == "relay-chat" and not status["configured"]
        assert status["version"] == APP_VERSION and status["settings"]["auto_wait_rate_limits"] is True
        if sys.platform == "win32":
            subprocess.run([setup, *args], env=env, check=True, timeout=15)
        for path, content in (("/", b"response-settings"), ("/app.js", b"openChat"),
                              ("/i18n.js", b"setLanguage"), ("/system-messages.js", b"systemMessage"),
                              ("/prompt.txt", b"OpenAI Developers")):
            with client.open(url + path) as response:
                assert content in response.read(), path
    (folder / ".env.local").write_text("OPENAI_API_KEY=local-smoke-fixture\n", encoding="utf-8")
    with launch(command + ["--no-open"], env, distribution, client, url, desktop=False) as status:
        assert status["desktop_window"] is None and status["key_source"] == ".env.local"
    print("Desktop package OK:", archive.name)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        try:
            smoke(Path(sys.argv[1]).resolve(), work)
        except Exception:
            log = work / "desktop-stderr.log"
            if log.is_file():
                print(log.read_text(errors="replace"), file=sys.stderr)
            raise
