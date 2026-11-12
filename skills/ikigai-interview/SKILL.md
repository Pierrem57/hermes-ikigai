---
name: ikigai-interview
description: Use when running the Ikigai getting-to-know-you interview or a check-in. One question per turn, exact quotes, no inference stored as fact.
---

# Ikigai interview

Rules for the agent while the interview is open (`/ikigai start`, `/ikigai resume`, a check-in):

1. **One question per turn.** Ask the question returned by `ikigai_next`, in the user's language, with its options as choices when it has any (use `clarify` buttons when available).
2. **Save every answer with `ikigai_save`**: `value` is the answer, `quote` is the user's exact words. Never paraphrase into `quote`.
3. **Never save a guess as fact.** If you infer something, save it with `source="inferred"`; it is held back from USER.md and SOUL.md until the user confirms it.
4. **Skipping is always fine.** Save with `skipped=true`. Do not ask again in the same interview.
5. **At most one short acknowledgement** between questions. No advice, no analysis of the answers during the interview.
6. **Sensitive material** (health, finances, family, minors, third parties): store it if asked, but it stays local. Say so once.
7. At the end, call `ikigai_compile`. Show the proposed USER.md lines and the diff. Apply them with the built-in `memory` tool (target `user`, one batch) only after the user agrees. Point to the SOUL.md draft; never write SOUL.md yourself.
8. If the user corrects you on the same thing repeatedly, offer to make it a lasting rule (see `ikigai_rule_done`).
9. **Full or untidy USER.md.** When the user asks to refine, condense or improve it (`/ikigai refine`), call `ikigai_refine`, not `ikigai_compile`. Rewrite the whole file as one tighter set of entries from `current_entries` and `facts`: never invent, never drop a fact silently, stay within `limit`. Show before/after and the character count, apply in one `memory` batch only after the user agrees.
