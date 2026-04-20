# Session Memory Protocol

## Purpose

This document defines how Claude Code manages persistent memory across sessions
in this repository. Paste the relevant sections into any repo's `CLAUDE.md` to
replicate the approach.

---

## On Session START — Load Context

1. Read `MEMORY.md` (the index). It lives at `.claude/memory/MEMORY.md`.
2. For any memory file whose description is relevant to this session's work,
   read it before writing a single line of code.
3. Treat memories as **point-in-time observations**, not live ground truth.
   Verify file paths, function names, and state claims against current code
   before asserting them as fact.

---

## During the Session — Capture Continuously

Save a memory whenever you learn something that future-you would need but
cannot derive from the code or git log alone. Four types:

| Type | Save when |
|---|---|
| `user` | You learn the user's role, expertise level, domain knowledge, or preferences |
| `feedback` | User corrects or confirms a non-obvious approach — record BOTH corrections AND validations |
| `project` | You learn who is doing what, why, or by when; bugs, decisions, constraints not in code |
| `reference` | You learn where to look for information in external systems |

**Do NOT save:** code patterns, file structure, git history, debugging recipes,
or anything already in CLAUDE.md. These can be re-derived.

### Required structure for feedback memories

```
Lead line: the rule itself

**Why:** the user's stated reason or the incident that prompted it
**How to apply:** when this guidance kicks in
```

### Required structure for project memories

```
Lead line: the fact or decision

**Why:** motivation, constraint, or stakeholder requirement
**How to apply:** how this shapes future suggestions
```

Convert all relative dates to absolute dates at save time
(e.g. "next Thursday" → "2026-04-24").

---

## How to Write a Memory File

```markdown
---
name: <short identifier>
description: <one-line description specific enough to judge relevance at load time>
type: <user | feedback | project | reference>
---

<content — lead with the rule or fact, then Why: and How to apply: for feedback/project>
```

Save to `.claude/memory/<topic>.md`.

Update the index at `.claude/memory/MEMORY.md`:
- One line per entry, ≤ 150 characters
- Format: `- [Title](file.md) — one-line hook`
- No frontmatter in MEMORY.md
- MEMORY.md is truncated after 200 lines — keep it lean

---

## On Session END — Synthesize

Before closing, do the following:

1. Review what changed this session. For any memory that is now stale or wrong,
   update or delete it — don't leave the index to rot.
2. If you opened a research question, added a bug, or changed strategy/module
   state, update the relevant memory file.
3. If the session produced a significant finding, decision, or blocker, append
   a dated entry to the project's session log (e.g. `docs/research/session_log.md`).

---

## Anti-Patterns to Avoid

- Never write memory about what the code *does* — read the code for that.
- Never duplicate information already in CLAUDE.md.
- Never create a memory for ephemeral session state (current task, in-progress work).
- Never save a file path or function name without first verifying it exists now.
- MEMORY.md is an index, not a dump — no memory content inline, ever.

---

## Gap Analysis vs. Ad-hoc Memory

| Gap in ad-hoc approach | What this protocol fixes |
|---|---|
| Memories written only when convenient | Explicit start/end protocol — load before coding, synthesize before closing |
| No staleness discipline | Verify before asserting; update stale entries at session end |
| Feedback memories lack context | Mandates Why + How to apply on every feedback/project entry |
| Index drifts from files | End-of-session review step catches drift |
| Relative dates decay silently | Hard rule: convert to absolute dates at save time |
| Validations never recorded | Feedback type covers confirmations, not just corrections |
