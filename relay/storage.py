import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .config import DEFAULT_MODEL
from . import pricing


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


DEFAULTS = {"model": DEFAULT_MODEL, "context": 32, "max_output": 4096, "instructions": ""}


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "relay.sqlite3"
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
                CREATE TABLE IF NOT EXISTS credit_meter (
                    id INTEGER PRIMARY KEY, period TEXT NOT NULL, since TEXT NOT NULL, opening REAL NOT NULL,
                    spent REAL NOT NULL DEFAULT 0, priced INTEGER NOT NULL DEFAULT 0, gaps INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS model_prices (
                    model TEXT PRIMARY KEY, input REAL NOT NULL, cached REAL NOT NULL, output REAL NOT NULL);
            """)

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

    def settings(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
        return DEFAULTS | (json.loads(row[0]) if row else {})

    def configure(self, data):
        model = str(data.get("model", "")).strip()
        if not model or len(model) > 150:
            raise ValueError("Enter a model ID that supports the Responses API.")
        ctx, maximum = int(data["context"]), int(data["max_output"])
        if ctx not in (8, 16, 32, 64, 0) or not 256 <= maximum <= 32768:
            raise ValueError("Check the context length and maximum output settings.")
        values = {"model": model, "context": ctx, "max_output": maximum, "instructions": str(data.get("instructions", ""))[:12000]}
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (compact(values),))
        return values

    @staticmethod
    def custom_rate(db, model):
        row = db.execute("SELECT input, cached, output FROM model_prices WHERE model=?", (model,)).fetchone()
        return tuple(row) if row else None

    def balance(self):
        model = self.settings()["model"]
        with self.db() as db:
            row = db.execute("SELECT since, opening, spent, priced, gaps FROM credit_meter WHERE id=1").fetchone()
            override = self.custom_rate(db, model)
        meter = dict(row) if row else None
        if meter:
            meter["remaining"] = round(meter["opening"] - meter["spent"], 9)
        return {"meter": meter, "model": model, "rates": override or pricing.STANDARD.get(pricing.base_model(model)),
                "custom_rates": override is not None}

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
                                    result.get("service_tier", "default"), override)
        # An in-flight reply belongs to the snapshot under which it was sent.
        with self.db() as db:
            db.execute("""UPDATE credit_meter SET spent=spent+?, priced=priced+?, gaps=gaps+?
                          WHERE id=1 AND period=?""", (cost or 0, int(cost is not None), int(cost is None), period))

    def list_chats(self, query=""):
        with self.db() as db:
            rows = db.execute("""
                SELECT c.*, (SELECT count(*) FROM messages m WHERE m.chat=c.id) AS count
                FROM chats c WHERE ?='' OR instr(lower(c.title),lower(?))>0 OR EXISTS
                (SELECT 1 FROM messages m WHERE m.chat=c.id AND instr(lower(m.text),lower(?))>0)
                ORDER BY pinned DESC, updated DESC
            """, (query, query, query))
            return [dict(r) for r in rows]

    @staticmethod
    def insert(db, cid, m):
        db.execute("INSERT INTO messages(chat,role,text,files,status,model,usage,note) VALUES (?,?,?,?,?,?,?,?)",
                   (cid, m["role"], m["text"], compact(m.get("files", [])), m.get("status", "done"),
                    m.get("model", ""), compact(m["usage"]) if m.get("usage") else None, m.get("note", "")))

    def create(self, title, source="local", messages=()):
        cid = uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO chats(id,title,source,updated) VALUES (?,?,?,?)", (cid, title[:100], source, now()))
            for m in messages:
                self.insert(db, cid, m)
        return cid

    def read(self, cid):
        with self.db() as db:
            chat = db.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
            if not chat:
                raise KeyError("Chat not found.")
            rows = db.execute("SELECT role,text,files,status,model,usage,note FROM messages WHERE chat=? ORDER BY id", (cid,))
            msgs = [dict(r) | {"files": json.loads(r["files"]), "usage": json.loads(r["usage"]) if r["usage"] else None} for r in rows]
        return dict(chat) | {"messages": msgs}

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

    def delete(self, cid):
        with self.db() as db:
            db.execute("DELETE FROM chats WHERE id=?", (cid,))
