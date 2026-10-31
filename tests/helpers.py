"""Test helpers: a clean copy of the example workflow, an in-process wf, a fake transcript."""

import contextlib
import datetime
import io
import json
import os
import shlex
import socket
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN))
# the platform under test: $DURWEG_PLATFORM, else a durweg checkout next to this repository
ROOT = Path(os.environ.get("DURWEG_PLATFORM") or PLUGIN.parent / "durweg").resolve()
os.environ["DURWEG_PLATFORM"] = str(ROOT)
from durweg_audit import locate_platform  # noqa: E402
locate_platform()

from durweg.cli import main  # noqa: E402
from durweg_audit.__main__ import main as audit_main  # noqa: E402

EXAMPLE = ROOT / "examples" / "mini"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class Transcript:
    """Writes a Claude Code style session file as the simulated agent works."""

    def __init__(self, path, cwd, opening=True):
        self.path, self.cwd, self.k = Path(path), str(cwd), 0
        self.lines = []
        if not opening:
            return
        early = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=2)
        self._add({"type": "user", "timestamp": early.isoformat().replace("+00:00", "Z"),
                   "message": {"role": "user", "content": "please run the mini workflow"}})

    def _add(self, d):
        d.setdefault("timestamp", utc())
        d.setdefault("cwd", self.cwd)
        self.lines.append(d)
        self.path.write_text("\n".join(json.dumps(x) for x in self.lines) + "\n")

    def _id(self):
        self.k += 1
        return f"toolu_{self.k:04d}"

    def user(self, text):
        self._add({"type": "user", "message": {"role": "user", "content": text}})

    def bash(self, command, output):
        i = self._id()
        self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": i, "name": "Bash", "input": {"command": command}}]}})
        self._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i, "content": output}]}})

    def write(self, path):
        i = self._id()
        self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": i, "name": "Write", "input": {"file_path": str(path), "content": "…"}}]}})
        self._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i, "content": "File created successfully"}]}})

    def ask(self, question, answer):
        i = self._id()
        self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": i, "name": "AskUserQuestion", "input": {"questions": [{"question": question}]}}]}})
        self._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i,
             "content": f'Your questions have been answered: "{question}"="{answer}".'}]}})


class WfCase(unittest.TestCase):
    """A temp dir with a committed copy of the example workflow and a runs dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wf-test-"))
        self.repo = self.tmp / "mini"
        shutil.copytree(EXAMPLE, self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "mini")
        self.runs = self.tmp / "runs"
        self.tr = None
        self._home = os.environ.get("DURWEG_HOME")
        os.environ["DURWEG_HOME"] = str(self.tmp / "Durweg")     # never the user's real ~/Durweg

    def tearDown(self):
        if self._home is None:
            os.environ.pop("DURWEG_HOME", None)
        else:
            os.environ["DURWEG_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wf(self, *args, expect=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([str(a) for a in args])
        text = out.getvalue() + err.getvalue()
        if self.tr is not None and args[0] not in ("check", "audit"):
            self.tr.bash("durweg " + " ".join(shlex.quote(str(a)) for a in args), text)
        if expect is not None:
            self.assertEqual(rc, expect, f"wf {' '.join(map(str, args))} -> {rc}\n{text}")
        return text

    def start(self, slug="demo", *extra, transcript=True):
        if transcript:
            self.tr = Transcript(self.tmp / "session.jsonl", self.tmp)
        self.wf("start", self.repo, slug, "--runs", self.runs, *extra)
        self.run = next(self.runs.glob(f"*_{slug}"))
        return self.run

    def put(self, rel, content):
        p = self.run / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content))
        if self.tr is not None:
            self.tr.write(p)

    # one full, correct pass through the three steps
    def step_topic(self, options=("cats", "dogs"), answer="dogs please", chosen="dogs"):
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": list(options)})
        self.wf("propose", self.run, "topic")
        if self.tr is not None:
            self.tr.user(answer)
        self.wf("confirm", self.run, "topic", "--quote", answer, "--set", f"chosen={chosen}")
        self.wf("done", self.run, "topic")

    POINTS = "## Points\n- loyal\n- loud\n- fluffy\n\n## Sources\n| point | source |\n|---|---|\n| loyal | book |\n"

    def step_points(self, text=None):
        self.wf("begin", self.run, "points")
        self.put("steps/2-points.md", text or self.POINTS)
        self.wf("done", self.run, "points")

    def step_verify(self, topic="dogs"):
        self.wf("begin", self.run, "verify")
        self.put("evidence/a.txt", "looked at a book\n")
        self.put("steps/3-verify.json", {"topic": topic, "checked": ["loyal", "loud", "fluffy"]})
        self.wf("done", self.run, "verify")

    def full(self):
        self.step_topic()
        self.step_points()
        self.step_verify()

    def check(self):
        out, rc = io.StringIO(), None
        with contextlib.redirect_stdout(out):
            rc = main(["check", str(self.run)])
        return rc, out.getvalue()

    def audit(self, *files):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = audit_main([str(self.run), "--transcript", *[str(f) for f in (files or [self.tr.path])]])
        return rc, out.getvalue()

    def events(self):
        return json.loads((self.run / ".wf/state.json").read_text())["events"]

    def set_events(self, events):
        p = self.run / ".wf/state.json"
        d = json.loads(p.read_text())
        d["events"] = events
        p.write_text(json.dumps(d))
