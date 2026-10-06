# Comments

The test, before every comment: **if this comment vanished, what concrete mistake would the
next reader make?** No answer, no comment. Code, names, types, tests and git history are the
primary documentation; a comment exists only for what none of those can carry.

1. **One home per fact.** Every explanation lives exactly once, at the narrowest scope where
   the mistake would happen — usually the declaration, sometimes the exact line. Call sites
   that merely comply with a rule do not cite it.
2. **Invariants, not chronicles.** Present tense only. What the code used to do, what was
   removed, and the measurements that justified a deleted thing belong in the commit that did
   it. Game facts (a property that only exists since some build) are data — kept once, where
   the value is defined.
3. **No advocacy, no pre-rebuttals.** State the constraint; never argue that the code is
   correct or anticipate a review objection. If a decision needs a paragraph of argument, the
   paragraph is the commit message.
4. **Never paraphrase the code.** No comments restating a type, a name, or what the next line
   visibly does.
5. **Module docstrings: at most four sentences.** What this is, the one thing someone will
   trip on, where to look next.
6. **Docstrings proportional to surface.** Public endpoints and MCP tools: contract level —
   what it answers, what null means. Private helpers: one line or none.
7. **Cross-file narration is a smell.** A rule shared by many files is written once (a short
   doc or the shared module) and pointed at, not re-told per file. The web wire rules live in
   [web-wire.md](web-wire.md).
8. **Tripwires stay** — one imperative line at the exact line they guard ("renaming this
   function churns the committed schema"). This is what comments are for.
9. **Budget, enforced as a ratchet.** `tests/test_comment_budget.py` fails when any file's
   prose:code ratio exceeds its layer's cap. The layers are the `src` packages, `tools/` and
   `tests/`, plus the frontend's hand-written TypeScript (`//` lines and every line of a
   `/* */` block count as prose; the generated schema types do not count). In Python, prose
   is `#` lines and strings that are statements of their own (docstrings); an `__all__`
   entry or a multi-line message is code. The caps are each tree's measured maximum plus a
   small working margin, re-measured in 2026-10 — they stop regrowth rather than assert an
   ideal. The density to aim at when writing is
   `interfaces/web/routers/layers/crates.py`, the reviewed example. Lowering a cap means
   sweeping the files it would fail, in that same commit.

Prose that survives the test is written in full sentences that say true things — the budget
changes how much is said, not how it is said.
