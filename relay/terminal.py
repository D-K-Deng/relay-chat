"""Run reviewed, noninteractive development commands for a workspace turn."""
import codecs
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


MAX_OUTPUT = 24_000
TIMEOUT = 120
TERMINAL_TOOL = {
    "type": "function", "name": "run_terminal", "strict": True,
    "description": (
        "Run one local development command in a workspace directory. Use a literal argument array. "
        "Git supports --version, branch --show-current, ls-files, log --oneline, status --short, "
        "diff, diff --stat and diff --cached --stat. Tests/builds support python -m unittest discover, "
        "python -m pytest, npm test, npm run build/test/lint/check/typecheck, cargo check/test/build "
        "and go test/build (optionally with ./...). Project code requires human approval. Output is limited "
        "to 24 KB and 120 seconds."
    ),
    "parameters": {"type": "object", "properties": {
        "argv": {"type": "array", "items": {"type": "string"}, "description": "Program and separate arguments."},
        "cwd": {"type": "string", "description": "Workspace-relative directory; use '.' for the root."},
    }, "required": ["argv", "cwd"], "additionalProperties": False},
}


def host_paths():
    paths = [Path(p) for p in os.get_exec_path() if Path(p).is_absolute()]
    if sys.platform == "darwin":  # Finder's default PATH omits Homebrew.
        paths += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    return paths


def executable(name, project, paths):
    filename = name + ".exe" if os.name == "nt" else name
    for folder in paths:
        candidate = folder / filename
        if (not candidate.resolve().is_relative_to(project.root)
                and candidate.is_file() and os.access(candidate, os.X_OK)):
            return str(candidate)
    raise ValueError(f"Install {name} on this computer and make it available on PATH.")


def git_query(args):
    """Only index and history queries are eligible for automatic approval."""
    if args in (["--version"], ["ls-files"], ["branch", "--show-current"]):
        return args, True
    if args[:2] == ["log", "--oneline"]:
        tail = args[2:]
        count = "10"
        if len(tail) == 1 and re.fullmatch(r"-[1-9][0-9]?", tail[0]):
            count = tail[0][1:]
        elif len(tail) == 2 and tail[0] == "-n" and re.fullmatch(r"[1-9][0-9]?", tail[1]):
            count = tail[1]
        elif tail:
            raise ValueError("Use git log --oneline or git log --oneline -n 10.")
        return ["log", "--oneline", "--no-decorate", "--no-notes", "--no-show-signature", "-n", count], True
    if args and args[0] == "status" and args[1:] in ([], ["--short"], ["--porcelain"]):
        return ["status", "--short", "--ignore-submodules=all"], False
    if args and args[0] == "diff" and all(a in {"--stat", "--name-only", "--cached", "--staged"} for a in args[1:]):
        summary = "--stat" in args or "--name-only" in args
        staged = "--cached" in args or "--staged" in args
        return [*args, "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--", "."], summary and staged
    raise ValueError("Use a supported Git query such as git ls-files, git status --short or git diff --stat.")


def project_repository(project, cwd):
    # Do not let Git discover a parent repository outside the user's selection.
    folder = cwd
    while True:
        marker = folder / ".git"
        if marker.exists():
            return folder
        if folder == project.root:
            raise ValueError("Choose a directory in a Git repository inside this workspace.")
        folder = folder.parent


def build_command(project, cwd, argv, paths):
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError("Use a program and its arguments as an array of strings.")
    name, args = argv[0], argv[1:]
    if name == "git":
        options, read_only = git_query(args)
        cmd = [executable("git", project, paths), "--no-pager", "--no-optional-locks",
               "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}"]
        if args != ["--version"]:
            repo = project_repository(project, cwd)
            marker = repo / ".git"
            # Linked worktrees can refer to metadata stored outside the selected folder.
            read_only = read_only and marker.is_dir() and marker.resolve() == marker
            cmd.extend([f"--work-tree={repo}", f"--git-dir={marker}"])
        return [*cmd, *options], read_only
    if name in ("python", "python3", "pytest"):
        args = ["-m", "pytest", *args] if name == "pytest" else args
        if args[:3] == ["-m", "unittest", "discover"]:
            options = args[3:]
            index = 0
            while index < len(options):
                item = options[index]
                index += 1
                if item in ("-v", "-q", "-f"):
                    continue
                if item == "-s" and index < len(options):
                    project.path((cwd.relative_to(project.root) / options[index]).as_posix())
                    index += 1
                    continue
                raise ValueError("Use unittest discover with optional -s <directory>, -v, -q or -f.")
        elif args[:2] == ["-m", "pytest"]:
            for item in args[2:]:
                if item in ("-q", "-v", "-x"):
                    continue
                if item.startswith(("-", "@")):
                    raise ValueError("Use pytest with a workspace test path and optional -q, -v or -x.")
                project.path((cwd.relative_to(project.root) / item.split("::")[0]).as_posix())
        else:
            raise ValueError("Use python -m unittest discover or python -m pytest.")
        venv = project.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if venv.is_file():
            program = str(venv)
        else:
            program = executable("python" if os.name == "nt" else "python3", project, paths)
        return [program, *args], False
    if name == "npm" and (args == ["test"] or (len(args) == 2 and args[0] == "run"
                                               and args[1] in ("build", "test", "lint", "check", "typecheck"))):
        if not (cwd / "package.json").is_file():
            raise ValueError("Choose the workspace directory containing package.json.")
        if os.name == "nt":
            # Invoke npm's JavaScript entry point directly. A .cmd shim would introduce cmd.exe parsing.
            node = executable("node", project, paths)
            for folder in [Path(node).parent, *paths]:
                cli = folder / "node_modules/npm/bin/npm-cli.js"
                if cli.is_file() and not cli.resolve().is_relative_to(project.root):
                    return [node, str(cli), *args], False
            raise ValueError("Install npm with Node.js on this computer.")
        return [executable(name, project, paths), *args], False
    if name == "cargo" and args in (["check"], ["test"], ["build"]):
        if not (cwd / "Cargo.toml").is_file():
            raise ValueError("Choose the workspace directory containing Cargo.toml.")
        return [executable(name, project, paths), args[0], "--offline"], False
    if name == "go" and args in (["test"], ["build"], ["test", "./..."], ["build", "./..."]):
        if not any((cwd / marker).is_file() for marker in ("go.mod", "go.work")):
            raise ValueError("Choose the workspace directory containing go.mod or go.work.")
        return [executable(name, project, paths), *args], False
    raise ValueError("Choose a supported Git, Python, npm, Cargo or Go development command.")


def child_environment(paths):
    keep = ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "HOMEDRIVE", "HOMEPATH", "USERPROFILE",
            "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update(PATH=os.pathsep.join(map(str, paths)), PYTHONUTF8="1", PYTHONUNBUFFERED="1", CI="1", NO_COLOR="1",
               GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="", GOPROXY="off",
               GOTOOLCHAIN="local", npm_config_offline="true")
    return env


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class JobLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                    ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t),
                    ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class ExtendedJobLimits(ctypes.Structure):
        _fields_ = [("limits", JobLimits), ("io_counters", ctypes.c_uint64 * 6), ("memory", ctypes.c_size_t * 4)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]


def tree_cleanup(process):
    if os.name == "nt":
        job = kernel.CreateJobObjectW(None, None)
        limits = ExtendedJobLimits()
        limits.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not kernel.AssignProcessToJobObject(job, int(process._handle))):
            error = ctypes.WinError(ctypes.get_last_error())
            if job:
                kernel.CloseHandle(job)
            process.kill()
            process.wait()
            raise error
        return lambda: kernel.CloseHandle(job)

    def stop_group():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return stop_group


def read_output(stream, events):
    remaining = MAX_OUTPUT
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    clipped = False
    try:
        with stream:
            while chunk := stream.read1(4096):
                if remaining:
                    piece = chunk[:remaining]
                    events.put(decoder.decode(piece))
                    remaining -= len(piece)
                else:
                    piece = b""
                if len(piece) < len(chunk) and not clipped:
                    events.put(decoder.decode(b"", final=True) + "\n[Output clipped at 24 KB]\n")
                    clipped = True
        if not clipped:
            events.put(decoder.decode(b"", final=True))
    finally:
        events.put(None)


def stream_command(cmd, cwd, env, stopped, publish, heartbeat):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, **options) as process:
        cleanup = tree_cleanup(process)
        events = queue.Queue()
        reader = threading.Thread(target=read_output, args=(process.stdout, events), daemon=True)
        reader.start()
        deadline = time.monotonic() + TIMEOUT
        output, interrupted, eof = [], "", False
        try:
            while not eof or process.poll() is None:
                if stopped.is_set() or time.monotonic() >= deadline:
                    interrupted = "Command stopped." if stopped.is_set() else f"Command timed out after {TIMEOUT} seconds."
                    break
                try:
                    chunk = events.get(timeout=.2)
                except queue.Empty:
                    heartbeat()
                    continue
                if chunk is None:
                    eof = True
                elif chunk:
                    output.append(chunk)
                    publish(chunk)
        finally:
            cleanup()
            process.wait()
            reader.join()
        return "".join(output), process.returncode, interrupted


def run_terminal(project, args, review, emit):
    entry = {"tool": "run_terminal", "path": "", "status": "error"}
    try:
        cwd = project.path(args["cwd"])
        if not cwd.is_dir():
            raise ValueError("Choose an existing workspace directory for the command.")
        relative = cwd.relative_to(project.root).as_posix()
        argv = args["argv"]
        paths = host_paths()
        cmd, read_only = build_command(project, cwd, argv, paths)
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        fields = {"path": relative, "cwd": relative, "directory": str(cwd), "argv": argv, "command": command,
                  "effective_argv": cmd}
        entry.update(fields)
        if not read_only and project.mode != "edit":
            raise ValueError("This command requires the Review edits workspace mode and a Run decision.")
        if not review({"tool": "run_terminal", **fields, "read_only": read_only}) or project.stopped.is_set():
            reason = "Command stopped before execution." if project.stopped.is_set() else "Command declined."
            entry.update(status="denied", detail=reason)
            return entry, reason
        if project.path(args["cwd"]) != cwd:
            raise ValueError("The working directory changed during review.")
        def publish(text):
            emit("terminal", **fields, text=text)
        publish("")
        output, code, interrupted = stream_command(cmd, cwd, child_environment(paths), project.stopped,
                                                   publish, lambda: emit("ping"))
        detail = interrupted or f"Exited with code {code}."
        entry.update(status="stopped" if project.stopped.is_set() else "error" if interrupted or code else "done",
                     detail=detail, output=output, exit_code=code)
        return entry, detail + ("\n" + output if output else "")
    except (ValueError, KeyError, TypeError, OSError) as error:
        entry["detail"] = str(error)
        return entry, entry["detail"]
