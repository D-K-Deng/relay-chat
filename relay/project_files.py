"""Text file tools available inside the folder selected for this turn."""
import codecs
import difflib
import fnmatch
import os
import shutil
import tempfile
from pathlib import Path, PureWindowsPath

from .cleanup import file_stamp


MAX_FILE = 1024 * 1024
MAX_RESULT = 24_000
EXCLUDED = {"node_modules", "__pycache__", "build", "dist", "vendor", "venv", "target",
            "credentials", "credentials.json", "secrets.json", "auth.json", "id_rsa", "id_ed25519"}


def visible(name):
    return (not name.startswith(".") and name.lower() not in EXCLUDED
            and not name.lower().endswith((".pem", ".key", ".p12", ".pfx", ".sqlite3", ".db")))


def tool(name, description, **fields):
    props = {key: {"type": typ, "description": hint} for key, (typ, hint) in fields.items()}
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": props, "required": list(props),
                           "additionalProperties": False}}


PATH = ("string", "A workspace-relative path.")
GLOB = ("string", "Filename or relative path glob, for example *.py. Use * for all files.")
READ_TOOLS = [
    tool("find_files", "Find project files matching a glob, optionally within a subfolder.", path=PATH, pattern=GLOB),
    tool("search_text", "Find case-sensitive literal text in matching files. Returns paths and numbered lines.",
         path=PATH, pattern=GLOB, query=("string", "Literal text to search for.")),
    tool("read_file", "Read numbered lines of a UTF-8 text file.", path=PATH,
         start=("integer", "First line, starting at 1."), count=("integer", "Number of lines, up to 250.")),
]
WRITE_TOOLS = [
    tool("edit_file", "Request a reviewed replacement of one unique exact text block. Read the file first.",
         path=PATH, old=("string", "Unique existing text, without the line-number prefix."),
         new=("string", "Replacement text.")),
    tool("create_file", "Request a reviewed new UTF-8 text file. Missing parent folders may be created.",
         path=PATH, content=("string", "Complete contents of the new file.")),
]


class ProjectFiles:
    def __init__(self, root, mode, stopped, write_lock, *, auto_review=False, terminal=False, managed=False,
                 on_write=None, track_creation=None):
        self.root = Path(root)
        self.mode = mode
        self.stopped = stopped
        self.write_lock = write_lock
        self.auto_review = auto_review
        self.terminal = terminal
        self.managed = managed
        self.on_write = on_write
        self.track_creation = track_creation

    def path(self, value):
        rel = Path(value)
        windows = PureWindowsPath(value)
        if (windows.anchor or ".." in rel.parts or ":" in value or "\x00" in value
                or any(not visible(part) or part.rstrip(" .") != part or PureWindowsPath(part).is_reserved()
                       for part in rel.parts)):
            raise ValueError("Choose a visible project path inside the workspace.")
        target = self.root / rel
        if target.resolve() != target:
            raise ValueError("Choose a direct project path. Linked paths are excluded from workspace access.")
        if self.managed and not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
        return target

    def files(self, folder, pattern):
        if not folder.is_dir():
            raise ValueError("Choose an existing project folder.")
        for base, dirs, files in os.walk(folder):
            if self.stopped.is_set():
                return
            base = Path(base)
            dirs[:] = sorted(d for d in dirs if visible(d) and (base / d).resolve() == base / d)
            for name in sorted(files):
                if not visible(name):
                    continue
                path = base / name
                relative = path.relative_to(self.root).as_posix()
                if (fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern)) and path.resolve() == path:
                    yield relative, path

    @staticmethod
    def contents(path):
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError("Choose a regular UTF-8 text file up to 1 MiB.")
        raw = path.read_bytes()
        if b"\0" in raw:
            raise ValueError("This file contains binary data.")
        return raw, raw.decode("utf-8-sig")

    def guidance(self):
        if self.managed and not self.root.is_dir():
            return ""
        try:
            return self.contents(self.path("AGENTS.md"))[1][:8_000]
        except (OSError, ValueError):
            return ""

    def inspect(self, name, args, path):
        if name == "read_file":
            start, count = args["start"], args["count"]
            if start < 1 or not 1 <= count <= 250:
                raise ValueError("Use a positive starting line and a count from 1 to 250.")
            lines = self.contents(path)[1].splitlines()
            end = min(start - 1 + count, len(lines))
            result = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))
            result = result[:MAX_RESULT] + ("\n[Output clipped]" if len(result) > MAX_RESULT else "")
            return result or "(No lines in range)", f"Read {max(0, end - start + 1)} lines of {len(lines)}"
        query = args["query"] if name == "search_text" else None
        if query == "":
            raise ValueError("Enter text to search for.")
        matches = []
        limit = 40 if name == "search_text" else 150
        for relative, file in self.files(path, args["pattern"]):
            if self.stopped.is_set():
                break
            if query is None:
                matches.append(relative)
            else:
                try:
                    lines = self.contents(file)[1].splitlines()
                except (OSError, ValueError):
                    continue
                for number, line in enumerate(lines, 1):
                    offset = line.find(query)
                    if offset >= 0:
                        matches.append(f"{relative}:{number}: {line[max(0, offset - 120):offset + 300]}")
                        if len(matches) == limit:
                            break
            if len(matches) == limit:
                break
        detail = f"{len(matches)} matches" + (" (limit reached)" if len(matches) == limit else "")
        return "\n".join([detail, *matches])[:MAX_RESULT], detail

    def change(self, name, args, target, review):
        if name == "create_file":
            if target.exists():
                raise ValueError("File already exists. Read it and use edit_file.")
            raw, before, after = None, "", args["content"]
        else:
            raw, before = self.contents(target)
            old, new = args["old"].replace("\r\n", "\n"), args["new"].replace("\r\n", "\n")
            if "\r\n" in before:
                old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
            if not old or before.count(old) != 1:
                raise ValueError("The original text must match exactly once. Read the current file and try again.")
            after = before.replace(old, new, 1)
            if before == after:
                return "File already matches.", "done", ""
        encoded = (codecs.BOM_UTF8 if raw and raw.startswith(codecs.BOM_UTF8) else b"") + after.encode("utf-8")
        if len(encoded) > MAX_FILE or b"\0" in encoded:
            raise ValueError("Edits support UTF-8 text files up to 1 MiB.")
        label = target.relative_to(self.root).as_posix()
        diff = unified_diff(before, after, label, raw is None)
        if not review({"tool": name, "path": label, "diff": diff}):
            detail = "Turn stopped before writing." if self.stopped.is_set() else "User declined the proposed change."
            return detail, "denied", ""
        with self.write_lock:
            if self.stopped.is_set():
                return "Turn stopped before writing.", "denied", ""
            target = self.path(label)
            before_stamp = file_stamp(target) if self.track_creation and raw is not None else None
            new_folders = []
            if raw is None:
                if self.track_creation:
                    for parent in target.parents:
                        if parent == self.root or parent.exists():
                            break
                        new_folders.append(str(parent))
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(encoded)
            else:
                if target.read_bytes() != raw:
                    raise ValueError("The file changed during review. Read it and propose a fresh edit.")
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(encoded)
                        stream.close()
                        shutil.copymode(target, temporary)
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
            if self.track_creation:
                self.track_creation(target, before_stamp, new_folders)
        if self.on_write:
            self.on_write(label, encoded)
        detail = "Created file" if raw is None else "Applied edit"
        return detail, "done", diff

    def execute(self, name, args, review):
        entry = {"tool": name, "path": "", "status": "error"}
        try:
            entry["path"] = args["path"]
            target = self.path(args["path"])
            if name in ("find_files", "search_text", "read_file"):
                result, entry["detail"] = self.inspect(name, args, target)
                entry["status"] = "done"
            elif self.mode == "edit" and name in ("edit_file", "create_file"):
                result, entry["status"], diff = self.change(name, args, target, review)
                entry["detail"] = result
                if diff:
                    entry["diff"] = diff
            else:
                raise ValueError("This tool is unavailable in the selected workspace mode.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            result = str(error) if isinstance(error, ValueError) else "Could not access this path or use these arguments."
            entry["detail"] = result
        return entry, result


def unified_diff(before, after, label, creating):
    if creating and not after:
        return f"--- /dev/null\n+++ b/{label}\n(empty file)\n"
    lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                 fromfile="/dev/null" if creating else "a/" + label, tofile="b/" + label)
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
