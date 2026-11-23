#!/usr/bin/env python3
"""Launch Relay, or check the local setup without showing a secret."""
import argparse
import json
import re
import sys
import threading
import urllib.request
import webbrowser

from relay import config, provider
from relay.harness import metered_round
from relay.server import RelayServer
from relay.storage import Store


def instance_status(url):
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=1) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def older_instance(status):
    def release(version):
        return tuple(map(int, re.findall(r"\d+", version)[:3]))
    return status.get("product") == "relay-chat" and release(status.get("version", "")) < release(config.APP_VERSION)


def start_or_reuse(port, desktop, no_open):
    url = f"http://127.0.0.1:{port}"
    while True:
        try:
            return RelayServer(port), 0
        except OSError:
            status = instance_status(url)
        if status.get("product") != "relay-chat":
            print("Cannot start Relay on this port. Try --port 8422.")
            return None, 1
        if older_instance(status):
            chinese = status.get("settings", {}).get("language") == "zh-CN"
            text = (f"旧版 Relay Chat 仍在运行。请在原窗口完成工作后退出应用，{config.APP_VERSION} 版会自动打开。"
                    if chinese else f"A previous version of Relay Chat is open. Finish your work in that window and "
                    f"quit Relay Chat. Version {config.APP_VERSION} will then open automatically.")
            if desktop:
                if desktop.wait_for_exit(text, "取消" if chinese else "Cancel", lambda: older_instance(instance_status(url))):
                    continue  # Only the instance that wins the port opens the chat store.
                return None, 0
            print("An older Relay Chat is running. Quit it before launching this version.")
            return None, 1
        if not no_open:
            handle = status.get("desktop_window")
            if desktop:
                if handle is None:
                    desktop.show(url, lambda _: None, None)
                elif handle:
                    desktop.focus_window(handle)
            else:
                webbrowser.open(url)
        print("Relay is already running:", url)
        return None, 0


def run_window(desktop, url, server):
    """Keep the local service alive for the lifetime of its desktop window."""
    stopped = threading.Event()

    def serve():
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            stopped.set()

    server.desktop_window = 0
    service = threading.Thread(target=serve)
    service.start()
    try:
        desktop.show(url, lambda handle: setattr(server, "desktop_window", handle), stopped)
    finally:
        with server.lock:
            for _, cancel in server.running.values():
                cancel.set()
        server.shutdown()
        service.join()


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
        settings = store.settings() | {"max_output": 256, "instructions": "", "effort": "auto",
                                       "service_tier": "default", "reasoning_summary": False}
        ticket = store.meter_ticket(settings["model"])
        body = provider.request_body([{"role": "user", "text": "Reply with only: OK"}], settings)
        got_text = False
        for kind, value in metered_round(provider.events, key, body, threading.Event(), lambda result: store.record_usage(ticket, result)):
            if kind == "delta":
                got_text = got_text or bool(value.strip())
            elif kind == "end":
                if (got_text or value.get("text")) and value["status"] == "done":
                    print("Responses API OK:", settings["model"])
                    return 0
                if value.get("note"):
                    print(value["note"])
                    return 1
            elif kind in ("error", "rejected", "closed"):
                print(value)
                return 1
        print("No complete text reply received.")
        return 1
    desktop = None
    if not (args.no_open or args.browser):
        try:
            if sys.platform == "darwin":
                from relay import macos_desktop as desktop
            elif sys.platform == "win32":
                from relay import windows_desktop as desktop
        except ImportError as error:
            print("Install the desktop dependencies with python -m pip install -r requirements-desktop.txt.", file=sys.stderr)
            print(error, file=sys.stderr)
            return 1
    server, exit_code = start_or_reuse(args.port, desktop, args.no_open)
    if server is None:
        return exit_code
    url = f"http://127.0.0.1:{server.server_port}"
    print("Relay:", url, "\nStop: Ctrl+C or Settings > Exit.", flush=True)
    try:
        if desktop:
            run_window(desktop, url, server)
        else:
            if not args.no_open:
                threading.Timer(.25, webbrowser.open, args=(url,)).start()
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
