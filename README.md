# durweg-audit

Optional plugin for [Durweg](https://github.com/musetee/durweg): compares a Durweg run with the
agent's own session files (Claude Code, Codex) and says whether every recorded step really happened
as a visible `durweg` call, and whether the user's confirmations appear in the conversation.

    durweg-audit <run folder> --transcript <session files...>

Results: `PASS` (0), `VIOLATION` (1), `INSUFFICIENT` (2: a person has to look at the listed gaps;
write what you checked under `## G-xxxxxxxx` in `<run>/audit-notes.md`), setup problem (4).
It reads only a defined subset of shell commands; anything else is never counted as evidence.

It finds the platform through `~/Durweg/.install.json` (written by Durweg's installer), or
`$DURWEG_PLATFORM`. Supports Durweg 1.x. Tests: `python -m unittest discover -s tests`
(with a Durweg checkout next to this repository, or `DURWEG_PLATFORM` set).
