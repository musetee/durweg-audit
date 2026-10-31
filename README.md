[English](README.md) · [中文](README_cn.md)

# durweg-audit

**An optional plugin for [Durweg](https://github.com/musetee/durweg) that checks a run against the agent's own session files: did every recorded step really happen as a visible `durweg` call, and did you really say the words recorded at each confirmation?**

`durweg check` checks a run's own record. durweg-audit goes one step further and compares that
record with what the agent (Claude Code or Codex) actually did in its session. Use it when a run
matters and you want to be sure the agent did not skip, fake or rewrite anything.

## 🧭 Install

You need Durweg installed, and git.

1. **Open a terminal** (as in step 1 of Durweg's "Every day").
2. **Get it:** `git clone https://github.com/musetee/durweg-audit.git ~/projects/durweg-audit`

## 📅 Use it

1. **Find the agent's session files for the run:**

   | Agent | Where its session files are |
   | :--- | :--- |
   | Claude Code | `~/.claude/projects/<the project folder, with every / written as ->/` (one `.jsonl` file per session) |
   | Codex | `~/.codex/sessions/<year>/<month>/<day>/` (one `rollout-….jsonl` file per session) |

2. **Run the audit:**
   `python3 ~/projects/durweg-audit/bin/durweg-audit <run folder> --transcript <session file> [more session files]`

Done looks like this: the first line is the result.

| Result | Exit code | What it means |
| :--- | :---: | :--- |
| `PASS` | 0 | Every step and every confirmation is backed by the session |
| `VIOLATION` | 1 | Something in the record is contradicted by the session (each one is listed) |
| `INSUFFICIENT` | 2 | • Some things cannot be decided from the session alone (listed as `gap G-…`)<br>• Look at each one and write what you checked under `## G-…` in `<run>/audit-notes.md`; audit then counts it as checked |
| setup problem | 4 | Durweg or a file was not found |

It reads only a defined subset of shell commands; anything else is never counted as evidence, only
listed as a gap.

## 🙋 I want to …

| I want to | How |
| :--- | :--- |
| Use it with a Durweg that is not installed (a checkout) | Set `DURWEG_PLATFORM` to that folder; otherwise audit finds Durweg through `~/Durweg/.install.json` |
| Run its tests | `python3 -m unittest discover -s tests` (with Durweg installed, or `DURWEG_PLATFORM` set) |

Supports Durweg 1.x.

---

License: [MIT](LICENSE).
