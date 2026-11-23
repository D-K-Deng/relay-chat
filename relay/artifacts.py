"""Keep and preview files created in a chat."""
import base64
import csv
import io
import os
import posixpath
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PureWindowsPath
from xml.etree import ElementTree as ET


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SHEETS = {".xlsx": XLSX, ".csv": "text/csv"}
MAX_OUTPUT = 24 * 1024 * 1024
TEXT = {".txt", ".md", ".json", ".py", ".js", ".ts", ".tsx", ".html", ".css", ".xml", ".log",
        ".yaml", ".yml", ".toml", ".sql", ".sh", ".c", ".cpp", ".rs", ".go", ".java", ".tex", ".tsv"}
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def file_format(name):
    ext = Path(name).suffix.lower()
    if ext in IMAGES:
        return "image", IMAGES[ext]
    if ext in SHEETS:
        return "table", SHEETS[ext]
    if ext in TEXT:
        return "text", "text/plain"
    return "document", "application/pdf" if ext == ".pdf" else "application/octet-stream"


def display_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", PureWindowsPath(name).name).strip(" .")[:180]
    return "_" + name if PureWindowsPath(name).is_reserved() else name or "output"


class TurnFiles:
    def __init__(self, store, cid, run):
        self.store, self.cid, self.run = store, cid, run
        self.items = []

    def workspace(self, path, content):
        name = display_name(path)
        occupied = {x["name"].casefold() for x in self.items}
        stem, suffix = Path(name).stem, Path(name).suffix
        index = 2
        while name.casefold() in occupied:
            name = f"{stem} ({index}){suffix}"
            index += 1
        kind, mime = file_format(name)
        item = {"chat": self.cid, "run": self.run, "name": name, "source": path, "kind": kind, "mime": mime}
        try:
            if self.cid:
                directory = self.store.chat_folder(self.cid) / "Results" / self.run
                if directory.resolve() != directory:
                    raise ValueError("Choose a direct folder for chat files.")
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / name).open("xb") as file:
                    file.write(content)
            else:
                item["data"] = base64.b64encode(content).decode()
            item["size"] = len(content)
        except (ValueError, OSError) as error:
            item["error"] = str(error) if isinstance(error, ValueError) else "Could not save a copy of this file."
        self.items.append(item)

def saved_file(store, chat, run, name):
    # Branches copy message metadata and retain links to the original output snapshot.
    with store.db() as db:
        row = db.execute("""SELECT json_extract(f.value,'$.chat')
                            FROM messages AS m, json_each(m.response_details,'$.artifacts') AS f
                            WHERE m.chat=? AND json_extract(f.value,'$.run')=? AND json_extract(f.value,'$.name')=?
                            AND json_extract(f.value,'$.error') IS NULL LIMIT 1""", (chat, run, name)).fetchone()
    if not row or not row[0]:
        raise KeyError("This file is unavailable in this chat.")
    path = store.chat_folder(row[0]) / "Results" / run / name
    if path.resolve() != path or not path.is_file():
        raise KeyError("The saved file has been moved or removed.")
    return path


def open_file(store, body):
    path = saved_file(store, body["chat"], body["run"], body["name"])
    if body.get("action") == "open":
        if path.suffix.lower() not in {".xlsx", ".csv", ".pdf", ".txt", *IMAGES}:
            raise ValueError("Use Show in folder to locate this file.")
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["/usr/bin/open" if sys.platform == "darwin" else "xdg-open", str(path)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
    return {"ok": True}


def xlsx_rows(data):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as files:
        def xml(name):
            if files.getinfo(name).file_size > MAX_OUTPUT:
                raise ValueError("This worksheet is too large to preview. Save a copy to view it in full.")
            return ET.fromstring(files.read(name))

        sheet = xml("xl/workbook.xml").find("m:sheets/m:sheet", ns)
        if sheet is None:
            return {"rows": [], "sheet": ""}
        relation = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = next(r.get("Target") for r in xml("xl/_rels/workbook.xml.rels") if r.get("Id") == relation)
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        shared = []
        if "xl/sharedStrings.xml" in files.namelist():
            shared = ["".join(t.text or "" for t in s.iterfind(".//m:t", ns)) for s in xml("xl/sharedStrings.xml")]
        rows = []
        for row in xml(path).iterfind("m:sheetData/m:row", ns):
            values = []
            for cell in row.iterfind("m:c", ns):
                col = 0
                for letter in re.match(r"[A-Z]+", cell.get("r", "A"))[0]:
                    col = col * 26 + ord(letter) - ord("A") + 1
                if col > 12:
                    break
                value = cell.findtext("m:v", "", ns)
                if cell.get("t") == "s" and value:
                    value = shared[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iterfind(".//m:t", ns))
                values.extend([""] * (col - len(values) - 1))
                values.append(value[:2000])
            rows.append(values)
            if len(rows) >= 25:
                break
        return {"sheet": sheet.get("name", ""), "rows": rows}


def preview(name, data):
    ext = Path(name).suffix.lower()
    if ext == ".xlsx":
        try:
            return xlsx_rows(data)
        except (zipfile.BadZipFile, KeyError, IndexError, StopIteration, ET.ParseError, ValueError, AttributeError):
            raise ValueError("This workbook could not be previewed. Save a copy to open it in your spreadsheet app.") from None
    if ext == ".csv":
        from itertools import islice
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"), newline=""))
        return {"rows": [[s[:2000] for s in row[:12]] for row in islice(reader, 25)]}
    return {"text": data[:32000].decode("utf-8-sig", errors="replace")}


def preview_file(store, chat, run, name):
    return preview(name, saved_file(store, chat, run, name).read_bytes())


def preview_inline(body):
    return preview(body["name"], base64.b64decode(body["data"], validate=True))
