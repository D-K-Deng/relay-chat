import json
import mimetypes
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from . import codex, config, provider
from .storage import Store, compact

MAX_BODY = 48 * 1024 * 1024


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, directory=None, upstream=None, key_source=None, codex_home=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = Store(directory or config.data_dir())
        self.upstream = upstream or provider.events
        self.key_source = key_source or config.api_key
        self.codex_home = codex_home or config.codex_dir()
        self.running = {}
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "Relay"

    def log_message(self, *args):
        pass  # Conversation text, filenames, and search queries never go to a log.

    def headers_for(self, status, mime, size=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        if size is not None:
            self.send_header("Content-Length", str(size))
        self.end_headers()

    def reply(self, data, status=200, mime="application/json; charset=utf-8"):
        payload = data if isinstance(data, bytes) else compact(data).encode()
        self.headers_for(status, mime, len(payload))
        self.wfile.write(payload)

    def route(self):
        s, store = self.server, self.server.store
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{s.server_port}", f"localhost:{s.server_port}"}
        if host not in allowed or self.headers.get("Origin", "http://" + host) != "http://" + host:
            return self.reply({"error": "Access is limited to the local Relay app."}, 403)
        u = urlsplit(self.path)
        path = u.path
        qs = parse_qs(u.query)
        if self.command == "GET":
            if path == "/api/status":
                key, source = s.key_source()
                return self.reply({"product": "relay-chat", "version": "1.1.0", "configured": bool(key),
                                   "key_source": source, "settings": store.settings()})
            if path == "/api/balance":
                return self.reply(store.balance())
            if path == "/api/chats":
                return self.reply(store.list_chats(qs.get("q", [""])[0]))
            if path.startswith("/api/chats/"):
                return self.reply(store.read(path.split("/")[-1]))
            if path == "/api/codex":
                return self.reply(codex.sessions(s.codex_home))
            if path.startswith("/api/codex/"):
                file = codex.session_path(s.codex_home, path.split("/")[-1])
                if not file:
                    raise KeyError("This local Codex session could not be found.")
                with file.open("rb") as f:
                    return self.reply({"messages": codex.visible(f)})
            name = path[1:] or "index.html"
            if name not in ("index.html", "app.css", "app.js", "format.js", "mark.svg", "prompt.txt", "guide.js", "importer.js", "balance.js"):
                raise KeyError("Page not found.")
            mime = {"js": "text/javascript", "svg": "image/svg+xml", "txt": "text/plain"}.get(name.split(".")[-1]) or mimetypes.guess_type(name)[0]
            # prompt.txt is the very same file Codex reads in the repository.
            file = config.ASSETS / name
            return self.reply(file.read_bytes(), mime=(mime or "text/plain") + "; charset=utf-8")

        if self.command != "POST":
            raise KeyError("Unknown operation.")
        if self.headers.get_content_type() != "application/json":
            return self.reply({"error": "The request must use application/json."}, 415)
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_BODY:
            return self.reply({"error": "This request is too large. Shorten the context or remove an attachment."}, 413)
        body = json.loads(self.rfile.read(size))
        if path == "/api/balance":
            return self.reply(store.set_balance(body["amount"]))
        if path == "/api/balance/rates":
            return self.reply(store.set_rate(body["model"], body["rates"]))
        if path == "/api/settings":
            return self.reply(store.configure(body))
        if path == "/api/respond":
            return self.respond(body)
        if path == "/api/stop":
            with s.lock:
                run = s.running.get(body["run"])
                if run:
                    run[1].set()
            return self.reply({"ok": True})
        if path in ("/api/chat/edit", "/api/chat/delete"):
            cid = body["id"]
            if path.endswith("edit"):
                store.edit(cid, body)
            else:
                with s.lock:
                    if any(c == cid for c, _ in s.running.values()):
                        raise ValueError("Stop the response before deleting this conversation.")
                    store.delete(cid)
            return self.reply({"ok": True})
        if path == "/api/codex/parse":
            return self.reply({"messages": codex.visible(body["jsonl"].splitlines())})
        if path == "/api/import":
            msgs = [{"role": m["role"], "text": m["text"]} for m in body["messages"]
                    if m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str) and m["text"].strip()]
            if not msgs:
                raise ValueError("There are no user messages or assistant replies to import.")
            cid = store.create(str(body.get("title") or "Imported conversation"), "codex", msgs)
            return self.reply({"id": cid})
        if path == "/api/shutdown":
            self.reply({"ok": True})
            threading.Thread(target=s.shutdown, daemon=True).start()
            return
        raise KeyError("Unknown operation.")

    def respond(self, body):
        s, store = self.server, self.server.store
        key, _ = s.key_source()
        if not key:
            return self.reply({"error": "Open the setup guide and follow step 2 to connect your API key.", "code": "no_key"}, 400)
        temp, retry = bool(body.get("temporary")), bool(body.get("retry"))
        cid = None if temp else body.get("id")
        settings = store.settings()
        run, stopped = uuid4().hex, threading.Event()
        with s.lock:
            if cid and any(c == cid for c, _ in s.running.values()):
                raise ValueError("A response is already in progress in this chat.")
            if temp:
                history = []
                for m in body.get("history", []):
                    if m.get("role") == "user":
                        history.append(provider.user_message(m))
                    elif m.get("role") == "assistant" and isinstance(m.get("text"), str):
                        history.append({"role": "assistant", "text": m["text"]})
            else:
                history = store.read(cid)["messages"] if cid else []
            if retry:
                if history and history[-1]["role"] == "assistant":
                    history.pop()
                if not history or history[-1]["role"] != "user":
                    raise ValueError("There is no question to regenerate.")
                if cid:
                    store.remove_last_answer(cid)
            else:
                msg = provider.user_message(body.get("message") or {})
                history.append(msg)
                if not temp:
                    if not cid:
                        title = (msg["text"] or msg["files"][0]["name"]).splitlines()[0][:36]
                        cid = store.create(title, messages=[msg])
                    else:
                        store.add(cid, msg)
            upstream_body = provider.request_body(history, settings)
            ticket = store.meter_ticket(settings["model"])
            s.running[run] = (cid, stopped)

        received = queue.Queue()
        def produce():
            try:
                for item in s.upstream(key, upstream_body, stopped):
                    if stopped.is_set():
                        break
                    received.put(item)
            except Exception:
                received.put(("error", "The connection to OpenAI was interrupted. You can retry the response."))
            finally:
                received.put(("closed", None))

        def emit(event, **fields):
            self.wfile.write((compact({"type": event, **fields}) + "\n").encode())
            self.wfile.flush()

        pieces = []
        final = {"status": "stopped", "usage": None, "note": "Generation stopped."}
        connected = True
        attempted = False
        try:
            self.headers_for(200, "application/x-ndjson; charset=utf-8")
            emit("start", id=cid, run=run, model=settings["model"], count=len(upstream_body["input"]))
            threading.Thread(target=produce, daemon=True).start()
            attempted = True
            while not stopped.is_set():
                try:
                    kind, value = received.get(timeout=.45)
                except queue.Empty:
                    emit("ping")
                    continue
                if stopped.is_set():
                    break
                if kind == "delta":
                    pieces.append(value)
                    emit("delta", text=value)
                else:
                    if kind == "rejected":
                        attempted = False
                    final = value if kind == "end" else {"status": "error", "usage": None, "note": value or "The connection ended early. Try again."}
                    break
        except (ConnectionError, OSError):
            connected = False
        finally:
            stopped.set()
            with s.lock:
                try:
                    if attempted:
                        store.record_usage(ticket, final)
                    if cid:
                        store.add(cid, {"role": "assistant", "text": "".join(pieces), "model": settings["model"], **final})
                finally:
                    s.running.pop(run, None)
        if connected:
            emit("finish", **final)

    def handle_api(self):
        try:
            self.route()
        except KeyError as e:
            self.reply({"error": str(e.args[0])}, 404)
        except (ValueError, TypeError) as e:
            self.reply({"error": str(e)}, 400)
        except (ConnectionError, BrokenPipeError):
            pass
        except OSError:
            self.reply({"error": "Could not read a local file. Check its location and permissions."}, 500)

    do_GET = do_POST = handle_api
