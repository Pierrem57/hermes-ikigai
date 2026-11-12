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
- **Full `USER.md`? Refine, explicitly.** `compile` never edits entries it does not own, so a file already at the limit gets no Ikigai lines (and says so). `/ikigai refine` is the opt-in alternative: the agent rewrites the whole file as one tighter set of entries from your current content and your answers, shows before/after, and applies it through the `memory` tool only if you agree.
- **Merges, never overwrites.** `/ikigai compile` reads your current `USER.md`, keeps every entry it does not own exactly as it is, counts those against the 1,375 characters first, and proposes only the minimal change: its own stale lines out, its new lines in. The agent applies it through Hermes's built-in `memory` tool, so `memory.write_approval` still applies and you see a diff first. Running it twice changes nothing. `SOUL.md` is only ever a *draft file* that you copy by hand.
- **Deterministic interview.** While an interview is active, the plugin itself takes your reply (a number, an option, or free text), saves it and shows the next question, with no model call, so it cannot be derailed and costs no tokens. `skip` passes, `pause` stops. Any message starting with `/` goes through normally. The interview belongs to the chat that started it: other chats, cron jobs and subagents are never touched.
- **Answer buttons in the desktop app.** Every question comes with clickable buttons (options, Confirm, Skip, Pause), rendered in the app's own theme. Questions that allow several answers ("which topics are off limits for memory?") have tick boxes; by text, `1,3` or `health and family` works too. A button of an old question can never answer the current one. Everywhere else (Telegram, CLI) the same questions are numbered lists.
- **Check-ins that earn their place.** One offer on your first session (never again if you ignore it), then a 3-question weekly check-in only if you started the interview, snoozable. If you correct the agent on the same theme three times (answer too long, stop asking, tone...), it offers once to turn that into a lasting rule.
- **Sensitive by design.** Questions tagged local-only (for example "which data must never leave my machine") are stored locally and excluded from `USER.md`, `SOUL.md` and the sheets, because those files are sent to your model provider. The first questions set the frame: what may be remembered (everything / work only / working preferences only) and which topics are off limits for memory, which becomes a `Never memorize` rule the agent sees on every turn.
- **Local only.** No network call, no telemetry, no dependency. Files are `0600` in a `0700` directory under `$HERMES_HOME/plugin-data/ikigai/`.

## Commands

| Command | Effect |
|---|---|
| `/ikigai start [fr or en]` | Start the express interview (in the desktop app, send any message to show the first question with its buttons) |
| `/ikigai resume` | Continue where you stopped |
| `/ikigai more [module]` | Go deeper, one module at a time (`ikigai`, `work`, `style`, `autonomy`, `decision`, `rhythm`, `goals`, `values`, `friction`) |
| `/ikigai status` | Progress per module, answers older than 90 days |
| `/ikigai show [sheet, all or soul]` | Read back what Ikigai holds about you, in the chat: overview, one sheet (`work`, `ikigai`, `communication`, `autonomy`, `environment`, `goals`, `frictions`), everything, or the `SOUL.md` draft |
| `/ikigai compile` | `USER.md` lines merged with what is already there, `SOUL.md` draft, sheets |
| `/ikigai refine` | For a `USER.md` that is already full: the agent proposes a rewritten, denser version using your answers (before/after, character count); nothing is written without your approval |
| `/ikigai pause`, `off`, `on` | Stop asking, disable nudges, re-enable |
| `/ikigai buttons on`, `off`, `auto` | Answer buttons (auto: desktop app only) |
| `/ikigai forget <id>` | Delete one answer; it is asked again on the next `resume` |
| `/ikigai erase confirm` | Delete everything the plugin stored |

## How it plugs into Hermes

Public APIs only: `register_command`, seven `register_tool`s (`ikigai_next`, `ikigai_save`, `ikigai_status`, `ikigai_compile`, `ikigai_refine`, `ikigai_snooze`, `ikigai_rule_done`), `pre_llm_call` (runs the interview turn, and injects short context for offers and check-ins) and `post_llm_call` (correction counting), and a bundled read-only skill, `ikigai:ikigai-interview`, with the interviewing rules. While a question is open, the plugin tells the agent how to treat your next message, so you simply answer in the chat.

No capability grants are requested: it does not override tools, switch models or providers, or act on chat platforms.

## What it reads and writes

- Reads: your `USER.md` (to merge with it and show a diff) and the user message of the current turn (the interview reply, and local keyword matching to count corrections).
- Writes: only inside `$HERMES_HOME/plugin-data/ikigai/` (`answers.json`, `meta.json`, `profile/`, `draft/SOUL.draft.md`, `history/`, `widgets/`).
- Sends: nothing.

## Install

```
hermes plugins install Pierrem57/hermes-ikigai
hermes plugins enable ikigai
```

Needs Hermes 0.22.6 or newer: the interview relies on a `pre_llm_call` hook being able to answer a turn itself.

Then restart what serves your chats, so the hooks load: the gateway (`hermes gateway restart`) for messaging platforms, and the desktop app's backend for desktop chats (restart the app, or its `hermes dashboard` service when the backend runs on a server). Installing or updating without that restart leaves the old code answering.

## Tests

```
python -m pytest tests
```

The tests cover the questionnaire's consistency, the budget and priority rules, the local-only and inferred filters, red lines never cut mid-sentence, the once-only offer, the weekly check-in, snooze, correction detection and erase.

## Known limits

- Correction detection is keyword-based (English and French), so it misses subtle corrections. It is a nudge, not a judge.
- The interview itself never calls the model. The seven tools and the bundled skill are for agent-driven use (a check-in, saving something the agent inferred); that path depends on the model following the skill.
- Questions are in English and French only.
- Answer buttons need the desktop app. The app shows a slash command's output as plain text, and only a reply can carry a widget, so `/ikigai start`, `resume` and `more` answer "Ready: N questions. Send go": that one message (anything) brings the first question with its buttons.
- The 1,375-character limit is Hermes's default; a custom `user_char_limit` is not read.
- `USER.md` is read as a frozen snapshot at session start, so an updated file shows up from the next session.
- With many MCP servers, Hermes may defer plugin tools behind `tool_search`; the interview still works, with one extra lookup.

## License

MIT
