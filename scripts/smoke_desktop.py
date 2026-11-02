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
from pathlib import Path


def smoke(archive, work):
    distribution = work / "Download with spaces"
    distribution.mkdir()
    data = work / "Local user data"
    env = dict(os.environ, RELAY_DATA_DIR=str(data))
    env.pop("OPENAI_API_KEY", None)
    folder = distribution / "RelayChat"
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/ditto", "-x", "-k", archive, distribution], check=True)
        setup = folder / "Start.command"
    else:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(distribution)
        setup = folder / "RelayChat.exe"

    result = subprocess.check_output([setup, "--doctor"], env=env)
    assert b"not configured" in result, result
    (folder / ".env.local").write_text("OPENAI_API_KEY=local-smoke-fixture\n", encoding="utf-8")
    with socket.socket() as free_port:
        free_port.bind(("127.0.0.1", 0))
        port = free_port.getsockname()[1]
    args = ["--no-open", "--port", str(port)]
    if sys.platform == "darwin":
        # LaunchServices may run the app away from its original setup folder.
        app = (folder / "RelayChat.app").rename(work / "RelayChat.app")
        command = ["/usr/bin/open", "-n", "-W", "--env", "RELAY_DATA_DIR=" + str(data),
                   "-a", str(app), "--args", *args]
    else:
        command = [str(setup), *args]
    process = subprocess.Popen(command, env=env)
    url = f"http://127.0.0.1:{port}"
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Desktop launcher exited before the service was ready.")
            try:
                with client.open(url + "/api/status", timeout=1) as response:
                    status = json.load(response)
                break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError("Desktop app did not start within 45 seconds.")
        assert status["product"] == "relay-chat" and status["configured"]
        assert status["key_source"] == ".env.local"
        for path, content in (("/", b"Relay"), ("/app.js", b"openChat"), ("/prompt.txt", b"OpenAI Developers")):
            with client.open(url + path) as response:
                assert content in response.read(), path
        with client.open(urllib.request.Request(url + "/api/shutdown", data=b"{}",
                         headers={"Content-Type": "application/json"})) as response:
            assert json.load(response)["ok"]
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    print("Desktop package OK:", archive.name)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        smoke(Path(sys.argv[1]).resolve(), Path(temporary))
