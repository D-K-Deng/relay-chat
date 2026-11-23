import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from relay import terminal
from relay.project_files import ProjectFiles


class TerminalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = ProjectFiles(self.root, "edit", threading.Event(), threading.Lock())
        self.proposals = []
        self.chunks = []

    def run_command(self, argv, allow=True, cwd=".", on_output=None):
        def review(proposal):
            self.proposals.append(proposal)
            return allow

        def emit(kind, **fields):
            if kind == "terminal" and fields["text"]:
                self.chunks.append(fields["text"])
                if on_output:
                    on_output(fields["text"])

        return terminal.run_terminal(self.project, {"argv": argv, "cwd": cwd}, review, emit)

    def write_test(self, body):
        code = "import os, subprocess, sys, time, unittest\nclass Sample(unittest.TestCase):\n    def test_sample(self):\n"
        (self.root / "test_sample.py").write_text(code + "".join("        " + line + "\n" for line in body.splitlines()),
                                                encoding="utf-8")

    def test_unittest_streams_failure_and_excludes_key(self):
        self.write_test("print('child ready', flush=True)\nassert 'OPENAI_API_KEY' not in os.environ\nself.fail('expected failure')")
        with patch.object(os, "environ", {**os.environ, "OPENAI_API_KEY": "fixture-only"}):
            entry, result = self.run_command(["python", "-m", "unittest", "discover"])
        self.assertFalse(self.proposals[0]["read_only"])
        self.assertEqual(self.proposals[0]["directory"], str(self.root))
        self.assertTrue(Path(self.proposals[0]["effective_argv"][0]).is_absolute())
        self.assertEqual(entry["exit_code"], 1)
        self.assertEqual(entry["status"], "error")
        self.assertEqual("".join(self.chunks), entry["output"])
        self.assertIn("child ready", result)
        self.assertIn("expected failure", result)
        self.assertNotIn("fixture-only", result)

    def test_read_only_query_and_workspace_boundary(self):
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        (self.root / "notes.txt").write_text("hello", encoding="utf-8")
        subprocess.run(["git", "add", "notes.txt"], cwd=self.root, check=True)
        self.project.mode = "read"
        entry, _ = self.run_command(["git", "ls-files"])
        self.assertEqual(entry["status"], "done")
        self.assertIn("notes.txt", entry["output"])
        self.assertTrue(self.proposals[0]["read_only"])
        # A nested project without its own Git root must not expose the parent repo.
        nested = self.root / "nested"
        nested.mkdir()
        self.project.root = nested
        entry, _ = self.run_command(["git", "ls-files"])
        self.assertEqual(entry["status"], "error")
        self.assertEqual(len(self.proposals), 1)

    def test_disallowed_and_declined_commands_do_not_start(self):
        self.write_test("open('ran.txt', 'w').close()")
        for args in (["git", "push"], ["python", "-c", "print(1)"], ["pytest", "@options.txt"], ["cmd", "/c", "dir"]):
            entry, _ = self.run_command(args)
            self.assertEqual(entry["status"], "error")
        self.assertEqual(self.proposals, [])
        entry, _ = self.run_command(["python", "-m", "unittest", "discover"], allow=False)
        self.assertEqual(entry["status"], "denied")
        self.assertFalse((self.root / "ran.txt").exists())

    def test_stop_terminates_child_tree(self):
        self.write_test("subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                        "print('running', flush=True)\ntime.sleep(60)")
        entry, _ = self.run_command(["python", "-m", "unittest", "discover"],
                                    on_output=lambda text: self.project.stopped.set() if "running" in text else None)
        self.assertEqual(entry["status"], "stopped")
        self.assertIn("running", entry["output"])
        self.assertIn("stopped", entry["detail"])

    def test_timeout_and_output_limit(self):
        self.write_test("print('a' * 40000, flush=True)\ntime.sleep(30)")
        with patch.object(terminal, "TIMEOUT", 2):
            entry, _ = self.run_command(["python", "-m", "unittest", "discover"])
        self.assertEqual(entry["status"], "error")
        self.assertIn("timed out", entry["detail"])
        self.assertIn("[Output clipped", entry["output"])
        self.assertLess(len(entry["output"]), terminal.MAX_OUTPUT + 100)
