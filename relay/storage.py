import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .config import DEFAULT_MODEL
from . import cleanup, models, pricing, workspace


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


DEFAULTS = {
    "model": DEFAULT_MODEL, "effort": "auto", "service_tier": "default", "context": 0, "max_output": 0,
    "instructions": "", "export_directory": "", "workspace_directory": "", "reasoning_summary": False, "web_search": True,
    "workspace_auto_review": True, "workspace_terminal": True, "auto_wait_rate_limits": True, "language": "en",
}

TURN_SETTINGS = ("model", "effort", "service_tier", "context", "max_output", "instructions", "reasoning_summary",
                 "auto_wait_rate_limits")


def updated_settings(current, changes):
    values = current | changes
    model = str(values["model"]).strip()
    if not model or len(model) > 150:
        raise ValueError("Choose an available chat model.")
    ctx, maximum = int(values["context"]), int(values["max_output"])
    if ctx not in (8, 16, 32, 64, 0) or (maximum != 0 and not 256 <= maximum <= 272_000):
        raise ValueError("Check the context length and maximum output settings.")
    values.update(model=model, context=ctx, max_output=maximum, instructions=str(values["instructions"])[:12000],
                  reasoning_summary=values["reasoning_summary"] is True,
                  auto_wait_rate_limits=values["auto_wait_rate_limits"] is True)
    if changes.keys() & {"model", "effort", "service_tier"}:
        values.update(models.selections(model, values["effort"], values["service_tier"]))
    return values


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory.resolve() / "relay.sqlite3"
        self.temporary_creations = {}
        self.temporary_projects = {}
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, chat TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, text TEXT NOT NULL, files TEXT NOT NULL,
                    status TEXT NOT NULL, model TEXT NOT NULL, usage TEXT, note TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS by_chat ON messages(chat,id);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (chat TEXT PRIMARY KEY, text TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS project_creations (
                    chat TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE, path TEXT NOT NULL,
                    stamp TEXT NOT NULL, folders TEXT NOT NULL, PRIMARY KEY(chat,path));
                CREATE TABLE IF NOT EXISTS credit_meter (
                    id INTEGER PRIMARY KEY, period TEXT NOT NULL, since TEXT NOT NULL, opening REAL NOT NULL,
                    spent REAL NOT NULL DEFAULT 0, priced INTEGER NOT NULL DEFAULT 0, gaps INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS model_prices (
                    model TEXT PRIMARY KEY, input REAL NOT NULL, cached REAL NOT NULL, output REAL NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            if not any(c["name"] == "estimated" for c in db.execute("PRAGMA table_info(credit_meter)")):
                db.execute("ALTER TABLE credit_meter ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE credit_meter ADD COLUMN estimated_spend REAL NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE credit_meter ADD COLUMN failed INTEGER NOT NULL DEFAULT 0")
            chat_columns = {c["name"] for c in db.execute("PRAGMA table_info(chats)")}
            if "workspace_mode" not in chat_columns:
                db.execute("ALTER TABLE chats ADD COLUMN workspace_mode TEXT NOT NULL DEFAULT 'off'")
                db.execute("ALTER TABLE chats ADD COLUMN workspace_directory TEXT NOT NULL DEFAULT ''")
            if "workspace_auto_review" not in chat_columns:
                db.execute("ALTER TABLE chats ADD COLUMN workspace_auto_review INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE chats ADD COLUMN workspace_terminal INTEGER NOT NULL DEFAULT 0")
            if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                db.execute("""UPDATE chats SET workspace_mode='chat', workspace_auto_review=1, workspace_terminal=1
                              WHERE source='local' AND workspace_mode='off' AND workspace_directory=''""")
                db.execute("PRAGMA user_version=1")
            columns = db.execute("PRAGMA table_info(messages)")
            if not any(column["name"] == "response_details" for column in columns):
                db.execute("ALTER TABLE messages ADD COLUMN response_details TEXT NOT NULL DEFAULT '{}'")
                values = self.read_settings(db)
                for field, old_default in (("context", 32), ("max_output", 4096)):
                    if values[field] == old_default:
                        values[field] = DEFAULTS[field]
                db.execute("UPDATE settings SET value=? WHERE id=1", (compact(values),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def read_settings(db):
        row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
        return DEFAULTS | (json.loads(row[0]) if row else {})

    def settings(self, turn=None):
        with self.db() as db:
            values = self.read_settings(db)
        if turn:
            return updated_settings(values, {key: turn[key] for key in TURN_SETTINGS if key in turn})
        return values

    def drafts(self):
        with self.db() as db:
            return dict(db.execute("SELECT chat, text FROM drafts WHERE text != ''"))

    def save_draft(self, cid, text, revision):
        with self.db() as db:
            # Ignore stale writes, including requests arriving after a chat was deleted.
            db.execute("""INSERT INTO drafts(chat,text,revision)
                          SELECT ?,?,? WHERE ?='new' OR EXISTS(SELECT 1 FROM chats WHERE id=?)
                          ON CONFLICT(chat) DO UPDATE SET text=excluded.text, revision=excluded.revision
                          WHERE excluded.revision > drafts.revision""", (cid, text, revision, cid, cid))

    def configure(self, data):
        changes = {name: data[name] for name in DEFAULTS if name in data}
        for folder in ("export_directory", "workspace_directory"):
            if folder in changes:
                changes[folder] = workspace.folder_path(changes[folder])
        for toggle in ("web_search", "workspace_auto_review", "workspace_terminal"):
            if toggle in changes:
                changes[toggle] = changes[toggle] is True
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            values = updated_settings(self.read_settings(db), changes)
            db.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (compact(values),))
        return values

    @staticmethod
    def custom_rate(db, model):
        row = db.execute("SELECT input, cached, output FROM model_prices WHERE model=?", (model,)).fetchone()
        return tuple(row) if row else None

    def balance(self):
        settings = self.settings()
        model, tier = settings["model"], settings["service_tier"]
        with self.db() as db:
            row = db.execute("""SELECT since, opening, spent, priced, gaps, estimated, estimated_spend, failed
                                FROM credit_meter WHERE id=1""").fetchone()
            override = self.custom_rate(db, model)
        meter = dict(row) if row else None
        if meter:
            meter["remaining"] = round(meter["opening"] - meter["spent"], 9)
        return {"meter": meter, "model": model, "rates": pricing.model_rates(model, override=override),
                "custom_rates": override is not None, "tier": tier, "price_date": pricing.PRICE_DATE,
                "tier_rates": pricing.model_rates(model, tier, override=override)}

    def set_balance(self, amount):
        with self.db() as db:
            if amount is None:
                db.execute("DELETE FROM credit_meter WHERE id=1")
            else:
                amount = float(amount)
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError("Enter a nonnegative USD balance.")
                db.execute("INSERT OR REPLACE INTO credit_meter(id,period,since,opening) VALUES(1,?,?,?)",
                           (uuid4().hex, now(), amount))
        return self.balance()

    def set_rate(self, model, rates):
        model = model.strip()
        if not model:
            raise ValueError("Choose a model before setting rates.")
        with self.db() as db:
            if rates is None:
                db.execute("DELETE FROM model_prices WHERE model=?", (model,))
            else:
                values = [float(rate) for rate in rates]
                if len(values) != 3 or any(not math.isfinite(rate) or rate < 0 for rate in values):
                    raise ValueError("Enter three nonnegative prices in USD per million tokens.")
                db.execute("INSERT OR REPLACE INTO model_prices VALUES(?,?,?,?)", (model, *values))
        return self.balance()

    def meter_ticket(self, model):
        with self.db() as db:
            row = db.execute("SELECT period FROM credit_meter WHERE id=1").fetchone()
            return (row[0], model, self.custom_rate(db, model)) if row else None

    def record_usage(self, ticket, result):
        if ticket is None:
            return
        period, model, override = ticket
        cost = pricing.estimate_usd(result.get("billed_model") or model, result.get("usage"),
                                    result.get("service_tier", "default"), override,
                                    search_calls=result.get("search_calls", 0))
        failed = result.get("status") in ("error", "rejected")
        rejected = result.get("status") == "rejected" or result.get("rejected")
        gap = cost is None and not (rejected and not result.get("usage"))
        estimated = cost is not None and result.get("usage_estimated", False)
        # An in-flight reply belongs to the snapshot under which it was sent.
        with self.db() as db:
            db.execute("""UPDATE credit_meter SET spent=spent+?, priced=priced+?, gaps=gaps+?,
                          failed=failed+?, estimated=estimated+?, estimated_spend=estimated_spend+?
                          WHERE id=1 AND period=?""",
                       (cost or 0, int(cost is not None), int(gap), int(failed),
                        int(estimated), cost if estimated else 0, period))

    def list_chats(self, query=""):
        with self.db() as db:
            rows = db.execute("""
                SELECT c.*, (SELECT count(*) FROM messages m WHERE m.chat=c.id) AS count
                FROM chats c WHERE ?='' OR instr(lower(c.title),lower(?))>0 OR EXISTS
                (SELECT 1 FROM messages m WHERE m.chat=c.id AND instr(lower(m.text),lower(?))>0)
                ORDER BY pinned DESC, updated DESC
            """, (query, query, query))
            return [self.chat_fields(r) for r in rows]

    def chat_folder(self, cid):
        return self.path.parent / "chat-workspaces" / cid

    def chat_fields(self, row):
        fields = dict(row) | {"workspace_auto_review": bool(row["workspace_auto_review"]),
                              "workspace_terminal": bool(row["workspace_terminal"])}
        fields.pop("data_analysis", None)  # Older databases may include the retired toggle.
        if fields["workspace_mode"] == "chat":
            fields["chat_folder"] = str(self.chat_folder(fields["id"]))
        return fields

    @staticmethod
    def insert(db, cid, m):
        details = {name: m[name] for name in ("sources", "reason", "reasoning_summary", "subagents",
                                              "workspace_activity", "agent_activity", "artifacts", "context_usage", "compaction",
                                              "steers", "rate_limit") if m.get(name)}
        db.execute("""INSERT INTO messages(chat,role,text,files,status,model,usage,note,response_details)
                      VALUES (?,?,?,?,?,?,?,?,?)""",
                   (cid, m["role"], m["text"], compact(m.get("files", [])), m.get("status", "done"),
                    m.get("model", ""), compact(m["usage"]) if m.get("usage") else None, m.get("note", ""), compact(details)))

    def create(self, title, source="local", messages=(), *, workspace_mode=None, workspace_directory="",
               workspace_auto_review=None, workspace_terminal=None):
        cid = uuid4().hex
        if workspace_mode is None:
            workspace_mode = "chat" if source == "local" else "off"
        if workspace_mode == "chat":
            workspace_directory = ""
        with self.db() as db:
            defaults = self.read_settings(db) if workspace_mode == "chat" else {}
            if workspace_auto_review is None:
                workspace_auto_review = defaults.get("workspace_auto_review", False)
            if workspace_terminal is None:
                workspace_terminal = defaults.get("workspace_terminal", False)
            db.execute("""INSERT INTO chats(id,title,source,updated,workspace_mode,workspace_directory,
                          workspace_auto_review,workspace_terminal) VALUES (?,?,?,?,?,?,?,?)""",
                       (cid, title[:100], source, now(), workspace_mode, workspace_directory,
                        workspace_auto_review, workspace_terminal))
            for m in messages:
                self.insert(db, cid, m)
        return cid

    def read(self, cid):
        with self.db() as db:
            chat = db.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
            if not chat:
                raise KeyError("Chat not found.")
            rows = db.execute("""SELECT role,text,files,status,model,usage,note,response_details
                                 FROM messages WHERE chat=? ORDER BY id""", (cid,))
            msgs = []
            for row in rows:
                msg = dict(row)
                msg.update(json.loads(msg.pop("response_details")), files=json.loads(msg["files"]),
                           usage=json.loads(msg["usage"]) if msg["usage"] else None)
                msgs.append(msg)
        return self.chat_fields(chat) | {"messages": msgs}

    def chat_workspace(self, cid):
        with self.db() as db:
            row = db.execute("""SELECT id,workspace_mode,workspace_directory,workspace_auto_review,workspace_terminal
                                FROM chats WHERE id=?""", (cid,)).fetchone()
        if not row:
            raise KeyError("Chat not found.")
        return self.chat_fields(row)

    def set_chat_workspace(self, cid, mode, directory, auto_review=None, terminal=None, defaults=None):
        if mode not in ("off", "chat", "read", "edit"):
            raise ValueError("Choose a workspace access mode.")
        directory = workspace.folder_path(directory) if mode != "chat" else ""
        if mode in ("read", "edit") and not directory:
            raise ValueError("Choose a project folder first.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("""SELECT workspace_mode,workspace_auto_review,workspace_terminal
                                     FROM chats WHERE id=?""", (cid,)).fetchone()
            if previous is None:
                raise KeyError("Chat not found.")
            settings = self.read_settings(db)
            if defaults:
                settings.update({key: defaults[key] is True for key in ("workspace_auto_review", "workspace_terminal")
                                 if key in defaults})
                db.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (compact(settings),))
            fallback = settings if mode == "chat" else {}
            if previous["workspace_mode"] == mode:
                fallback = dict(previous)
            if auto_review is None:
                auto_review = bool(fallback.get("workspace_auto_review"))
            if terminal is None:
                terminal = bool(fallback.get("workspace_terminal"))
            auto_review = mode != "off" and auto_review is True
            terminal = mode != "off" and terminal is True
            db.execute("""UPDATE chats SET workspace_mode=?,workspace_directory=?,workspace_auto_review=?,
                          workspace_terminal=? WHERE id=?""", (mode, directory, auto_review, terminal, cid))
        return self.chat_fields({"id": cid, "workspace_mode": mode, "workspace_directory": directory,
                                 "workspace_auto_review": auto_review, "workspace_terminal": terminal})

    def fork(self, cid, through):
        """Start a saved chat with the messages up to the selected reply."""
        if type(through) is not int or through < 0:
            raise ValueError("Choose a reply to branch from.")
        branch = uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            chat = db.execute("SELECT title FROM chats WHERE id=?", (cid,)).fetchone()
            if not chat:
                raise KeyError("Chat not found.")
            last = db.execute("SELECT id,role FROM messages WHERE chat=? ORDER BY id LIMIT 1 OFFSET ?",
                              (cid, through)).fetchone()
            if not last or last["role"] != "assistant":
                raise ValueError("Choose a reply to branch from.")
            db.execute("INSERT INTO chats(id,title,source,updated) VALUES(?,?,?,?)",
                       (branch, ("Branch · " + chat["title"])[:100], "branch", now()))
            db.execute("""INSERT INTO messages(chat,role,text,files,status,model,usage,note,response_details)
                          SELECT ?,role,text,files,status,model,usage,note,response_details FROM messages
                          WHERE chat=? AND id<=? ORDER BY id""", (branch, cid, last["id"]))
        return branch

    def add(self, cid, msg):
        with self.db() as db:
            self.insert(db, cid, msg)
            db.execute("UPDATE chats SET updated=? WHERE id=?", (now(), cid))

    def remove_last_answer(self, cid):
        with self.db() as db:
            db.execute("""DELETE FROM messages WHERE id=(SELECT max(id) FROM messages WHERE chat=?) AND role='assistant'""", (cid,))

    def edit(self, cid, data):
        with self.db() as db:
            if "title" in data:
                title = str(data["title"]).strip()[:100]
                if not title:
                    raise ValueError("Enter a title.")
                db.execute("UPDATE chats SET title=? WHERE id=?", (title, cid))
            if "pinned" in data:
                db.execute("UPDATE chats SET pinned=? WHERE id=?", (int(bool(data["pinned"])), cid))

    def delete(self, cid, remove_folder=False, project_files=()):
        return cleanup.delete(self, cid, remove_folder, project_files)
