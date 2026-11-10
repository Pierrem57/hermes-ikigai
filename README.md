# Ikigai: get Hermes to actually know you

Hermes keeps two small files about you: `USER.md` (1,375 characters, injected on every turn) and `SOUL.md` (the agent's voice and rules). Most people fill them in by accident, a line at a time, or never.

**Ikigai is a structured getting-to-know-you interview, plus light periodic check-ins, that builds a sourced picture of you and compiles it into those files without blowing their budget.**

```
/ikigai start        # 12 questions, about 10 minutes, in English or French
/ikigai compile      # proposes USER.md (fits 1,375 chars), a SOUL.md draft, detailed sheets
```

## What it does

- **76 questions in 11 modules.** An express path first (12 questions: how to address you, what you do, how long answers should be, what I may do without asking, your one red line). Then deeper modules on demand: the four ikigai circles, work, communication style, autonomy and guardrails, decisions, rhythms, goals, values, frictions.
- **Your own words, with a source.** Every answer is stored with the exact quote, a date and a source tag: `declared`, `inferred` or `corrected`. Anything the agent merely *guessed* (`inferred`) is held back from `USER.md` and `SOUL.md` until you confirm it.
- **Compiles short, details on demand.** `USER.md` gets one themed line per topic (at most 120 characters, whole file at most 1,375). When the budget is tight, the lowest-priority facts drop first and behaviour rules survive. The detail goes to sheets in `plugin-data/ikigai/profile/`, which are never injected into the prompt.
- **Never overwrites your files.** `USER.md` lines are applied by the agent through Hermes's built-in `memory` tool, so `memory.write_approval` still applies and you see a diff first. `SOUL.md` is only ever a *draft file* that you copy by hand.
- **Deterministic interview.** While an interview is active, the plugin itself takes your reply (a number, an option, or free text), saves it and shows the next question, with no model call, so it cannot be derailed and costs no tokens. `skip` passes, `pause` stops. Any message starting with `/` goes through normally.
- **Check-ins that earn their place.** One offer on your first session (never again if you ignore it), then a 3-question weekly check-in only if you started the interview, snoozable. If you correct the agent on the same theme three times (answer too long, stop asking, tone...), it offers once to turn that into a lasting rule.
- **Sensitive by design.** Questions tagged local-only (for example "which data must never leave my machine") are stored locally and excluded from `USER.md`, `SOUL.md` and the sheets, because those files are sent to your model provider. Choose the scope in the first question ("everything / work only / working preferences only").
- **Local only.** No network call, no telemetry, no dependency. Files are `0600` in a `0700` directory under `$HERMES_HOME/plugin-data/ikigai/`.

## Commands

| Command | Effect |
|---|---|
| `/ikigai start [fr or en]` | Start the express interview |
| `/ikigai resume` | Continue where you stopped |
| `/ikigai more [module]` | Go deeper (`ikigai`, `work`, `style`, `autonomy`, `decision`, `rhythm`, `goals`, `values`, `friction`) |
| `/ikigai status` | Progress per module, answers older than 90 days |
| `/ikigai compile` | Proposed `USER.md` lines, `SOUL.md` draft, sheets |
| `/ikigai pause`, `off`, `on` | Stop asking, disable nudges, re-enable |
| `/ikigai forget <id>` | Delete one answer |
| `/ikigai erase confirm` | Delete everything the plugin stored |

## How it plugs into Hermes

Public APIs only: `register_command`, six `register_tool`s (`ikigai_next`, `ikigai_save`, `ikigai_status`, `ikigai_compile`, `ikigai_snooze`, `ikigai_rule_done`), `pre_llm_call` (runs the interview turn, and injects short context for offers and check-ins) and `post_llm_call` (correction counting), and a bundled read-only skill, `ikigai:ikigai-interview`, with the interviewing rules. While a question is open, the plugin tells the agent how to treat your next message, so you simply answer in the chat.

No capability grants are requested: it does not override tools, switch models or providers, or act on chat platforms.

## What it reads and writes

- Reads: your `USER.md` (only to show a diff) and the messages of the current turn (to detect corrections, locally).
- Writes: only inside `$HERMES_HOME/plugin-data/ikigai/` (`answers.json`, `meta.json`, `profile/`, `draft/SOUL.draft.md`, `history/`).
- Sends: nothing.

## Install

```
hermes plugins install Pierrem57/hermes-ikigai
hermes plugins enable ikigai
```

Then restart the gateway so it loads the plugin.

## Tests

```
python -m pytest tests
```

The tests cover the questionnaire's consistency, the budget and priority rules, the local-only and inferred filters, red lines never cut mid-sentence, the once-only offer, the weekly check-in, snooze, correction detection and erase.

## Known limits

- Correction detection is keyword-based (English and French), so it misses subtle corrections. It is a nudge, not a judge.
- Interview quality depends on the model following the bundled skill. The plugin keeps state (open question, saved answers), so a model that drifts does not lose progress.
- Questions are in English and French only.
- `USER.md` is read as a frozen snapshot at session start, so an updated file shows up from the next session.
- With many MCP servers, Hermes may defer plugin tools behind `tool_search`; the interview still works, with one extra lookup.

## License

MIT
