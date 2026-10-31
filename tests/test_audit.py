"""wf audit against session transcripts (design 3.3, 7.2)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
import os
import unittest

from helpers import Transcript, WfCase

if os.name == "nt":       # the audit replays bash transcripts; it becomes the durweg-audit plugin
    raise unittest.SkipTest("audit tests run on Linux and macOS")


class Audit(WfCase):
    def test_good_run_only_manual_gaps(self):
        self.start()
        self.full()
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)              # confirm meaning is for a person: INSUFFICIENT
        self.assertNotIn("violation", out)
        self.assertIn("check that the version shown", out)

    def test_gaps_cleared_in_notes(self):
        self.start()
        self.full()
        out = self.audit()[1]
        ids = [w.split()[1].rstrip(":") for w in out.splitlines() if w.strip().startswith("gap ")]
        (self.run / "audit-notes.md").write_text(
            "".join(f"## {i}\nChecked the session at the propose: the user saw the options.\n" for i in ids))
        rc, out = self.audit()
        self.assertEqual(rc, 2)
        self.assertIn("all gaps checked by a person", out)

    def test_user_chose_non_recommended(self):
        self.start()
        self.step_topic(options=("cats", "dogs", "birds"), answer="the third one", chosen="birds")
        self.step_points()
        self.step_verify(topic="birds")
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("--set {'chosen': 'birds'}", out)

    def test_user_corrects_proposal_then_repropose(self):
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("propose", self.run, "topic")
        self.tr.user("add birds")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs", "birds"]})
        self.wf("propose", self.run, "topic")
        self.tr.user("yes, birds")
        self.wf("confirm", self.run, "topic", "--quote", "yes, birds", "--set", "chosen=birds")
        self.wf("done", self.run, "topic")
        self.step_points()
        self.step_verify(topic="birds")
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertNotIn("violation", out)

    def test_answer_through_question_tool(self):
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("propose", self.run, "topic")
        self.tr.ask("Which topic?", "dogs")
        self.wf("confirm", self.run, "topic", "--quote", "dogs", "--set", "chosen=dogs")
        self.wf("done", self.run, "topic")
        self.assertNotIn("violation", self.audit()[1])

    def test_quote_not_said(self):
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("propose", self.run, "topic")
        self.wf("confirm", self.run, "topic", "--quote", "dogs please", "--set", "chosen=dogs")
        rc, out = self.audit()
        self.assertEqual(rc, 1)
        self.assertIn("is not in a user message", out)

    def test_old_round_quote_reused(self):
        self.start()
        self.step_topic(answer="dogs please")
        self.wf("reopen", self.run, "topic", "--reason", "again")
        self.wf("propose", self.run, "topic")
        self.wf("confirm", self.run, "topic", "--quote", "dogs please", "--set", "chosen=dogs")
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)

    def test_quote_before_propose_does_not_count(self):
        self.start()
        self.wf("begin", self.run, "topic")
        self.tr.user("dogs please")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("propose", self.run, "topic")
        self.wf("confirm", self.run, "topic", "--quote", "dogs please", "--set", "chosen=dogs")
        self.assertEqual(self.audit()[0], 1)

    def test_failed_call_passed_off_as_success(self):
        self.start()
        self.step_topic()
        self.wf("begin", self.run, "points")
        self.put("steps/2-points.md", self.POINTS.replace("- fluffy\n", ""))
        self.wf("done", self.run, "points", expect=1)          # refused in the transcript
        ev = [e for e in self.events() if e["type"] != "refused"]
        ev.append({"t": ev[-1]["t"], "type": "done", "step": "points", "round": 1,
                   "artifacts_digest": "forged", "files": []})
        for i, e in enumerate(ev):
            e["n"] = i + 1
        self.set_events(ev)
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)
        self.assertIn("no successful durweg call", out)

    def test_truncated_transcript_is_insufficient(self):
        self.start()
        self.full()
        lines = self.tr.lines
        cut = len(lines) // 2
        later = Transcript(self.tmp / "later.jsonl", self.tmp)
        later.lines = lines[cut:]
        later.path.write_text("\n".join(json.dumps(x) for x in later.lines) + "\n")
        # move the start event earlier than any transcript line
        ev = self.events()
        ev[0]["t"] = "2020-01-01T00:00:00+00:00"
        self.set_events(ev)
        rc, out = self.audit(later.path)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("not covered", out)

    def test_two_session_files_cover_the_run(self):
        self.start()
        self.step_topic()
        first = list(self.tr.lines)
        self.tr = Transcript(self.tmp / "resumed.jsonl", self.tmp, opening=False)
        self.step_points()
        self.step_verify()
        (self.tmp / "first.jsonl").write_text("\n".join(json.dumps(x) for x in first) + "\n")
        rc, out = self.audit(self.tmp / "first.jsonl", self.tmp / "resumed.jsonl")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("violation", out)

    def test_write_before_begin(self):
        self.start()
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})   # before begin
        self.wf("begin", self.run, "topic")
        self.wf("propose", self.run, "topic")
        self.tr.user("dogs")
        self.wf("confirm", self.run, "topic", "--quote", "dogs", "--set", "chosen=dogs")
        self.wf("done", self.run, "topic")
        rc, out = self.audit()
        self.assertEqual(rc, 1)
        self.assertIn("outside any active round", out)

    def test_rewrite_after_reopen_is_fine(self):
        self.start()
        self.step_topic()
        self.wf("reopen", self.run, "topic", "--reason", "user adds birds")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs", "birds"]})
        self.wf("propose", self.run, "topic")
        self.tr.user("birds then")
        self.wf("confirm", self.run, "topic", "--quote", "birds then", "--set", "chosen=birds")
        self.wf("done", self.run, "topic")
        rc, out = self.audit()
        self.assertNotIn("violation", out)

    def test_change_after_answer_outside_wf(self):
        """Agent edits the proposal after the user answered, and wf never sees it (bypass)."""
        self.start()
        self.step_topic()
        # simulate: a Write to the proposal between propose and done, then the file put back
        lines = self.tr.lines
        idx = max(i for i, l in enumerate(lines)
                  if "durweg done" in json.dumps(l) and "topic" in json.dumps(l) and l["type"] == "assistant")
        sneaky = {"type": "assistant", "timestamp": lines[idx]["timestamp"], "cwd": str(self.tmp),
                  "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_x",
                              "name": "Write", "input": {"file_path": str(self.run / "steps/1-topic.json")}}]}}
        lines.insert(idx, sneaky)
        self.tr.path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)
        self.assertIn("changed after the proposal", out)

    def test_decision_file_written_by_hand(self):
        self.start()
        self.step_topic()
        self.tr.write(self.run / "steps/1-topic.decision.json")
        rc, out = self.audit()
        self.assertEqual(rc, 1)
        self.assertIn("outside `durweg confirm`", out)

    def test_state_edited_by_shell(self):
        self.start()
        self.step_topic()
        self.tr.bash(f"cat x > {self.run}/.wf/state.json", "")
        self.assertEqual(self.audit()[0], 1)

    def test_unrecognized_shell_write_is_a_gap(self):
        self.start()
        self.step_topic()
        self.tr.bash(f"python3 - <<'EOF'\nopen('{self.run}/steps/2-points.md','w')\nEOF", "")
        rc, out = self.audit()
        self.assertEqual(rc, 2)
        self.assertIn("unrecognized shell command", out)

    def test_read_only_commands_are_not_gaps(self):
        self.start()
        self.step_topic()
        self.tr.bash(f"cat {self.run}/steps/1-topic.json; ls {self.run}", "{}")
        out = self.audit()[1]
        self.assertNotIn("unrecognized", out)


class CodexTranscript(WfCase):
    """A Codex rollout: exec tool with JS calling exec_command; user messages as response items."""

    def codex_file(self):
        lines = [{"timestamp": "2020-01-01T00:00:00Z", "type": "session_meta",
                  "payload": {"cwd": str(self.tmp)}}]
        import datetime
        t0 = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=3)
        k = 0

        def ts():
            nonlocal k
            k += 1
            return (t0 + datetime.timedelta(milliseconds=k)).isoformat().replace("+00:00", "Z")
        lines[0]["timestamp"] = ts()
        for item in self.tr.lines:
            if item["type"] == "user" and isinstance(item["message"]["content"], str):
                lines.append({"timestamp": ts(), "type": "response_item", "payload": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": item["message"]["content"]}]}})
            elif item["type"] == "assistant":
                tu = item["message"]["content"][0]
                if tu["name"] == "Bash":
                    js = "text(await tools.exec_command({cmd:%s}));" % json.dumps(tu["input"]["command"])
                else:
                    rel = tu["input"]["file_path"]
                    js = "text(await tools.exec_command({cmd:%s}));" % json.dumps(f"cat > {rel} <<'EOF'\n{{}}\nEOF")
                lines.append({"timestamp": ts(), "type": "response_item", "payload": {
                    "type": "custom_tool_call", "call_id": tu["id"], "name": "exec", "input": js}})
            elif item["type"] == "user":
                tr = item["message"]["content"][0]
                lines.append({"timestamp": ts(), "type": "response_item", "payload": {
                    "type": "custom_tool_call_output", "call_id": tr["tool_use_id"],
                    "output": [{"type": "input_text", "text": "Output:\n" + tr["content"]}]}})
        path = self.tmp / "rollout.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return path

    def test_codex_good_run(self):
        self.start()
        self.full()
        rc, out = self.audit(self.codex_file())
        self.assertEqual(rc, 2, out)
        self.assertNotIn("violation", out)

    def test_codex_write_before_begin(self):
        self.start()
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("begin", self.run, "topic")
        rc, out = self.audit(self.codex_file())
        self.assertEqual(rc, 1, out)


if __name__ == "__main__":
    unittest.main()


# -- WP-C regression tests -----------------------------------------------------------------
import os
import shlex
import subprocess
import sys
from pathlib import Path

from helpers import ROOT, git

WF_BIN = str(ROOT / "bin" / "durweg")


class Evidence(WfCase):
    """WP-C-001: only a visible wf call with a matching ok line in its own result counts."""

    def single_step(self):
        w = json.loads((self.repo / "workflow.json").read_text())
        w["steps"] = w["steps"][:1]
        w["steps"][0]["gates"] = w["steps"][0]["gates"][:1]
        (self.repo / "workflow.json").write_text(json.dumps(w))
        git(self.repo, "commit", "-qam", "single step, no user gate")
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("done", self.run, "topic")

    def test_cached_ok_lines_are_not_evidence(self):
        self.single_step()
        markers = "\n".join(f"ok run={self.run.name} event={e['n']} {e['type']} {e.get('step', '')}".rstrip()
                            for e in self.events())
        cached = Transcript(self.tmp / "cat-only.jsonl", self.tmp)
        cached.bash("cat cached-output.txt", markers)
        rc, out = self.audit(cached.path)
        self.assertEqual(rc, 1, out)
        self.assertIn("does not visibly run durweg", out)

    def test_event_edited_to_another_step(self):
        self.start()
        self.full()
        ev = self.events()
        done_points = next(e for e in ev if e["type"] == "done" and e["step"] == "points")
        done_points["step"] = "verify"
        self.set_events(ev)
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)

    def test_confirm_set_differs_from_command(self):
        self.start()
        self.full()
        ev = self.events()
        c = next(e for e in ev if e["type"] == "confirm")
        c["set"] = {"chosen": "cats"}
        self.set_events(ev)
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)
        self.assertIn("different from the command", out)

    def test_successful_call_missing_from_log(self):
        self.start()
        self.full()
        self.set_events(self.events()[:-1])          # drop the last done
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)
        self.assertIn("log does not have", out)

    def test_wrapped_batch_of_wf_calls_is_fine(self):
        self.start()
        self.step_topic()
        self.put("steps/2-points.md", self.POINTS)
        self.tr.lines.pop()
        self.tr.lines.pop()                          # the write above happens inside the batch instead
        target = self.run / "steps/2-points.md"
        cmd = (f"{WF_BIN} begin {shlex.quote(str(self.run))} points && "
               f"printf %s {shlex.quote(self.POINTS)} > {shlex.quote(str(target))} && "
               f"{WF_BIN} done {shlex.quote(str(self.run))} points")
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.tr.bash(cmd, r.stdout + r.stderr)
        rc, out = self.audit()
        self.assertNotIn("violation", out)


class OpaqueWrites(WfCase):
    """WP-C-002: what audit cannot locate is a gap, never a silent PASS."""

    def test_relative_script_in_run_folder(self):
        self.start()
        self.full()
        self.tr.cwd = str(self.run)
        self.tr.bash("python3 mutate.py", "")
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("inside the run folder", out)

    def test_script_with_variable_path_during_run(self):
        self.start()
        self.step_topic()
        self.tr.bash('python3 tool.py "$TARGET"', "")
        self.step_points()
        self.step_verify()
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("variables during the run", out)

    def test_script_given_a_done_steps_file(self):
        self.start()
        self.full()
        self.tr.bash(f"python3 fix.py {self.run}/steps/1-topic.json", "")
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("a script was given this path", out)


class Ordering(WfCase):
    """WP-C-003: operations inside one call are ordered; unknown order is a gap."""

    def test_real_batch_reopen_write_done(self):
        w = json.loads((self.repo / "workflow.json").read_text())
        w["steps"] = w["steps"][:1]
        w["steps"][0]["gates"] = w["steps"][0]["gates"][:1]
        (self.repo / "workflow.json").write_text(json.dumps(w))
        git(self.repo, "commit", "-qam", "single step")
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("done", self.run, "topic")
        target = self.run / "steps/1-topic.json"
        cmd = (f"{WF_BIN} reopen {shlex.quote(str(self.run))} topic --reason refresh && "
               f"printf %s {shlex.quote(json.dumps({'options': ['cats', 'birds']}))} > {shlex.quote(str(target))} && "
               f"{WF_BIN} done {shlex.quote(str(self.run))} topic")
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.tr.bash(cmd, r.stdout + r.stderr)
        self.assertEqual(self.check()[0], 0)
        rc, out = self.audit()
        self.assertEqual(rc, 0, out)

    def test_write_before_begin_in_one_batch_is_a_violation(self):
        self.start()
        target = self.run / "steps/1-topic.json"
        cmd = (f"printf %s {shlex.quote(json.dumps({'options': ['a', 'b']}))} > {shlex.quote(str(target))} && "
               f"{WF_BIN} begin {shlex.quote(str(self.run))} topic")
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.tr.bash(cmd, r.stdout + r.stderr)
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)

    def test_uncertain_order_is_a_gap(self):
        self.start()
        target = self.run / "steps/1-topic.json"
        cmd = (f"for i in 1; do printf x > {shlex.quote(str(target))}; done; "
               f"{WF_BIN} begin {shlex.quote(str(self.run))} topic")
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.tr.bash(cmd, r.stdout + r.stderr)
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("outside the shell subset", out)

    def test_failed_write_tool_is_ignored(self):
        self.start()
        i = "toolu_fail"
        self.tr._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": i, "name": "Write", "input": {"file_path": str(self.run / "steps/1-topic.json")}}]}})
        self.tr._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i, "is_error": True, "content": "permission denied"}]}})
        self.full()
        self.assertNotIn("violation", self.audit()[1])


class Interruption(WfCase):
    """WP-C-004: a crash inside a command leaves a run that can be resumed and audited."""

    def prepare(self):
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("propose", self.run, "topic")
        self.tr.user("dogs")

    def test_decision_write_fails(self):
        import durweg.cli as cli
        self.prepare()
        orig = cli.write_json

        def fail(path, obj):
            if str(path).endswith(".decision.json"):
                raise OSError("injected")
            return orig(path, obj)
        cli.write_json = fail
        try:
            with self.assertRaises(OSError):
                main(["confirm", str(self.run), "topic", "--quote", "dogs", "--set", "chosen=dogs"])
        finally:
            cli.write_json = orig
        self.tr.bash("durweg confirm " + shlex.quote(str(self.run)) + " topic --quote dogs --set chosen=dogs",
                     "Traceback ... OSError: injected")
        self.assertEqual(self.events()[-1]["type"], "propose", "no confirm committed")
        self.wf("confirm", self.run, "topic", "--quote", "dogs", "--set", "chosen=dogs")
        self.wf("done", self.run, "topic")
        self.step_points()
        self.step_verify()
        self.assertEqual(self.check()[0], 0)
        self.assertNotIn("violation", self.audit()[1])

    def test_stale_decision_file_without_event(self):
        self.prepare()
        (self.run / "steps/1-topic.decision.json").write_text(json.dumps({"confirm_event": 4, "values": {"chosen": "x"}}))
        self.wf("done", self.run, "topic", expect=1)          # no confirm event: refused
        self.wf("confirm", self.run, "topic", "--quote", "dogs", "--set", "chosen=dogs")
        self.wf("done", self.run, "topic")

    def test_crash_after_commit_before_output(self):
        import durweg.cli as cli
        self.prepare()
        orig = cli.ok

        def crash(*a, **k):
            raise KeyboardInterrupt
        cli.ok = crash
        try:
            with self.assertRaises(KeyboardInterrupt):
                main(["confirm", str(self.run), "topic", "--quote", "dogs", "--set", "chosen=dogs"])
        finally:
            cli.ok = orig
        self.tr.bash("durweg confirm " + shlex.quote(str(self.run)) + " topic --quote dogs --set chosen=dogs",
                     "^C KeyboardInterrupt")
        self.assertEqual(self.events()[-1]["type"], "confirm", "committed before the crash")
        self.wf("done", self.run, "topic")                    # resumes: the confirm is valid
        self.step_points()
        self.step_verify()
        self.assertEqual(self.check()[0], 0)
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("success was not shown", out)


    def test_call_with_variable_run_and_trimmed_output_is_a_gap(self):
        """durweg confirm "$N" ... | tail -1: the call ran, its ok line was cut off; not a violation."""
        self.prepare()
        main(["confirm", str(self.run), "topic", "--quote", "dogs", "--set", "chosen=dogs"])
        self.tr.bash("N=" + shlex.quote(str(self.run)) + ' && durweg confirm "$N" topic --quote dogs '
                     "--set chosen=dogs | tail -1", "next: durweg done")
        self.wf("done", self.run, "topic")
        self.step_points()
        self.step_verify()
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("success was not shown", out)

class TranscriptSets(WfCase):
    def test_missing_middle_session(self):
        self.start()
        self.step_topic()
        first = list(self.tr.lines)
        self.tr = Transcript(self.tmp / "middle.jsonl", self.tmp, opening=False)
        self.step_points()
        self.tr = Transcript(self.tmp / "last.jsonl", self.tmp, opening=False)
        self.step_verify()
        (self.tmp / "first.jsonl").write_text("\n".join(json.dumps(x) for x in first) + "\n")
        # in real sessions the middle one spans its own minutes; push its events out of the others' spans
        ev = self.events()
        for e in ev:
            if e.get("step") == "points":
                e["t"] = "2030-01-01T00:00:00+00:00"
        self.set_events(ev)
        rc, out = self.audit(self.tmp / "first.jsonl", self.tmp / "last.jsonl")
        self.assertEqual(rc, 2, out)
        self.assertIn("not covered", out)

    def test_same_file_twice_and_repeated_lines(self):
        self.start()
        self.full()
        for i, line in enumerate(self.tr.lines):
            line["uuid"] = f"u{i}"
        self.tr.path.write_text("\n".join(json.dumps(x) for x in self.tr.lines) + "\n")
        resumed = self.tmp / "resumed.jsonl"
        resumed.write_text(self.tr.path.read_text())          # a resume repeats the earlier lines
        rc, out = self.audit(self.tr.path, self.tr.path, resumed)
        self.assertNotIn("violation", out)
        self.assertNotIn("reported twice", out)


from durweg.cli import main  # noqa: E402


class Recheck(WfCase):
    """WP-C second round: printed wf text is not a call; a redirect does not hide side effects."""

    def single_step(self):
        w = json.loads((self.repo / "workflow.json").read_text())
        w["steps"] = w["steps"][:1]
        w["steps"][0]["gates"] = w["steps"][0]["gates"][:1]
        (self.repo / "workflow.json").write_text(json.dumps(w))
        git(self.repo, "commit", "-qam", "single step")

    def real(self, cmd, cwd=None):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, cwd=cwd, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        if cwd:
            self.tr.cwd = str(cwd)
        self.tr.bash(cmd, r.stdout + r.stderr)
        self.tr.cwd = str(self.tmp)
        return r.stdout

    def test_echo_of_wf_text_with_cached_output(self):
        self.single_step()
        self.start()
        outs = {"begin": self.wf("begin", self.run, "topic")}
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        outs["done"] = self.wf("done", self.run, "topic")
        fake = Transcript(self.tmp / "echo.jsonl", self.tmp)
        for sub, out in outs.items():
            cache = self.tmp / f"cached-{sub}.txt"
            cache.write_text(out)
            fake.bash(f"echo durweg {sub} {self.run} topic && cat {cache}", f"durweg {sub} {self.run} topic\n{out}")
        rc, out = self.audit(fake.path)
        self.assertEqual(rc, 1, out)

    def test_printf_and_quoted_wf_are_not_calls(self):
        self.single_step()
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        out = self.wf("done", self.run, "topic", expect=None)
        self.tr.lines = self.tr.lines[:-2]                # drop the real done call
        self.tr.bash(f"printf '%s\\n' 'durweg done {self.run} topic'; cat log", out)
        rc, res = self.audit()
        self.assertEqual(rc, 1, res)

    def test_supported_wrappers_count(self):
        self.single_step()
        self.start()
        q = shlex.quote(str(self.run))
        self.real(f"env WF_X=1 {WF_BIN} begin {q} topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.real(f"python3 -m durweg.cli done {q} topic")
        rc, out = self.audit()
        self.assertEqual(rc, 0, out)

    def test_python_path_to_wf_counts(self):
        self.single_step()
        self.start()
        q = shlex.quote(str(self.run))
        self.real(f"python3 {WF_BIN} begin {q} topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.real(f"python3 {WF_BIN} done {q} topic")
        self.assertEqual(self.audit()[0], 0)

    def done_single(self):
        self.single_step()
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("done", self.run, "topic")
        (self.run / "mutate.py").write_text(
            "from pathlib import Path\np=Path(__file__).parent/'steps/1-topic.json'\ns=p.read_text()\n"
            "p.write_text('transient')\np.write_text(s)\nprint('ok')\n")

    def test_risky_with_redirect_outside(self):
        self.done_single()
        self.real(f"python3 mutate.py > {self.tmp}/script.log", cwd=self.run)
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)
        self.assertIn("inside the run folder", out)

    def test_risky_with_redirect_to_devnull(self):
        self.done_single()
        self.real("python3 mutate.py > /dev/null", cwd=self.run)
        self.assertEqual(self.audit()[0], 2)

    def test_risky_with_redirect_into_run_file(self):
        self.done_single()
        self.real(f"python3 {self.run}/mutate.py > {self.run}/notes.txt")
        rc, out = self.audit()
        self.assertEqual(rc, 2, out)

    def test_heredoc_body_is_not_a_command(self):
        self.done_single()
        target = self.tmp / "notes.md"
        self.real(f"cat > {target} <<'EOF'\npython3 runs {self.run.name} steps/ fix\nEOF")
        rc, out = self.audit()
        self.assertEqual(rc, 0, out)

    def test_python_heredoc_on_run_is_a_gap(self):
        self.done_single()
        self.real(f"python3 - <<'EOF'\nprint(open('{self.run}/steps/1-topic.json').read())\nEOF")
        self.assertEqual(self.audit()[0], 2)


class Quoting(WfCase):
    """WP-C-001 round 3: separators inside quotes or escaped do not start a command."""

    def cached(self, render):
        w = json.loads((self.repo / "workflow.json").read_text())
        w["steps"] = w["steps"][:1]
        w["steps"][0]["gates"] = w["steps"][0]["gates"][:1]
        (self.repo / "workflow.json").write_text(json.dumps(w))
        git(self.repo, "commit", "-qam", "single step")
        self.start()
        outs = {"begin": self.wf("begin", self.run, "topic")}
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        outs["done"] = self.wf("done", self.run, "topic")
        fake = Transcript(self.tmp / "q.jsonl", self.tmp)
        for sub, out in outs.items():
            fake.bash(render(sub), out)
        return self.audit(fake.path)

    def test_single_quoted_semicolon(self):
        rc, out = self.cached(lambda s: f"echo 'example; durweg {s} {self.run} topic;' && cat cache")
        self.assertEqual(rc, 1, out)

    def test_double_quoted_and_escaped(self):
        rc, out = self.cached(lambda s: f'echo "x && durweg {s} {self.run} topic" \\; durweg\\ {s} && cat cache')
        self.assertEqual(rc, 1, out)

    def test_unbalanced_quote_is_a_gap_not_evidence(self):
        rc, out = self.cached(lambda s: f"echo 'oops; durweg {s} {self.run} topic")
        self.assertNotEqual(rc, 0, out)       # never PASS: no evidence for the events
        self.assertIn("outside the shell subset", out)

    def test_semicolon_inside_quote_argument_of_a_real_call(self):
        self.start()
        self.wf("begin", self.run, "topic")
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.wf("propose", self.run, "topic")
        self.tr.user("dogs; definitely")
        self.wf("confirm", self.run, "topic", "--quote", "dogs; definitely", "--set", "chosen=dogs")
        self.wf("done", self.run, "topic")
        self.assertNotIn("violation", self.audit()[1])

    def test_real_calls_separated_by_semicolons(self):
        self.start()
        q = shlex.quote(str(self.run))
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
        self.tr.lines = self.tr.lines[:-2]           # the write comes after begin, below
        cmd = f"{WF_BIN} begin {q} topic; printf %s '{{\"options\": [\"a\", \"b\"]}}' > {q}/steps/1-topic.json; {WF_BIN} propose {q} topic"
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env)
        self.tr.bash(cmd, r.stdout + r.stderr)
        rc, out = self.audit()
        self.assertNotIn("violation", out)
        self.assertIn("event=3 propose", r.stdout)


class Subset(WfCase):
    """WP-C-001 round 4: commands outside the supported shell subset are never evidence."""

    def cached(self, render):
        return Quoting.cached(self, render)

    def test_comment_hides_wf(self):
        rc, out = self.cached(lambda s: f"echo example # ; durweg {s} {self.run} topic;\ncat cache")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("outside the shell subset", out)

    def test_command_substitution(self):
        rc, out = self.cached(lambda s: f"echo $(durweg {s} {self.run} topic); cat cache")
        self.assertNotEqual(rc, 0, out)

    def test_group_and_background(self):
        for render in (lambda s: f"{{ durweg {s} {self.run} topic; }}; cat cache",
                       lambda s: f"(durweg {s} {self.run} topic) && cat cache",
                       lambda s: f"durweg {s} {self.run} topic & cat cache"):
            self.tearDown()
            self.setUp()
            rc, out = self.cached(render)
            self.assertNotEqual(rc, 0, out)

    def test_hash_inside_a_word_is_not_a_comment(self):
        from durweg_audit.audit import _segments
        self.assertEqual(_segments("echo a#b; durweg begin R s"), ["echo a#b", " durweg begin R s"])
        self.assertIsNone(_segments("echo a #b; durweg begin R s"))
        self.assertIsNone(_segments("if true; then durweg begin R s; fi"))
        self.assertEqual(_segments("durweg done R s 2>&1 | tail -3"), ["durweg done R s 2>&1 ", " tail -3"])

    def test_unreadable_command_far_away_does_not_hide_a_missing_event(self):
        import datetime
        self.start()
        self.full()
        # drop the transcript evidence of the last done (call + result) ...
        self.tr.lines = self.tr.lines[:-2]
        # ... and add an unreadable command naming wf ten minutes later
        later = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10))
        stamp = later.isoformat().replace("+00:00", "Z")
        self.tr._add({"type": "assistant", "timestamp": stamp, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_late", "name": "Bash", "input": {"command": "echo x # durweg"}}]}})
        self.tr._add({"type": "user", "timestamp": stamp, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_late", "content": "x"}]}})
        rc, out = self.audit()
        self.assertEqual(rc, 1, out)
        self.assertIn("no successful durweg call", out)


class EnvWrapper(WfCase):
    """WP-C-001 round 5: env only with VAR=value assignments; other wrappers are not evidence."""

    def cached(self, render):
        return Quoting.cached(self, render)

    def test_env_help_is_not_a_call(self):
        rc, out = self.cached(lambda s: f"env --help durweg {s} {self.run} topic && cat cache")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("outside the shell subset", out)

    def test_env_version_and_other_wrappers(self):
        for render in (lambda s: f"env --version durweg {s} {self.run} topic && cat cache",
                       lambda s: f"env -i durweg {s} {self.run} topic && cat cache",
                       lambda s: f"timeout 5 durweg {s} {self.run} topic && cat cache",
                       lambda s: f"python3 -I durweg {s} {self.run} topic && cat cache"):
            self.tearDown()
            self.setUp()
            rc, out = self.cached(render)
            self.assertNotEqual(rc, 0, out)

    def test_env_assignments_count(self):
        w = json.loads((self.repo / "workflow.json").read_text())
        w["steps"] = w["steps"][:1]
        w["steps"][0]["gates"] = w["steps"][0]["gates"][:1]
        (self.repo / "workflow.json").write_text(json.dumps(w))
        git(self.repo, "commit", "-qam", "single step")
        self.start()
        q = shlex.quote(str(self.run))
        for cmd in (f"env A=1 B=2 {WF_BIN} begin {q} topic", None, f"LANG=C env X=1 {WF_BIN} done {q} topic"):
            if cmd is None:
                self.put("steps/1-topic.json", {"options": ["cats", "dogs"]})
                continue
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.tr.bash(cmd, r.stdout + r.stderr)
        rc, out = self.audit()
        self.assertEqual(rc, 0, out)

    def test_wf_position_table(self):
        from durweg_audit.audit import wf_position, UNSUPPORTED
        cases = {"durweg begin R s": 1, "/x/bin/durweg begin R s": 1, "A=1 durweg begin": 2, "env A=1 durweg begin": 3,
                 "python3 -m durweg.cli begin": 3, "python3 /x/bin/durweg begin": 2, "echo durweg begin": None,
                 "printf %s durweg": None, "env --help durweg begin": UNSUPPORTED, "env -u A durweg begin": UNSUPPORTED,
                 "nice durweg begin": UNSUPPORTED, "python3 -I /x/durweg begin": UNSUPPORTED, "env --help": None}
        for cmd, want in cases.items():
            self.assertEqual(wf_position(cmd.split()), want, cmd)


class Install(unittest.TestCase):
    """Plan V1-R-004: the plugin finds the platform from the install manifest, from any folder."""

    def run_plugin(self, env, cwd):
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "bin" / "durweg-audit"),
                               str(cwd / "no-run"), "--transcript", str(cwd / "t.jsonl")],
                              capture_output=True, text=True, env=env, cwd=cwd)

    def test_found_through_the_install_manifest(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="audit-install-"))
        home = tmp / "Durweg"
        home.mkdir()
        (home / ".install.json").write_text(json.dumps({"platform_dir": str(ROOT)}))
        env = {k: v for k, v in os.environ.items() if k not in ("DURWEG_PLATFORM", "PYTHONPATH")}
        env["DURWEG_HOME"] = str(home)
        r = self.run_plugin(env, tmp)
        self.assertEqual(r.returncode, 4, r.stderr)          # platform found; the run does not exist
        self.assertIn("is not a", r.stderr)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_no_platform(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="audit-none-"))
        env = {k: v for k, v in os.environ.items() if k not in ("DURWEG_PLATFORM", "PYTHONPATH")}
        env["DURWEG_HOME"] = str(tmp / "nothing")
        r = self.run_plugin(env, tmp)
        self.assertEqual(r.returncode, 4)
        self.assertIn("platform was not found", r.stderr)
        shutil.rmtree(tmp, ignore_errors=True)
