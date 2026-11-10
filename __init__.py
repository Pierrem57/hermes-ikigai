"""Ikigai: onboarding interview and periodic check-ins that build a sourced user profile.

Public plugin API only: ``register_tool``, ``register_command``, ``register_hook``,
``register_skill``. Everything is stored under ``$HERMES_HOME/plugin-data/ikigai`` and nothing is
sent anywhere. The plugin never writes USER.md or SOUL.md itself: it proposes, the agent applies
USER.md lines through the built-in ``memory`` tool (so ``memory.write_approval`` still applies), and
SOUL.md stays a draft file the user copies by hand.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from . import core
except ImportError:  # imported outside the Hermes loader (unit tests)
    import core  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

_ctx: Any = None


def _store() -> core.Store:
    from hermes_constants import get_hermes_home
    return core.Store(Path(get_hermes_home()) / "plugin-data" / "ikigai")


def _user_md() -> str:
    from hermes_constants import get_hermes_home
    p = Path(get_hermes_home()) / "memories" / "USER.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _ok(**kw: Any) -> str:
    return json.dumps({"success": True, **kw}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False)


# ------------------------------------------------------------------ tools

def _t_next(args: dict, **_: Any) -> str:
    st = _store()
    q = core.next_question(st, module=args.get("module") or None, lang=args.get("lang") or None)
    if q is None:
        return _ok(done=True, hint="Nothing left in this path. Call ikigai_compile.")
    return _ok(question=q)


def _t_save(args: dict, **_: Any) -> str:
    try:
        rec = _store().save(str(args.get("id", "")), str(args.get("value", "")), str(args.get("quote", "")),
                            args.get("source", "declared"), bool(args.get("skipped", False)))
    except (KeyError, ValueError) as exc:
        return _err(str(exc))
    return _ok(saved=args.get("id"), source=rec["source"], skipped=rec["skipped"])


def _t_status(args: dict, **_: Any) -> str:
    return _ok(**core.status(_store()))


def _t_compile(args: dict, **_: Any) -> str:
    st = _store()
    cur = _user_md()
    user = core.compile_user(st, args.get("lang") or None, current=cur)
    files = core.write_compiled(st, args.get("lang") or None)
    return _ok(user_md_lines=user["lines"], kept_entries=user["kept_entries"], operations=user["operations"],
               chars=user["chars"], limit=user["limit"], separator=user["separator"],
               dropped_for_budget=user["dropped"], diff=core.user_diff(cur, user["merged"]), files=files,
               apply="This is a MERGE: entries Ikigai does not own stay untouched. Show the diff to the user. "
                     "If they agree, apply exactly `operations` in ONE call to the memory tool "
                     "(target='user'); with no operations, USER.md is already up to date. "
                     "Do not write SOUL.md: point to the draft file instead.")


def _t_snooze(args: dict, **_: Any) -> str:
    import time
    days = max(1, min(90, int(args.get("days", 14))))
    _store().set_meta(checkin_declined_until=time.time() + days * 86400)
    return _ok(snoozed_days=days)


def _t_rule_done(args: dict, **_: Any) -> str:
    st = _store()
    r = st.meta().get("rule_pending")
    if not r:
        return _err("no rule proposal is pending")
    core.clear_rule(st, r["theme"], bool(args.get("accepted", True)))
    return _ok(theme=r["theme"], accepted=bool(args.get("accepted", True)))


def _schema(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []}}


# ------------------------------------------------------------------ slash command

HELP = {
    "en": ("/ikigai start [fr|en] — begin the 10-minute express interview\n"
           "/ikigai resume — continue where you stopped\n"
           "/ikigai more [module] — go deeper (modules: %s)\n"
           "/ikigai status — progress and stale answers\n"
           "/ikigai compile — propose USER.md, SOUL.md draft and sheets\n"
           "/ikigai pause | off | on — stop asking, disable nudges, re-enable\n"
           "/ikigai buttons on|off|auto — answer buttons (auto: desktop app only)\n"
           "/ikigai forget <id> — delete one answer (it is asked again on resume) · "
           "/ikigai erase confirm — delete everything"),
    "fr": ("/ikigai start [fr|en] — lance l'entretien express de 10 minutes\n"
           "/ikigai resume — reprend là où tu t'es arrêté\n"
           "/ikigai more [module] — approfondir (modules : %s)\n"
           "/ikigai status — avancement et réponses périmées\n"
           "/ikigai compile — propose USER.md, brouillon de SOUL.md et fiches\n"
           "/ikigai pause | off | on — arrêter les questions, couper les rappels, les réactiver\n"
           "/ikigai buttons on|off|auto — boutons de réponse (auto : application de bureau seulement)\n"
           "/ikigai forget <id> — supprime une réponse (reposée au prochain resume) · "
           "/ikigai erase confirm — supprime tout"),
}


def _open(st: core.Store, q: dict | None, lg: str, who: dict, intro: str = "", done: str = "") -> str:
    """Start answering in the calling session. Slash output is shown as plain text, so where buttons
    exist the question itself is shown by the next message (any text), as a normal reply."""
    core.set_active(st, bool(q))
    if not q:
        st.set_meta(pending_show=False)
        return done
    core.bind_owner(st, who.get("session_id", ""), who.get("session_key", ""), who.get("platform", ""))
    if core.buttons_enabled(st):
        st.set_meta(pending_show=True)
        return intro + ("Envoie « go » (ou n'importe quel message) pour afficher la question avec ses boutons."
                        if lg == "fr" else
                        "Send \"go\" (or any message) to show the question with its buttons.")
    st.set_meta(pending_show=False)
    return intro + core.format_question(q, lg == "fr")


def _cmd(raw: str, session_id: str = "", session_key: str = "", platform: str = "", **_: Any) -> str:
    st = _store()
    who = {"session_id": session_id or "", "session_key": session_key or "", "platform": platform or ""}
    if platform and st.meta().get("platform") != platform:
        st.set_meta(platform=platform)
    parts = (raw or "").split()
    sub = parts[0].lower() if parts else "help"
    rest = parts[1:]
    lg = st.meta().get("lang", "en")
    mods = ", ".join(m["id"] for m in core.modules(lg) if m["id"] != "m0")
    if sub in ("help", "?"):
        return HELP[lg] % mods
    if sub == "start":
        if rest and rest[0][:2].lower() in ("fr", "en"):
            lg = "fr" if rest[0].lower().startswith("fr") else "en"
        st.set_meta(lang=lg, started=st.meta().get("started") or core._now(), full=False, module=None)
        intro = ("Entretien express : ~12 questions, tout est stocké en local, rien n'entre dans USER.md "
                 "sans ton accord. Une phrase suffit par réponse.\n\n" if lg == "fr" else
                 "Express interview: ~12 questions, everything stays local, nothing reaches USER.md "
                 "without your approval. One sentence per answer is enough.\n\n")
        return _open(st, core.next_in_scope(st, lg), lg, who, intro,
                     "Déjà complet. /ikigai more <module> pour approfondir, /ikigai compile pour compiler."
                     if lg == "fr" else "Already complete. /ikigai more <module> to go deeper, /ikigai compile.")
    if sub == "resume":
        return _open(st, core.next_in_scope(st, lg), lg, who, "",
                     "Terminé : /ikigai compile" if lg == "fr" else "Done: /ikigai compile")
    if sub == "more":
        mod = rest[0].lower() if rest else None
        if mod and mod not in {m["id"] for m in core.modules()}:
            return ("Module inconnu. Modules : " if lg == "fr" else "Unknown module. Modules: ") + mods
        st.set_meta(full=True, module=mod)
        return _open(st, core.next_in_scope(st, lg), lg, who, "",
                     "Module terminé." if lg == "fr" else "Module complete.")
    if sub == "status":
        s = core.status(st)
        rows = "\n".join(f"  {m['module']:<9} {m['answered']}/{m['total']}"
                         + (f" (+{m['skipped']} skip)" if m["skipped"] else "") for m in s["modules"])
        stale = f"\nstale >90d: {', '.join(s['stale'])}" if s["stale"] else ""
        return f"{s['answered']}/{s['total']} answered, {s['skipped']} skipped\n{rows}{stale}"
    if sub == "compile":
        r = json.loads(_t_compile({"lang": lg}))
        if not r["user_md_lines"]:
            return "Rien à compiler pour l'instant." if lg == "fr" else "Nothing to compile yet."
        lines = "\n".join(r["user_md_lines"])
        kept, ops = len(r["kept_entries"]), r["operations"]
        adds = sum(1 for o in ops if o["action"] == "add")
        rems = len(ops) - adds
        if lg == "fr":
            change = f"{adds} ligne(s) à ajouter, {rems} à retirer" if ops else "USER.md est déjà à jour"
            return (f"Lignes Ikigai pour USER.md :\n\n{lines}\n\n"
                    f"Fusion : {kept} ligne(s) existante(s) conservée(s) telles quelles ; {change}. "
                    f"Total après fusion : {r['chars']}/{r['limit']} caractères.\n"
                    f"Fiches : {r['files']['profile_dir']}\n"
                    f"Brouillon SOUL.md : {r['files']['soul_draft'] or '(rien)'}"
                    + ("\n\nDis « applique le profil Ikigai » et je mets à jour USER.md (avec ton approbation)."
                       if ops else ""))
        change = f"{adds} line(s) to add, {rems} to remove" if ops else "USER.md is already up to date"
        return (f"Ikigai lines for USER.md:\n\n{lines}\n\n"
                f"Merge: {kept} existing line(s) kept as they are; {change}. "
                f"Total after merge: {r['chars']}/{r['limit']} chars.\n"
                f"Sheets: {r['files']['profile_dir']}\nSOUL.md draft: {r['files']['soul_draft'] or '(none)'}"
                + ("\n\nSay \"apply the Ikigai profile\" and I will update USER.md (with your approval)."
                   if ops else ""))
    if sub == "buttons":
        v = (rest[0].lower() if rest else "")
        if v in ("on", "off", "auto"):
            st.set_meta(buttons={"on": True, "off": False, "auto": None}[v])
        return f"buttons: {st.meta().get('buttons', 'auto')} (on|off|auto)"
    if sub == "pause":
        st.set_meta(awaiting=None, active=False, pending_show=False)
        return "OK. /ikigai resume" if lg == "en" else "D'accord. /ikigai resume"
    if sub in ("off", "on"):
        st.set_meta(off=(sub == "off"), **({"active": False, "awaiting": None, "pending_show": False} if sub == "off" else {}))
        return f"Rappels {'coupés' if sub == 'off' else 'réactivés'}." if lg == "fr" else f"Nudges {sub}."
    if sub == "forget" and rest:
        return ("Supprimée." if lg == "fr" else "Deleted.") if st.forget(rest[0]) else "?"
    if sub == "erase":
        if rest[:1] == ["confirm"]:
            st.erase_all()
            return "Tout est effacé." if lg == "fr" else "Everything erased."
        return "Ajoute « confirm »." if lg == "fr" else "Add 'confirm'."
    return HELP[lg] % mods


# ------------------------------------------------------------------ hooks

def _pre_llm_call(session_id: str = "", user_message: Any = "", is_first_turn: bool = False,
                  platform: str = "", parent_session_id: str = "", **_: Any) -> Any:
    try:
        if platform in core.NON_INTERACTIVE:      # cron jobs and subagents are nobody's interview
            return None
        st = _store()
        text = user_message if isinstance(user_message, str) else ""
        if platform and st.meta().get("platform") != platform:
            st.set_meta(platform=platform)
        if text and not text.lstrip().startswith("/"):
            turn = core.interview_turn(st, text, session_id=session_id or "",
                                       parent_session_id=parent_session_id or "", platform=platform or "")
            if turn is not None:
                return {"action": "block", "message": turn["message"]}
        if st.meta().get("active"):               # another chat while an interview runs: stay out of it
            return None
        parts = [p for p in (core.awaiting_context(st, text), core.nudge(st, bool(is_first_turn))) if p]
        return {"context": "\n\n".join(parts)} if parts else None
    except Exception:  # never break a turn
        logger.exception("ikigai pre_llm_call failed")
        return None


def _post_llm_call(session_id: str = "", user_message: Any = "", platform: str = "", **_: Any) -> None:
    try:
        if platform in core.NON_INTERACTIVE or not isinstance(user_message, str):
            return
        theme = core.detect_correction(user_message)
        if theme:
            core.note_correction(_store(), theme, user_message)
    except Exception:
        logger.exception("ikigai post_llm_call failed")


# ------------------------------------------------------------------ registration

def register(ctx: Any) -> None:
    global _ctx
    _ctx = ctx
    ts = "ikigai"
    ctx.register_tool("ikigai_next", ts, _schema(
        "ikigai_next", "Get the next unanswered Ikigai interview question (express path by default). "
        "Marks it as the open question.", {
            "module": {"type": "string", "description": "Optional module id to go deeper (e.g. 'style')."},
            "lang": {"type": "string", "enum": ["fr", "en"]}}), _t_next,
        description="Next Ikigai question")
    ctx.register_tool("ikigai_save", ts, _schema(
        "ikigai_save", "Save the user's answer to an Ikigai question. `quote` must be their exact words. "
        "Use source='inferred' for guesses (held back from USER.md/SOUL.md until confirmed), "
        "'corrected' when the user corrected you, skipped=true when they pass.", {
            "id": {"type": "string"}, "value": {"type": "string"}, "quote": {"type": "string"},
            "source": {"type": "string", "enum": ["declared", "inferred", "corrected"]},
            "skipped": {"type": "boolean"}}, ["id", "value"]), _t_save, description="Save an answer")
    ctx.register_tool("ikigai_status", ts, _schema(
        "ikigai_status", "Interview progress per module, skipped and stale (>90 days) answers.", {}),
        _t_status, description="Ikigai progress")
    ctx.register_tool("ikigai_compile", ts, _schema(
        "ikigai_compile", "Compile answers into a USER.md proposal (<=1375 chars, with diff against the "
        "current file), a SOUL.md draft and detailed sheets. Writes only inside the plugin's own data "
        "directory. Apply USER.md lines yourself with the memory tool, after the user agrees.",
        {"lang": {"type": "string", "enum": ["fr", "en"]}}), _t_compile, description="Compile profile")
    ctx.register_tool("ikigai_snooze", ts, _schema(
        "ikigai_snooze", "The user declined the weekly check-in: do not offer it again for N days.",
        {"days": {"type": "integer", "minimum": 1, "maximum": 90}}), _t_snooze, description="Snooze check-in")
    ctx.register_tool("ikigai_rule_done", ts, _schema(
        "ikigai_rule_done", "Close a pending 'repeated correction' proposal once the user has answered.",
        {"accepted": {"type": "boolean"}}), _t_rule_done, description="Close rule proposal")
    ctx.register_command("ikigai", _cmd, description="Getting-to-know-you interview and profile",
                         args_hint="start|resume|more|status|compile|buttons|pause|off|on|forget|erase")
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_llm_call", _post_llm_call)
    skill = Path(__file__).parent / "skills" / "ikigai-interview" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("ikigai-interview", skill, "Run the Ikigai interview: one question per turn, exact quotes.")
