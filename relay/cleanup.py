"""Clean up a chat's files while preserving results referenced by other chats."""
import errno
import json
import shutil
from pathlib import Path


def file_stamp(path):
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"


def record_project_write(store, owner, path, before, created_folders):
    """Track only files this chat created, while they still match its previous write."""
    key, current = str(path.resolve()), file_stamp(path)
    for other, files in store.temporary_creations.items():
        if other != owner:
            files.pop(key, None)
    with store.db() as db:
        db.execute("DELETE FROM project_creations WHERE path=? AND chat<>?", (key, owner))
        if not owner.startswith("temp:"):
            if before is None:
                db.execute("INSERT OR REPLACE INTO project_creations VALUES (?,?,?,?)",
                           (owner, key, current, json.dumps(created_folders)))
            else:
                db.execute("UPDATE project_creations SET stamp=? WHERE chat=? AND path=? AND stamp=?",
                           (current, owner, key, before))
            return
    files = store.temporary_creations.setdefault(owner, {})
    if before is None:
        files[key] = (current, created_folders)
    elif key in files and files[key][0] == before:
        files[key] = (current, files[key][1])


def entries(store, db, owner):
    if owner.startswith("temp:"):
        return store.temporary_creations.get(owner, {})
    if not db.execute("SELECT 1 FROM chats WHERE id=?", (owner,)).fetchone():
        return None
    return {path: (stamp, json.loads(folders)) for path, stamp, folders in db.execute(
        "SELECT path,stamp,folders FROM project_creations WHERE chat=? ORDER BY path", (owner,))}


def still_owned(path, stamp):
    return path.resolve() == path and path.is_file() and file_stamp(path) == stamp


def project_roots(store, db):
    selected = db.execute("SELECT DISTINCT workspace_directory FROM chats WHERE workspace_mode IN ('read','edit')")
    directories = [row[0] for row in selected] + list(store.temporary_projects.values())
    directories.append(store.read_settings(db)["workspace_directory"])
    return {Path(name) for name in directories if name}


def shared_folder(store, db, owner, folder, roots):
    if any(folder.is_relative_to(root) or root.is_relative_to(folder) for root in roots):
        return True
    # A second chat may have created files here before switching projects.
    other_files = (row[0] for row in db.execute("SELECT path FROM project_creations WHERE chat<>?", (owner,)))
    return (any(Path(name).is_relative_to(folder) for name in other_files) or
            any(Path(name).is_relative_to(folder) for chat, files in store.temporary_creations.items()
                if chat != owner for name in files))


def preview(store, owner):
    with store.db() as db:
        files = entries(store, db, owner)
        if files is None:
            raise KeyError("Chat not found.")
        folder = None if owner.startswith("temp:") else store.chat_folder(owner)
        plan = {"folder": None, "project_files": sorted(name for name, (stamp, _) in files.items()
                                                       if still_owned(Path(name), stamp))}
        if folder and folder.is_dir() and folder.resolve() == folder:
            key = "shared_folder" if shared_folder(store, db, owner, folder, project_roots(store, db)) else "folder"
            plan[key] = str(folder)
        return plan


def preserve_branch_files(store, db, owner):
    rows = db.execute("""SELECT DISTINCT m.id,m.chat,m.response_details FROM messages m,
                         json_each(m.response_details,'$.artifacts') f
                         WHERE m.chat<>? AND json_extract(f.value,'$.chat')=?""", (owner, owner)).fetchall()
    for row in rows:
        details = json.loads(row["response_details"])
        for file in details["artifacts"]:
            if file.get("chat") != owner or file.get("error"):
                continue
            suffix = Path("Results") / file["run"] / file["name"]
            source = store.chat_folder(owner) / suffix
            destination = store.chat_folder(row["chat"]) / suffix
            if not source.is_file():
                continue  # A missing source was already unavailable before deletion.
            if (not source.is_relative_to(store.chat_folder(owner)) or source.resolve() != source or
                    not destination.is_relative_to(store.chat_folder(row["chat"])) or destination.resolve() != destination):
                raise ValueError("The saved result folder changed. Choose history-only deletion or restore the folder.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            file["chat"] = row["chat"]
        db.execute("UPDATE messages SET response_details=? WHERE id=?",
                   (json.dumps(details, ensure_ascii=False), row["id"]))


def delete(store, owner, remove_folder=False, project_files=()):
    skipped = []
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        files = entries(store, db, owner)
        if files is None:
            return {"ok": True, "changed": []}
        temporary = owner.startswith("temp:")
        folder = None if temporary else store.chat_folder(owner)
        roots = project_roots(store, db)
        if remove_folder and folder and folder.exists():
            if folder.parent != store.path.parent / "chat-workspaces" or folder.resolve() != folder:
                raise ValueError("The chat folder location changed. Choose history-only deletion or restore the folder.")
            if shared_folder(store, db, owner, folder, roots):
                raise ValueError("This chat folder is shared with a project. Reopen the delete dialog to keep that folder.")
            preserve_branch_files(store, db, owner)
        try:
            parents = set()
            for name in project_files:
                if name not in files:
                    continue
                path = Path(name)
                stamp, folders = files[name]
                if still_owned(path, stamp):
                    path.unlink()
                elif path.exists():
                    skipped.append(name)
                parents.update(map(Path, folders))
            for parent in sorted(parents, key=lambda path: len(path.parts), reverse=True):
                if parent.is_dir() and parent.resolve() == parent and not any(root.is_relative_to(parent) for root in roots):
                    try:
                        parent.rmdir()
                    except OSError as error:
                        if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                            raise
            if remove_folder and folder and folder.exists():
                shutil.rmtree(folder)
        except OSError:
            # A locked file can interrupt cleanup. Keep the history for retry and
            # commit branch copies even if part of the source has already gone.
            db.commit()
            raise ValueError("Could not remove all selected files. Close any open files and try again.") from None
        if temporary:
            store.temporary_creations.pop(owner, None)
            store.temporary_projects.pop(owner, None)
        else:
            db.execute("DELETE FROM drafts WHERE chat=?", (owner,))
            db.execute("DELETE FROM chats WHERE id=?", (owner,))
    return {"ok": True, "changed": skipped}
