"""wf audit: compare a run's event log with the agent's own session transcripts (design 3.3).

Result: PASS (0), VIOLATION (1), INSUFFICIENT (2). Gaps that need a person are listed with a
stable id; a person clears them in <run>/audit-notes.md (`## G-xxxxxxxx` + what was checked).

Evidence model (WP-C-001..004):
- A transcript tool call is split into ordered operations: visible `wf` invocations, recognised
  writes, and risky commands. Each operation gets a position inside its call, so a batch like
  `durweg reopen … && printf … > file && wf done …` is ordered by its parts.
- An event counts as executed only when a visible `wf` invocation of the same command, step and
  run (and, for confirm, the same quote and --set values) has an `ok run=<id> event=<n> <type> <step>`
  line in the result of *that* call. `ok` lines anywhere else are not evidence.
- Whatever cannot be located or ordered becomes a gap (INSUFFICIENT), never a silent PASS and
  never a VIOLATION.
"""

import datetime
import fnmatch
import hashlib
import json
import os
import re
import shlex
from pathlib import Path

from durweg import workflow
from durweg.state import CHANGING, RunState, replay

OK_LINE = re.compile(r"^ok run=(\S+) event=(\d+) (\w+)(?: (\S+))?\s*$", re.M)
SUBS = ("start", "begin", "propose", "confirm", "done", "reopen", "status", "render", "check", "audit")
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
RISKY = re.compile(r"\b(python3?|perl|node|ruby|awk|rsync|dd|install|ln|truncate|sponge|unzip|tar|bash|sh|zsh|"
                   r"make|git\s+(checkout|restore|apply|stash|reset))\b|\bsed\s+(-\w*i|--in-place)|^\s*\./")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
JS_CMD = re.compile(r"\bcmd\s*:\s*(\"(?:[^\"\\]|\\.)*\")")
SYSTEMISH = ("<", "# AGENTS.md instructions")


def _ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return ""


# -- transcripts -> items --------------------------------------------------------------
class Stream:
    """Items in order: user {text}, call {id, cmds, paths, cwd}, result {id, text, error}."""

    def __init__(self):
        self.items, self.spans, self.results, self.file = [], [], {}, 0

    def add(self, kind, t, **kw):
        if kw.get("id") is not None:          # tool ids are only unique within one session file
            kw["id"] = f"{self.file}:{kw['id']}"
        kw.update(kind=kind, t=t, pos=len(self.items))
        self.items.append(kw)
        if kind == "result" and kw.get("id"):
            self.results.setdefault(kw["id"], kw)


def _claude(lines, st):
    ask_ids = set()
    for d in lines:
        typ, t = d.get("type"), _ts(d.get("timestamp"))
        if typ not in ("user", "assistant"):
            continue
        content = (d.get("message") or {}).get("content")
        cwd = d.get("cwd")
        if typ == "user":
            if isinstance(content, str):
                if not d.get("isMeta") and not content.lstrip().startswith(SYSTEMISH):
                    st.add("user", t, text=content)
                continue
            for it in content or []:
                if it.get("type") == "text" and not d.get("isMeta"):
                    if not it["text"].lstrip().startswith(SYSTEMISH):
                        st.add("user", t, text=it["text"])
                elif it.get("type") == "tool_result":
                    txt = _text(it.get("content"))
                    if it.get("tool_use_id") in ask_ids:
                        st.add("user", t, text=txt)
                    st.add("result", t, id=it.get("tool_use_id"), text=txt, error=bool(it.get("is_error")))
        else:
            for it in content or []:
                if it.get("type") != "tool_use":
                    continue
                name, inp = it.get("name"), it.get("input") or {}
                if name == "AskUserQuestion":
                    ask_ids.add(it.get("id"))
                elif name == "Bash":
                    st.add("call", t, id=it.get("id"), cmds=[inp.get("command", "")], paths=[], cwd=cwd)
                elif name in WRITE_TOOLS:
                    p = inp.get("file_path") or inp.get("notebook_path")
                    st.add("call", t, id=it.get("id"), cmds=[], paths=[p] if p else [], cwd=cwd)


def _codex(lines, st):
    cwd = None
    for d in lines:
        p, t = d.get("payload") or {}, _ts(d.get("timestamp"))
        if d.get("type") in ("session_meta", "turn_context") and p.get("cwd"):
            cwd = p["cwd"]
        if d.get("type") != "response_item":
            continue
        pt = p.get("type")
        if pt == "message" and p.get("role") == "user":
            txt = _text(p.get("content"))
            if not txt.lstrip().startswith(SYSTEMISH):
                st.add("user", t, text=txt)
        elif pt in ("function_call", "custom_tool_call"):
            name = p.get("name", "")
            raw = p.get("arguments") if pt == "function_call" else p.get("input", "")
            cmds, paths = [], []
            if pt == "function_call":
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except ValueError:
                    args = {}
                c = args.get("cmd") or args.get("command")
                if isinstance(c, list):
                    c = " ".join(shlex.quote(x) for x in c)
                if c:
                    cmds.append(c)
                if "patch" in name:
                    paths += PATCH_FILE.findall(args.get("input", "") or raw or "")
            else:
                text = raw if isinstance(raw, str) else json.dumps(raw)
                for m in JS_CMD.finditer(text):
                    try:
                        cmds.append(json.loads(m.group(1)))
                    except ValueError:
                        cmds.append(m.group(1))
                paths += PATCH_FILE.findall(text.replace("\\n", "\n"))
            st.add("call", t, id=p.get("call_id"), cmds=cmds, paths=paths, cwd=cwd)
        elif pt in ("function_call_output", "custom_tool_call_output"):
            out = p.get("output")
            if isinstance(out, dict):
                out = out.get("content") or out.get("output") or json.dumps(out)
            st.add("result", t, id=p.get("call_id"), text=_text(out), error=False)


def load_transcripts(files):
    loaded, seen_files = [], set()
    for f in files:
        raw = Path(f).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen_files:          # the same file given twice
            continue
        seen_files.add(digest)
        lines = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                pass
        times = [x for x in (_ts(d.get("timestamp")) for d in lines) if x]
        loaded.append((f, lines, min(times) if times else None, max(times) if times else None))
    loaded.sort(key=lambda x: x[2] or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))
    st, seen_lines = Stream(), set()
    for idx, (f, lines, t0, t1) in enumerate(loaded):
        st.file = idx
        fresh = []
        for d in lines:                   # a resumed session repeats earlier lines
            key = d.get("uuid") or json.dumps(d, sort_keys=True)
            if key not in seen_lines:
                seen_lines.add(key)
                fresh.append(d)
        codex = any(d.get("type") in ("response_item", "session_meta") for d in lines[:50])
        (_codex if codex else _claude)(fresh, st)
        st.spans.append((str(f), t0, t1))
    return st


# -- calls -> ordered operations -------------------------------------------------------
KEYWORDS = {"for", "while", "until", "if", "then", "else", "elif", "fi", "do", "done", "case", "esac",
            "select", "function", "eval", "time", "coproc", "!", "[[", "]]", "{", "}"}


def _segments(cmd):
    """Split a command of the supported subset into simple commands (WP-C-001, round 4).

    Supported: simple commands joined by ; && || | and newlines; words with single quotes,
    double quotes and backslash escapes; redirections (> >> 2> 2>&1 &> < <<<); here-documents
    (their bodies are removed first). Anything else - comments, $( ), backticks, ( ) groups,
    { } blocks, a lone & (background), <( ) >( ), control keywords, eval - is outside the subset:
    the function returns None and the caller treats the whole command as unparsed, which is a
    gap and never evidence.
    """
    segs, cur, i, quote, word_start = [], [], 0, None, True
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if quote == "'":
            if c == "'":
                quote = None
            cur.append(c)
            i += 1
            continue
        if quote == '"':
            if c == "\\" and i + 1 < n:
                cur.append(cmd[i:i + 2])
                i += 2
                continue
            if c == "`" or cmd.startswith("$(", i):
                return None
            if c == '"':
                quote = None
            cur.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if cmd[i + 1] == "\n":            # line continuation
                i += 2
                continue
            cur.append(cmd[i:i + 2])
            i += 2
            word_start = False
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
            i += 1
            word_start = False
            continue
        if c == "#" and word_start:
            return None                       # a comment
        if c == "`" or cmd.startswith("$(", i) or c in "(){}" or cmd.startswith("<(", i) or cmd.startswith(">(", i):
            return None
        two = cmd[i:i + 2]
        if two in ("&&", "||"):
            segs.append("".join(cur))
            cur, i, word_start = [], i + 2, True
            continue
        if c == "&":
            prev = cmd[i - 1] if i else ""
            if prev == ">" or cmd.startswith("&>", i):   # 2>&1, &>file
                cur.append(c)
                i += 1
                word_start = False
                continue
            return None                       # background
        if c in ";|\n":
            segs.append("".join(cur))
            cur, i, word_start = [], i + 1, True
            continue
        cur.append(c)
        word_start = c in " \t"
        i += 1
    if quote:
        return None
    segs.append("".join(cur))
    segs = [s for s in segs if s.strip()]
    for s in segs:
        toks = _tokens(s)
        if toks and toks[0] in KEYWORDS:
            return None
    return segs


def _tokens(seg):
    try:
        return shlex.split(seg, comments=False)
    except ValueError:
        return seg.split()


ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


UNSUPPORTED = "unsupported"
WRAPPERS = {"nice", "nohup", "timeout", "sudo", "command", "exec", "stdbuf", "xargs", "setsid", "script",
            "chrt", "ionice", "taskset", "unbuffer", "watch", "flock", "su"}


def _names_wf(tok):
    return os.path.basename(tok) == "durweg" or tok in ("durweg.cli", "durweg")


def wf_position(toks):
    """Index of the wf subcommand if the segment *executes* wf; None if it does not;
    UNSUPPORTED if wf appears behind a wrapper form audit does not read (WP-C-001).

    Supported: `durweg …`, `/path/to/wf …`, `VAR=x wf …`, `env VAR=x … wf …` (assignments only),
    `python3 -m durweg.cli …`, `python3 /path/to/bin/wf …`. echo/printf text and other arguments are
    not calls. env with options, other wrappers (nice, timeout, sudo, …) and python with options
    in front of wf are unsupported: never evidence, a gap instead.
    """
    later_wf = any(_names_wf(x) for x in toks)
    i = 0
    while i < len(toks) and ASSIGN.match(toks[i]):
        i += 1
    if i < len(toks) and os.path.basename(toks[i]) == "env":
        i += 1
        while i < len(toks) and ASSIGN.match(toks[i]):
            i += 1
        if i < len(toks) and toks[i].startswith("-"):
            return UNSUPPORTED if later_wf else None
    if i >= len(toks):
        return None
    head = os.path.basename(toks[i])
    if head == "durweg":
        return i + 1
    if re.match(r"^python3?(\.\d+)?$", head):
        if toks[i + 1:i + 3] in (["-m", "durweg.cli"], ["-m", "durweg"]):
            return i + 3
        if i + 1 < len(toks) and os.path.basename(toks[i + 1]) == "durweg":
            return i + 2
        return UNSUPPORTED if later_wf else None
    if head in WRAPPERS and later_wf:
        return UNSUPPORTED
    return None


def parse_wf(toks):
    """If the tokens execute wf, return {sub, pos, quote, set}."""
    j = wf_position(toks)
    if j == UNSUPPORTED:
        return UNSUPPORTED
    if j is None or j >= len(toks) or toks[j] not in SUBS:
        return None
    rest, info = toks[j + 1:], {"sub": toks[j], "pos": [], "quote": None, "set": {}}
    k = 0
    while k < len(rest):
        a = rest[k]
        if a in ("--quote", "--reason", "--runs", "--agent", "--session") and k + 1 < len(rest):
            if a == "--quote":
                info["quote"] = rest[k + 1]
            k += 2
            continue
        if a == "--set" and k + 1 < len(rest):
            key, _, val = rest[k + 1].partition("=")
            info["set"][key] = val
            k += 2
            continue
        if a.startswith("--quote="):
            info["quote"] = a.split("=", 1)[1]
        elif a.startswith("--set="):
            key, _, val = a.split("=", 1)[1].partition("=")
            info["set"][key] = val
        elif not a.startswith("-"):
            info["pos"].append(a)
        k += 1
    return info


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def strip_heredocs(cmd):
    """Drop here-document bodies, so text inside them is not read as commands."""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        ends = [m.group(2) for m in HEREDOC.finditer(line)]
        i += 1
        for end in ends:
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            i += 1
    return "\n".join(out)


def _resolve(p, cwd):
    p = os.path.expanduser(p.strip("'\""))
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.realpath(p)


def call_ops(call):
    """Ordered operations of one call: (kind, data). kind: wf | write | risky."""
    ops, uncertain = [], False
    cwd = call.get("cwd")
    for p in call["paths"]:
        ops.append(("write", {"path": p, "cwd": cwd}))
    for cmd in call["cmds"]:
        here = cwd
        segs = _segments(strip_heredocs(cmd))
        if segs is None:
            ops.append(("unparsed", {"cmd": cmd}))
            continue
        for seg in segs:
            toks = _tokens(seg)
            if toks and toks[0] == "cd" and len(toks) > 1:
                here = _resolve(toks[1], here)
                continue
            w = parse_wf(toks)
            if w == UNSUPPORTED:
                ops.append(("unparsed", {"cmd": seg.strip()}))
                continue
            if w:
                w["cwd"] = here
                ops.append(("wf", w))
                if not re.search(r"(?<![0-9<&>])>", seg):
                    continue
            found = False
            for m in re.finditer(r"(?<![0-9<&>])>>?\s*([^\s;&|<>]+)", seg):
                if m.group(1) not in ("/dev/null", "&1", "&2"):
                    ops.append(("write", {"path": m.group(1), "cwd": here}))
                    found = True
            if toks:
                head = os.path.basename(toks[0])
                args = [x for x in toks[1:] if not x.startswith("-")]
                targets = []
                if head == "tee":
                    targets = args
                elif head in ("cp", "mv") and len(args) >= 2:
                    targets = args[-1:] + (args[:-1] if head == "mv" else [])
                elif head in ("touch", "rm", "mkdir"):
                    targets = args
                for t in targets:
                    ops.append(("write", {"path": t, "cwd": here}))
                    found = True
                # a recognised redirect says where stdout went, not what the program wrote (WP-C-002)
                if not w and RISKY.search(seg):
                    paths = []
                    for x in toks[1:]:
                        x = x.split("=", 1)[1] if x.startswith("-") and "=" in x else x
                        if x and not x.startswith("-"):
                            paths.append(x)
                    ops.append(("risky", {"seg": seg.strip()[:200], "cwd": here, "args": paths,
                                          "cmd": cmd, "vars": "$" in seg}))
    return ops, uncertain


# -- audit -----------------------------------------------------------------------------
def gap_id(text):
    return "G-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def cleared_gaps(run_dir):
    notes = Path(run_dir) / "audit-notes.md"
    if not notes.is_file():
        return set()
    out, cur, body = set(), None, []
    for line in notes.read_text(encoding="utf-8").splitlines() + ["## END"]:
        m = re.match(r"^##\s+(G-[0-9a-f]{8})\b", line)
        if line.startswith("## "):
            if cur and "".join(body).strip():
                out.add(cur)
            cur, body = (m.group(1) if m else None), []
        else:
            body.append(line)
    return out


def _step_arg(data):
    return data["pos"][1] if len(data["pos"]) > 1 else None


class Audit:
    def __init__(self, run_dir, transcripts):
        self.st = RunState(run_dir)
        self.dir = Path(run_dir).resolve()
        self.rd = str(self.dir)
        self.wf = workflow.load(self.dir / workflow.SNAP)
        self.rp = replay(self.wf, self.st.events)
        self.violations = list(self.rp.violations)
        self.gaps = []
        self.stream = load_transcripts(transcripts)
        self.files = transcripts

    def in_run(self, p, cwd):
        ap = _resolve(p, cwd)
        return ap == self.rd or ap.startswith(self.rd + os.sep), ap

    def mentions_run(self, text):
        return self.rd in text or self.st.id in text

    # 1. operations with fractional positions inside their call
    def build_ops(self):
        self.ops = []          # (pos, kind, data, call, result)
        self.unparsed_wf = []      # (call time, result time) of unreadable commands naming wf/the run
        self.wf_calls = set()
        for it in self.stream.items:
            if it["kind"] != "call":
                continue
            ops, uncertain = call_ops(it)
            res = self.stream.results.get(it.get("id"))
            it["uncertain"] = uncertain
            for k, (kind, data) in enumerate(ops):
                self.ops.append((it["pos"] + (k + 1) / 1000.0, kind, data, it, res))
                if kind == "wf":
                    self.wf_calls.add(it.get("id"))
                if kind == "unparsed" and (re.search(r"\bdurweg\b", data["cmd"]) or self.mentions_run(data["cmd"])):
                    self.unparsed_wf.append((it["t"], (res or {}).get("t") or it["t"]))

    # 2. event evidence, both ways
    def match_events(self):
        events = {e["n"]: e for e in self.st.events}
        self.pos, ran_unshown, consumed = {}, set(), {}
        for pos, kind, data, call, res in self.ops:
            if kind != "wf" or data["sub"] not in CHANGING:
                continue
            used = consumed.setdefault(call["pos"], set())
            hit = None
            for m in (OK_LINE.finditer(res["text"]) if res else []):
                if m.start() in used or m.group(3) != data["sub"]:
                    continue
                if data["sub"] != "start" and m.group(4) != _step_arg(data):
                    continue
                hit = m
                used.add(m.start())
                break
            if hit is None:
                refused = res and re.search(rf"refused run={re.escape(self.st.id)}\b", res["text"])
                if self._targets_run(data) and not refused:
                    ran_unshown.add((data["sub"], _step_arg(data)))
                continue
            if hit.group(1) != self.st.id:
                continue
            n = int(hit.group(2))
            ev = events.get(n)
            where = f"transcript item {call['pos']}"
            if ev is None or ev["type"] != data["sub"] or ev.get("step") != (hit.group(4) or None) and data["sub"] != "start":
                self.violations.append(f"{where}: a successful `durweg {data['sub']}` reported event {n}, which the "
                                       f"log does not have in that form")
                continue
            if ev["type"] == "confirm" and (data["quote"] != ev.get("quote") or data["set"] != ev.get("set", {})):
                self.violations.append(f"{where}: confirm event {n} records a quote or --set different from "
                                       f"the command that was run")
                continue
            if data["sub"] != "start" and data["pos"] and "$" not in data["pos"][0] \
                    and not self.in_run(data["pos"][0], data["cwd"])[0]:
                self.violations.append(f"{where}: event {n} was reported for run {self.st.id}, but the "
                                       f"command named another run ({data['pos'][0]})")
                continue
            if n in self.pos:
                self.violations.append(f"{where}: event {n} reported twice")
                continue
            self.pos[n] = pos
        # ok lines in the output of calls that do not visibly run durweg are not evidence
        for it in self.stream.items:
            if it["kind"] == "result" and it.get("id") not in self.wf_calls and \
                    any(m.group(1) == self.st.id for m in OK_LINE.finditer(it["text"])):
                self.gaps.append(f"transcript item {it['pos']}: output shows `ok run={self.st.id}` lines but its "
                                 f"call does not visibly run durweg (cached output or a wrapper script)")
        slack = datetime.timedelta(seconds=1)
        for ev in self.st.events:
            if ev["type"] not in CHANGING or ev["n"] in self.pos:
                continue
            what = f"event {ev['n']} ({ev['type']} {ev.get('step', '')}, {ev.get('t')})"
            if (ev["type"], ev.get("step")) in ran_unshown:
                self.gaps.append(f"{what}: a matching durweg command ran but its success was not shown "
                                 f"(interrupted?); check the output of that call")
                continue
            t = _ts(ev.get("t"))
            if t and any(a and b and a - slack <= t <= b + slack for a, b in self.unparsed_wf):
                self.gaps.append(f"{what}: no readable durweg call; it may be inside a command audit cannot "
                                 f"read (listed as a gap)")
                continue
            t = _ts(ev.get("t"))
            covered = [f for f, a, b in self.stream.spans if a and b and t and a - slack <= t <= b + slack]
            if covered:
                self.violations.append(f"{what}: no successful durweg call in {covered[0]}, which covers that time")
            else:
                self.gaps.append(f"{what}: not covered by the given transcripts")
        seen = sorted(self.pos.items())
        for (n1, p1), (n2, p2) in zip(seen, seen[1:]):
            if p2 < p1:
                self.violations.append(f"events {n1} and {n2} appear in the transcripts in the opposite order")

    def _targets_run(self, data):
        if data["sub"] == "start" or not data["pos"]:
            return False
        if "$" in data["pos"][0]:
            return True       # a variable can name this run: cannot be ruled out, so a gap, not a violation
        return self.in_run(data["pos"][0], data["cwd"])[0] or self.mentions_run(data["pos"][0])

    # 3. confirmations
    def check_confirms(self):
        for ev in self.st.events:
            if ev["type"] != "confirm":
                continue
            p, c = self.pos.get(ev.get("proposal_event")), self.pos.get(ev["n"])
            if p is None or c is None:
                continue
            quote = " ".join(ev["quote"].split())
            heard = any(it["kind"] == "user" and p < it["pos"] < c and quote in " ".join(it["text"].split())
                        for it in self.stream.items)
            if not heard:
                self.violations.append(f"confirm event {ev['n']} ({ev['step']}): the quote “{ev['quote']}” is "
                                       f"not in a user message between propose event {ev['proposal_event']} "
                                       f"and the confirm")
            elif (self.rp.done.get(ev["step"]) or {}).get("confirm_event") == ev["n"]:
                extra = f"; --set {ev['set']} matches what the user meant" if ev.get("set") else ""
                self.gaps.append(f"confirm event {ev['n']} ({ev['step']}): check that the version shown after "
                                 f"propose event {ev['proposal_event']} is the proposal and that the user "
                                 f"answered this step{extra}")

    # 4. writes and risky commands
    def check_writes(self):
        self.rounds = self._rounds()
        lifetime = (min(self.pos.values()) if self.pos else 0, max(self.pos.values()) if self.pos else 0)
        for pos, kind, data, call, res in self.ops:
            if kind == "write":
                if res and res.get("error") and call["paths"]:
                    continue                      # the write tool failed: nothing was written
                inside, ap = self.in_run(data["path"], data["cwd"])
                if inside:
                    self._check_write(os.path.relpath(ap, self.rd), pos, call, certain=True)
            elif kind == "risky":
                self._risky(pos, data, call, lifetime)
            elif kind == "unparsed":
                cwd_in = bool(call.get("cwd")) and self.in_run(".", call.get("cwd"))[0]
                if self.mentions_run(data["cmd"]) or re.search(r"\bdurweg\b", data["cmd"]) or cwd_in \
                        or "steps/" in data["cmd"] or ".wf" in data["cmd"]:
                    self.gaps.append(f"command outside the shell subset audit reads (comments, $( ), "
                                     f"groups, loops, background, unbalanced quotes); check it by hand: "
                                     f"{data['cmd'].strip()[:200]}")

    def _risky(self, pos, data, call, lifetime):
        cwd_in = bool(data["cwd"]) and self.in_run(".", data["cwd"])[0]
        targets = [ap for inside, ap in (self.in_run(a, data["cwd"]) for a in data["args"]) if inside]
        if cwd_in:
            self.gaps.append(f"command run inside the run folder; it may write there: {data['seg']}")
        elif targets:
            for ap in targets:
                self._check_write(os.path.relpath(ap, self.rd), pos, call, certain=False)
        elif self.mentions_run(data["cmd"]) or "steps/" in data["cmd"] or ".wf" in data["cmd"]:
            self.gaps.append(f"unrecognized shell command that may write the run: {data['cmd'].strip()[:200]}")
        elif data["vars"] and lifetime[0] <= pos <= lifetime[1]:
            self.gaps.append(f"command with variables during the run; cannot tell what it writes: {data['seg']}")

    def _rounds(self):
        out = {s: [] for s in self.wf.order}
        open_, end = {}, float("inf")
        for ev in self.st.events:
            s, p = ev.get("step"), self.pos.get(ev["n"])
            if p is None:
                continue
            if ev["type"] in ("begin", "reopen"):
                if ev["type"] == "reopen":
                    for d in [s] + list(ev.get("reset", [])):
                        if d in open_:
                            r = open_.pop(d)
                            r["end"] = p
                            out[d].append(r)
                open_[s] = {"start": p, "end": end, "proposes": [], "done": None}
            elif ev["type"] == "propose" and s in open_:
                open_[s]["proposes"].append(p)
            elif ev["type"] == "done" and s in open_:
                r = open_.pop(s)
                r["end"], r["done"] = p, ev
                out[s].append(r)
        for s, r in open_.items():
            out[s].append(r)
        return out

    def _check_write(self, rel, wpos, call, certain):
        """certain: the write surely happened (tool or redirect); else a script was given the path."""
        found = []
        if rel.startswith(".wf" + os.sep) or rel == ".wf":
            found.append(f"{rel} written outside wf")
        elif any(rel == self.wf.decision_path(s) for s in self.wf.order):
            found.append(f"{rel}: the decision file was written outside `durweg confirm`")
        else:
            owned = False
            for sid in self.wf.order:
                if not any(fnmatch.fnmatch(rel, g) for g in self.wf.step(sid)["artifacts"]):
                    continue
                owned = True
                inside = [r for r in self.rounds[sid] if r["start"] < wpos < r["end"]]
                if not inside:
                    found.append(f"{rel} (step {sid}) written outside any active round of {sid}")
                elif inside[0]["done"] and self.wf.has_user(sid) and inside[0]["proposes"] \
                        and wpos > inside[0]["proposes"][-1]:
                    found.append(f"{rel} (step {sid}) changed after the proposal that the user confirmed")
            if not owned and not certain:
                self.gaps.append(f"a script was given {rel}, which belongs to no step; check what it wrote "
                                 f"(transcript item {call['pos']})")
        for f in found:
            msg = f"{f} (transcript item {call['pos']})"
            if certain and not call.get("uncertain"):
                self.violations.append(msg)
            else:
                why = "the order inside this call is uncertain" if certain else "a script was given this path"
                self.gaps.append(f"{msg}; {why}, check whether it really wrote")

    def run(self):
        self.build_ops()
        self.match_events()
        self.check_confirms()
        self.check_writes()
        return self.report()

    def report(self):
        gaps = list(dict.fromkeys(self.gaps))
        cleared = cleared_gaps(self.dir)
        if self.violations:
            result, rc = "VIOLATION", 1
        elif gaps:
            result, rc = "INSUFFICIENT", 2
        else:
            result, rc = "PASS", 0
        print(f"{result} run={self.st.id} ({len(self.stream.items)} transcript items from "
              f"{len(self.files)} file(s))")
        for v in self.violations:
            print("  violation: " + v)
        for g in gaps:
            gid = gap_id(g)
            print(f"  {'cleared' if gid in cleared else 'gap'} {gid}: {g}")
        if gaps and not self.violations:
            left = [g for g in gaps if gap_id(g) not in cleared]
            print(f"  {len(gaps) - len(left)} of {len(gaps)} gap(s) cleared in audit-notes.md"
                  + ("" if left else " - all gaps checked by a person"))
        return rc


def audit(run_dir, transcripts):
    return Audit(run_dir, transcripts).run()
