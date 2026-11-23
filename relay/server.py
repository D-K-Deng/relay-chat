import json
import mimetypes
import queue
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from . import artifacts, cleanup, codex, config, dictation, harness, live, models, provider, workspace
from .project_files import ProjectFiles
from .storage import Store, compact

MAX_BODY = 48 * 1024 * 1024


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"  # SO_REUSEADDR permits duplicate listeners on Windows.

    def __init__(self, port, directory=None, upstream=None, key_source=None, codex_home=None, model_source=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = Store(directory or config.data_dir())
        self.upstream = upstream or provider.events
        self.key_source = key_source or config.api_key
        self.model_source = model_source or provider.available_models
        self.codex_home = codex_home or config.codex_dir()
        self.context_limits, self.ultra_efforts = models.codex_model_defaults(self.codex_home)
        self.running = {}
        self.approvals = {}
        self.lock = threading.Lock()
        self.workspace_lock = threading.Lock()
        self.desktop_window = None


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
                settings = store.settings()
                return self.reply({"product": "relay-chat", "version": config.APP_VERSION, "configured": bool(key),
                                   "key_source": source, "settings": settings,
                                   "model_capabilities": models.capabilities(settings["model"]),
                                   "workspace_picker": workspace.PICKER_AVAILABLE,
                                   "desktop_window": s.desktop_window})
            if path == "/api/models":
                key, _ = s.key_source()
                if not key:
                    return self.reply({"error": "Connect your OpenAI API key to load models."}, 400)
                return self.reply({"models": s.model_source(key)})
            if path == "/api/balance":
                return self.reply(store.balance())
            if path in ("/api/artifacts/content", "/api/artifacts/preview"):
                args = (store, qs["chat"][0], qs["run"][0], qs["name"][0])
                if path.endswith("/preview"):
                    return self.reply(artifacts.preview_file(*args))
                return self.reply(artifacts.saved_file(*args).read_bytes(), mime="application/octet-stream")
            if path == "/api/chats":
                return self.reply(store.list_chats(qs.get("q", [""])[0]))
            if path == "/api/drafts":
                return self.reply(store.drafts())
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
            if name not in ("index.html", "app.css", "app.js", "i18n.js", "system-messages.js", "format.js", "mark.svg", "prompt.txt", "guide.js",
                            "importer.js", "balance.js", "workspace.js", "artifacts.js", "artifacts.css",
                            "dictation.js", "dictation.css", "drafts.js", "agents.js"):
                raise KeyError("Page not found.")
            mime = {"js": "text/javascript", "svg": "image/svg+xml", "txt": "text/plain"}.get(name.split(".")[-1]) or mimetypes.guess_type(name)[0]
            # prompt.txt is the very same file Codex reads in the repository.
            file = config.ASSETS / name
            return self.reply(file.read_bytes(), mime=(mime or "text/plain") + "; charset=utf-8")

        if self.command != "POST":
            raise KeyError("Unknown operation.")
        if path == "/api/transcribe":
            return self.reply(dictation.handle(self, s.key_source()[0]))
        if self.headers.get_content_type() != "application/json":
            return self.reply({"error": "The request must use application/json."}, 415)
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_BODY:
            return self.reply({"error": "This request is too large. Shorten the context or remove an attachment."}, 413)
        body = json.loads(self.rfile.read(size))
        if path == "/api/draft":
            store.save_draft(body["id"], body["text"], body["revision"])
            return self.reply({"ok": True})
        if path == "/api/artifacts/open":
            return self.reply(artifacts.open_file(store, body))
        if path == "/api/artifacts/preview":
            return self.reply(artifacts.preview_inline(body))
        if path == "/api/steer":
            with s.lock:
                active = s.running.get(body["run"])
            if active is None:
                raise ValueError("This response has finished. Send your update as a new message.")
            return self.reply(active[1].steer(body["text"]))
        if path == "/api/balance":
            return self.reply(store.set_balance(body["amount"]))
        if path == "/api/balance/rates":
            return self.reply(store.set_rate(body["model"], body["rates"]))
        if path == "/api/settings":
            return self.reply(store.configure(body))
        if path == "/api/workspace/pick":
            return self.reply({"directory": workspace.pick_folder(store.settings()["language"])})
        if path == "/api/workspace/decision":
            if not isinstance(body.get("allow"), bool):
                raise ValueError("Approve or decline this workspace action.")
            with s.lock:
                decision = s.approvals.pop((body["run"], body["id"]), None)
                if decision is None:
                    raise ValueError("This change is no longer waiting for review.")
                decision.put(body["allow"])
            return self.reply({"ok": True})
        if path == "/api/export":
            return self.reply({"path": workspace.save_markdown(
                store.settings()["export_directory"], body["title"], body["markdown"])})
        if path == "/api/respond":
            return self.respond(body)
        if path == "/api/stop":
            with s.lock:
                run = s.running.get(body["run"])
                if run:
                    run[1].set()
            return self.reply({"ok": True})
        if path == "/api/chat/fork":
            return self.reply({"id": store.fork(body["id"], body["through"])})
        if path == "/api/chat/workspace":
            with s.lock:
                if any(cid == body["id"] for cid, _ in s.running.values()):
                    raise ValueError("Stop the active turn before changing its workspace.")
                binding = store.set_chat_workspace(body["id"], body["mode"], body.get("directory", ""),
                                                   body.get("workspace_auto_review"), body.get("workspace_terminal"),
                                                   body.get("defaults"))
            return self.reply(binding)
        if path == "/api/chat/edit":
            store.edit(body["id"], body)
            return self.reply({"ok": True})
        if path == "/api/chat/release-temporary":
            owner = "temp:" + body["temporary_session"]
            with s.lock, s.workspace_lock:
                for active, control in s.running.values():
                    if active == owner:
                        control.set()
                store.temporary_creations.pop(owner, None)
                store.temporary_projects.pop(owner, None)
            return self.reply({"ok": True})
        if path in ("/api/chat/delete", "/api/chat/delete-preview"):
            owner = "temp:" + body["temporary_session"] if body.get("temporary_session") else body["id"]
            with s.lock, s.workspace_lock:
                if any(c == owner for c, _ in s.running.values()):
                    raise ValueError("Stop the response before deleting this conversation.")
                if path.endswith("-preview"):
                    result = cleanup.preview(store, owner)
                else:
                    result = store.delete(owner, body.get("remove_folder") is True, body.get("project_files", []))
            return self.reply(result)
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
        settings = store.settings(body.get("model_settings"))
        model = settings["model"]
        capabilities = models.capabilities(model)
        input_limit = s.context_limits.get(models.base_model(model))
        run, stopped = uuid4().hex, live.RunControl(model)
        project = None
        with s.lock:
            if cid and any(c == cid for c, _ in s.running.values()):
                raise ValueError("A response is already in progress in this chat.")
            binding = store.chat_workspace(cid) if cid else settings
            mode = body.get("workspace_mode", binding.get("workspace_mode", "off" if temp else "chat"))
            if mode not in ("off", "chat", "read", "edit") or (mode == "chat" and temp):
                raise ValueError("Choose a workspace access mode for this chat.")
            defaults = binding if cid or mode == "chat" else {}
            auto_review = mode != "off" and body.get("workspace_auto_review", defaults.get("workspace_auto_review")) is True
            terminal = mode != "off" and body.get("workspace_terminal", defaults.get("workspace_terminal")) is True
            if cid and mode != "off" and (mode != binding["workspace_mode"] or (terminal and not binding["workspace_terminal"])
                                          or (auto_review and not binding["workspace_auto_review"])):
                raise ValueError("Workspace access changed. Check this chat's workspace selection.")
            directory = ""
            if mode in ("read", "edit"):
                directory = binding["workspace_directory"]
                requested_directory = body.get("workspace_directory", directory if cid else "")
                if not directory or Path(directory) != Path(requested_directory or ""):
                    raise ValueError("Choose the workspace folder for this chat before using file tools.")
                if Path(workspace.folder_path(directory)) != Path(directory):
                    raise ValueError("The workspace location changed. Choose the project folder again.")
            if temp:
                history = []
                for m in body.get("history", []):
                    if m.get("role") == "user":
                        history.append(provider.user_message(m))
                    elif m.get("role") == "assistant" and isinstance(m.get("text"), str):
                        history.append({"role": "assistant", "text": m["text"]} |
                                       {field: m[field] for field in ("compaction", "steers") if m.get(field)})
            else:
                history = store.read(cid)["messages"] if cid else []
            if retry:
                if history and history[-1]["role"] == "assistant":
                    history.pop()
                if not history or history[-1]["role"] != "user":
                    raise ValueError("There is no question to regenerate.")
            else:
                msg = provider.user_message(body.get("message") or {})
                history.append(msg)
            default_search = settings["web_search"] and settings["effort"] != "minimal" and capabilities["web_search"]
            web_search = body.get("web_search", default_search) is True
            upstream_body = provider.request_body(history, settings, web_search, input_limit,
                                                  ultra_effort=s.ultra_efforts.get(models.base_model(model)))
            if not temp:
                if retry:
                    if cid:
                        store.remove_last_answer(cid)
                elif cid:
                    store.add(cid, msg)
                else:
                    title = (msg["text"] or msg["files"][0]["name"]).splitlines()[0][:36]
                    cid = store.create(title, messages=[msg], workspace_mode=mode,
                                       workspace_directory=directory,
                                       workspace_auto_review=auto_review, workspace_terminal=terminal)
            chat_folder = store.chat_folder(cid) if mode == "chat" else None
            ticket = store.meter_ticket(model)
            owner = "temp:" + body["temporary_session"] if temp and body.get("temporary_session") else cid
            if temp and owner:
                store.temporary_projects[owner] = directory
            s.running[run] = (owner, stopped)

        outputs = artifacts.TurnFiles(store, cid, run)
        if mode != "off" and capabilities["workspace_tools"]:
            project = ProjectFiles(chat_folder or directory, "edit" if chat_folder else mode, stopped, s.workspace_lock,
                                   auto_review=auto_review, terminal=terminal, managed=bool(chat_folder),
                                   on_write=outputs.workspace, track_creation=partial(cleanup.record_project_write, store, owner)
                                   if owner and mode == "edit" else None)

        def emit(event, **fields):
            self.wfile.write((compact({"type": event, **fields}) + "\n").encode())
            self.wfile.flush()

        def record(result):
            store.record_usage(ticket, result)

        def review(proposal):
            token = (run, uuid4().hex)
            decision = queue.Queue()
            with s.lock:
                s.approvals[token] = decision
            try:
                emit("approval", id=token[1], **proposal)
                while not stopped.is_set():
                    try:
                        return decision.get(timeout=.45)
                    except queue.Empty:
                        emit("ping")
                return False
            finally:
                with s.lock:
                    s.approvals.pop(token, None)

        turn = harness.Turn(stopped, project, auto_wait_rate_limits=settings["auto_wait_rate_limits"])
        connected = True
        try:
            self.headers_for(200, "application/x-ndjson; charset=utf-8")
            emit("start", id=cid, run=run, model=model, count=len(upstream_body["input"]) - 1,
                 **({"chat_folder": str(chat_folder)} if chat_folder else {}))
            turn.send(s.upstream, key, upstream_body, emit, review, record, user_request=history[-1]["text"])
        except ConnectionError:
            connected = False
        except Exception as error:
            # The stream has already sent HTTP 200. Report a turn failure in its final event.
            turn.final = {"status": "error", "note": str(error) if isinstance(error, ValueError)
                          else "Relay could not finish this response. You can retry it."}
        finally:
            stopped.set()
            final = turn.result()
            if outputs.items:
                final["artifacts"] = outputs.items
            with s.lock:
                try:
                    if cid:
                        store.add(cid, {"role": "assistant", "model": model, **final})
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
