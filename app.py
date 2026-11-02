#!/usr/bin/env python3
"""Launch Relay, or check the local setup without showing a secret."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from relay import config, provider
from relay.server import RelayServer
from relay.storage import Store


def open_window(url, browser=False):
    if not browser:
        if sys.platform == "win32":
            apps = [Path(os.getenv(root, "")) / exe for root in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
                    for exe in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")]
        elif sys.platform == "darwin":
            apps = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
        else:
            apps = [Path(p) for name in ("google-chrome", "chromium", "microsoft-edge") if (p := shutil.which(name))]
        for exe in apps:
            if exe.is_file():
                subprocess.Popen([str(exe), "--app=" + url, "--window-size=1240,840"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    if sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", url])
    else:
        webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="Relay Chat | Local API workspace")
    parser.add_argument("--no-open", action="store_true", help="Start the local service without opening a window")
    parser.add_argument("--browser", action="store_true", help="Open in your default browser")
    parser.add_argument("--port", type=int, default=8421)
    parser.add_argument("--doctor", action="store_true", help="Check local setup without a network request")
    parser.add_argument("--test-api", action="store_true", help="Send one short, billable request to OpenAI")
    args = parser.parse_args()
    if args.doctor:
        key, source = config.api_key()
        print("API Key:", ("configured (" + source + ")") if key else "not configured")
        print("Local data:", config.data_dir())
        print("Codex sessions:", "found" if (config.codex_dir() / "sessions").is_dir() else "not found (optional)")
        return 0
    if args.test_api:
        key, _ = config.api_key()
        if not key:
            print("API Key not configured. See README.md.")
            return 1
        store = Store(config.data_dir())
        settings = store.settings() | {"max_output": 256, "instructions": ""}
        ticket = store.meter_ticket(settings["model"])
        body = provider.request_body([{"role": "user", "text": "Reply with only: OK"}], settings)
        got_text = False
        for kind, value in provider.events(key, body, threading.Event()):
            if kind == "delta":
                got_text = got_text or bool(value.strip())
            elif kind == "end":
                store.record_usage(ticket, value)
                if got_text and value["status"] == "done":
                    print("Responses API OK:", settings["model"])
                    return 0
            elif kind in ("error", "rejected"):
                if kind == "error":
                    store.record_usage(ticket, {})
                print(value)
                return 1
        print("No complete text reply received.")
        return 1
    url = f"http://127.0.0.1:{args.port}"
    try:
        server = RelayServer(args.port)
    except OSError:
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2) as r:
                running = json.load(r).get("product") == "relay-chat"
        except (OSError, ValueError):
            running = False
        if running:
            if not args.no_open:
                open_window(url, args.browser)
            print("Relay is already running:", url)
            return 0
        print("Cannot start Relay on this port. Try --port 8422.")
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print("Relay:", url, "\nStop: Ctrl+C or Settings > Exit.", flush=True)
    if not args.no_open:
        threading.Timer(.25, open_window, args=(url, args.browser)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
