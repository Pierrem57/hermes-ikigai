"""Ikigai core: questionnaire flow, local storage and compilation.

Pure Python, no Hermes imports and no network access, so it can be unit-tested on its own.
Storage layout under ``root`` (a directory the caller owns, mode 0700):

    answers.json     every answer, with the user's own words, a source tag and a date
    meta.json        language, flow position, nudge bookkeeping, correction counters
    profile/*.md     detailed sheets compiled from the answers (never injected automatically)
    draft/SOUL.draft.md   a SOUL.md draft. The plugin never writes the real SOUL.md.
    history/<date>.json   snapshots, so two compilations can be compared
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

USER_LIMIT = 1375          # USER.md hard limit in Hermes
LINE_LIMIT = 120           # one theme per line, short
STALE_DAYS = 90
CHECKIN_DAYS = 7
NUDGE_COOLDOWN_H = 24
AWAIT_TTL_H = 12

SOURCES = ("declared", "inferred", "corrected")
_DATA = Path(__file__).with_name("data") / "questionnaire.json"

# ---------------------------------------------------------------- questionnaire

_Q: dict[str, Any] | None = None


def questionnaire() -> dict[str, Any]:
    global _Q
    if _Q is None:
        _Q = json.loads(_DATA.read_text(encoding="utf-8"))
    assert _Q is not None
    return _Q


def module_of(qid: str) -> str:
    for m in questionnaire()["modules"]:
        if qid in m["questions"]:
            return m["id"]
    raise KeyError(qid)


def _lang(lang: str) -> str:
    return "fr" if str(lang).lower().startswith("fr") else "en"


def render(qid: str, lang: str = "en") -> dict[str, Any]:
    q = questionnaire()["questions"][qid]
    lg = _lang(lang)
    out = {"id": qid, "type": q["type"], "text": q[lg], "module": module_of(qid)}
    if q.get("opts_" + lg):
        out["options"] = q["opts_" + lg]
    return out


def modules(lang: str = "en") -> list[dict[str, Any]]:
    lg = _lang(lang)
    return [{"id": m["id"], "title": m["title"][lg], "count": len(m["questions"]),
             "express": m["express"]} for m in questionnaire()["modules"]]


# ---------------------------------------------------------------- storage

def _now() -> float:
    return time.time()


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    # answers: {qid: {value, quote, source, ts, skipped}}
    def answers(self) -> dict[str, dict[str, Any]]:
        return _read(self.root / "answers.json", {})

    def meta(self) -> dict[str, Any]:
        return _read(self.root / "meta.json", {})

    def set_meta(self, **kw: Any) -> dict[str, Any]:
        m = self.meta()
        m.update(kw)
        _write(self.root / "meta.json", m)
        return m

    def save(self, qid: str, value: str, quote: str = "", source: str = "declared",
             skipped: bool = False) -> dict[str, Any]:
        if qid not in questionnaire()["questions"]:
            raise KeyError(f"unknown question id: {qid}")
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        if source == "declared" and not skipped and not (quote or value).strip():
            raise ValueError("a declared answer needs the user's own words")
        a = self.answers()
        a[qid] = {"value": value.strip(), "quote": (quote or value).strip(), "source": source,
                  "ts": _now(), "skipped": bool(skipped)}
        _write(self.root / "answers.json", a)
        m = self.meta()
        if m.get("awaiting") == qid:
            m["awaiting"] = None
        m.setdefault("started", _now())
        _write(self.root / "meta.json", m)
        return a[qid]

    def forget(self, qid: str) -> bool:
        a = self.answers()
        if qid not in a:
            return False
        del a[qid]
        _write(self.root / "answers.json", a)
        return True

    def erase_all(self) -> None:
        for p in sorted(self.root.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()


# ---------------------------------------------------------------- flow

def pending_ids(store: Store, module: str | None = None, express: bool = False) -> list[str]:
    """Question ids not answered or skipped yet, in questionnaire order."""
    done = store.answers()
    ids: list[str] = []
    for m in questionnaire()["modules"]:
        if module and m["id"] != module:
            continue
        if express and not m["express"] and m["id"] != "m0":
            continue
        ids += [q for q in m["questions"] if q not in done]
    return ids


def next_question(store: Store, module: str | None = None, express: bool | None = None,
                  lang: str | None = None, mark_awaiting: bool = True) -> dict[str, Any] | None:
    meta = store.meta()
    lg = lang or meta.get("lang", "en")
    if express is None:
        express = module is None and not meta.get("full", False)
    ids = pending_ids(store, module, express)
    if not ids:
        return None
    q = render(ids[0], lg)
    q["remaining"] = len(ids)
    if mark_awaiting:
        store.set_meta(awaiting=q["id"], awaiting_ts=_now(), lang=_lang(lg))
    return q


def status(store: Store) -> dict[str, Any]:
    a = store.answers()
    meta = store.meta()
    per = []
    for m in questionnaire()["modules"]:
        total = len(m["questions"])
        done = sum(1 for q in m["questions"] if q in a and not a[q].get("skipped"))
        skipped = sum(1 for q in m["questions"] if q in a and a[q].get("skipped"))
        per.append({"module": m["id"], "answered": done, "skipped": skipped, "total": total})
    stale = [q for q, v in a.items() if not v.get("skipped")
             and _now() - v["ts"] > STALE_DAYS * 86400]
    return {"started": bool(meta.get("started")), "lang": meta.get("lang", "en"),
            "answered": sum(p["answered"] for p in per), "skipped": sum(p["skipped"] for p in per),
            "total": len(questionnaire()["questions"]), "modules": per, "stale": sorted(stale),
            "awaiting": meta.get("awaiting"), "last_checkin": meta.get("last_checkin")}


# ---------------------------------------------------------------- compilation

# Short labels for the lines that may reach USER.md (French and English).
_LABELS = {
    "1.1": ("Appel", "Call"), "1.2": ("Rôle", "Role"), "1.3": ("Objectif 3 mois", "3-month goal"),
    "1.4": ("Longueur", "Length"), "1.5": ("Décision", "Decisions"), "1.6": ("Sans demander", "Without asking"),
    "1.11": ("Horaires", "Hours"), "1.12": ("Ligne rouge", "Red line"),
    "2.8": ("Succès à 3 ans", "3-year success"), "3.2": ("Tâches lourdes", "Heavy tasks"),
    "3.5": ("Outils", "Tools"), "3.7": ("Équipe", "Team"), "4.7": ("Commandes mobile", "Mobile commands"),
    "5.1": ("Autonomie réversible", "Reversible autonomy"), "5.2": ("Autonomie irréversible", "Irreversible autonomy"),
    "5.3": ("Envois à des tiers", "Third-party sends"), "5.4": ("Dépenses", "Spending"),
    "6.2": ("Critères", "Criteria"), "7.2": ("Notifications", "Notifications"),
    "7.3": ("Appareils", "Devices"), "7.4": ("Résumé", "Digest"), "8.1": ("Priorités", "Priorities"),
    "8.4": ("Temps de cadrage", "Review time"), "10.3": ("Mes questions", "My questions"),
}
# (theme, [(qid, priority)]); priority 1 is kept longest when the budget is tight.
_USER_THEMES = [
    ("Identité" , "Identity", [("1.1", 1), ("1.2", 2), ("3.7", 4)]),
    ("Règles", "Rules", [("1.12", 1), ("1.6", 1), ("5.1", 1), ("5.2", 1), ("5.3", 1), ("5.4", 2),
                         ("1.5", 2), ("1.4", 2), ("10.3", 3)]),
    ("Format", "Format", [("4.7", 2), ("7.2", 3), ("7.4", 4)]),
    ("Contexte", "Context", [("1.11", 3), ("3.2", 3), ("3.5", 4), ("7.3", 4)]),
    ("Objectifs", "Goals", [("1.3", 2), ("8.1", 2), ("2.8", 4), ("6.2", 4), ("8.4", 4)]),
]
_SHEET = {  # module -> sheet file
    "m0": "autonomie", "express": None, "ikigai": "ikigai", "work": "travail", "style": "communication",
    "autonomy": "autonomie", "decision": "autonomie", "rhythm": "environnement", "goals": "objectifs",
    "values": "autonomie", "friction": "frictions",
}
_SOUL_SECTIONS = [
    ("Voix", "Voice", ["1.4", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.8", "4.9"]),
    ("Décision et erreurs", "Decisions and mistakes",
     ["1.5", "1.7", "1.8", "3.4", "3.8", "5.7", "6.1", "6.3", "6.4", "6.5", "6.6", "10.3", "10.5"]),
    ("Autonomie", "Autonomy", ["1.6", "5.1", "5.2", "5.3", "5.6", "5.8", "7.6"]),
    ("Lignes rouges et prudence", "Red lines and caution", ["1.12", "9.1", "9.2", "9.3", "9.4", "9.5"]),
    ("Ce qui compte pour toi", "What matters to you", ["1.9", "1.10", "8.5", "8.6"]),
]


def _usable(store: Store) -> dict[str, dict[str, Any]]:
    """Answers allowed to leave the local store: not skipped, not 'inferred' (unconfirmed),
    not tagged local-only, and respecting the scope the user chose in 0.1."""
    q = questionnaire()["questions"]
    a = store.answers()
    scope = a.get("0.1", {}).get("value", "").lower()
    narrow = bool(scope) and not re.search(r"\b(tout|everything)\b", scope)
    out = {}
    for qid, v in a.items():
        if v.get("skipped") or v["source"] == "inferred" or "L" in q[qid]["to"]:
            continue
        if narrow and module_of(qid) in ("ikigai", "values"):
            continue
        out[qid] = v
    return out


def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def compile_user(store: Store, lang: str | None = None) -> dict[str, Any]:
    """Build the USER.md proposal: one line per theme, <=120 chars, whole file <=1375."""
    lg = _lang(lang or store.meta().get("lang", "en"))
    idx = 0 if lg == "fr" else 1
    use = _usable(store)
    q = questionnaire()["questions"]
    items = []  # (theme_idx, order, prio, text)
    for ti, (tfr, ten, ids) in enumerate(_USER_THEMES):
        for order, (qid, prio) in enumerate(ids):
            if qid in use and "U" in q[qid]["to"]:
                items.append([ti, order, prio, qid, _clip(use[qid]["value"], 70)])
    detail = "Détail : profile/" if lg == "fr" else "Detail: profile/"

    def build(its: list[list[Any]]) -> list[str]:
        """Pack each theme's parts into lines of at most LINE_LIMIT chars. A part is never cut
        in the middle: a theme that does not fit on one line continues on the next."""
        lines: list[str] = []
        for ti, (tfr, ten, _) in enumerate(_USER_THEMES):
            head = (tfr, ten)[idx]
            cur = ""
            for i in its:
                if i[0] != ti:
                    continue
                part = f"{_LABELS[i[3]][idx]}: {i[4]}"
                if not cur:
                    cur = f"{head} — {part}"
                elif len(cur) + 2 + len(part) <= LINE_LIMIT:
                    cur += "; " + part
                else:
                    lines.append(cur)
                    cur = f"{head} — {part}"
            if cur:
                lines.append(cur)
        if lines:
            lines.append(detail)
        return lines

    dropped: list[str] = []
    while True:
        lines = build(items)
        size = len("\n§\n".join(lines))
        if size <= USER_LIMIT or not items:
            break
        worst = max(items, key=lambda i: (i[2], -i[1]))
        items.remove(worst)
        dropped.append(worst[3])
    return {"lines": lines, "chars": size, "limit": USER_LIMIT, "dropped": dropped,
            "kept": [i[3] for i in items], "separator": "\n§\n"}


def compile_soul(store: Store, lang: str | None = None) -> str:
    lg = _lang(lang or store.meta().get("lang", "en"))
    idx = 0 if lg == "fr" else 1
    use = _usable(store)
    qs = questionnaire()["questions"]
    head = ("# Brouillon de SOUL.md (généré par Ikigai : à relire, jamais appliqué seul)\n"
            if lg == "fr" else "# SOUL.md draft (generated by Ikigai: review it, never applied automatically)\n")
    out = [head]
    for sec in _SOUL_SECTIONS:
        rows = [(qid, use[qid]) for qid in sec[2] if qid in use and "S" in qs[qid]["to"]]
        if not rows:
            continue
        out.append(f"\n## {sec[idx]}\n")
        for qid, v in rows:
            out.append(f"- {qs[qid][lg]} → {_clip(v['value'], 200)}\n")
    return "".join(out) if len(out) > 1 else ""


def compile_sheets(store: Store, lang: str | None = None) -> dict[str, str]:
    lg = _lang(lang or store.meta().get("lang", "en"))
    use = _usable(store)
    qs = questionnaire()["questions"]
    sheets: dict[str, list[str]] = {}
    for qid, v in sorted(use.items(), key=lambda kv: [int(x) for x in kv[0].split(".")]):
        name = _SHEET.get(module_of(qid))
        if name is None:  # express questions go with the sheet of their theme number
            name = {"1.1": "communication", "1.2": "travail", "1.3": "objectifs", "1.4": "communication",
                    "1.5": "autonomie", "1.6": "autonomie", "1.7": "autonomie", "1.8": "autonomie",
                    "1.9": "frictions", "1.10": "frictions", "1.11": "environnement",
                    "1.12": "autonomie"}[qid]
        when = time.strftime("%Y-%m-%d", time.localtime(v["ts"]))
        quote = v["quote"] if v["quote"] and v["quote"] != v["value"] else ""
        row = f"- **{qs[qid][lg]}** ({qid}, {v['source']}, {when})\n  {v['value']}\n"
        if quote:
            row += f"  > {quote}\n"
        sheets.setdefault(name, []).append(row)
    return {n: f"# {n}\n\n" + "".join(rows) for n, rows in sheets.items()}


def snapshot(store: Store) -> Path:
    path = store.root / "history" / (time.strftime("%Y-%m-%d_%H%M%S") + ".json")
    _write(path, store.answers())
    return path


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(q for q in set(old) & set(new) if old[q]["value"] != new[q]["value"])
    return {"added": added, "removed": removed, "changed": changed}


def user_diff(current: str, proposed_lines: list[str]) -> str:
    proposed = "\n§\n".join(proposed_lines)
    return "".join(difflib.unified_diff(current.splitlines(True), (proposed + "\n").splitlines(True),
                                        "USER.md (actuel)", "USER.md (proposé)"))


def write_compiled(store: Store, lang: str | None = None) -> dict[str, Any]:
    """Write sheets and the SOUL draft into the plugin's own directory. Touches nothing else."""
    snapshot(store)
    sheets = compile_sheets(store, lang)
    prof = store.root / "profile"
    prof.mkdir(exist_ok=True, mode=0o700)
    for old in prof.glob("*.md"):
        old.unlink()
    for name, text in sheets.items():
        (prof / f"{name}.md").write_text(text, encoding="utf-8")
    soul = compile_soul(store, lang)
    draft = store.root / "draft" / "SOUL.draft.md"
    if soul:
        draft.parent.mkdir(exist_ok=True, mode=0o700)
        draft.write_text(soul, encoding="utf-8")
    elif draft.exists():
        draft.unlink()
    return {"sheets": sorted(sheets), "soul_draft": str(draft) if soul else None,
            "profile_dir": str(prof)}


# ---------------------------------------------------------------- nudges and corrections

_CORR = {
    "length": r"too long|trop long|plus court|shorter|more concise|moins de blabla|raccourcis",
    "format": r"no (bullet|list|table|emoji)|pas de (liste|tableau|emoji)|stop (using|with) (bullet|emoji)|arr[êe]te (les|d'utiliser)",
    "language": r"in (french|english)|en (fran[çc]ais|anglais)|speak (french|english)|parle[- ]moi en",
    "asking": r"stop asking|arr[êe]te de (me )?demander|don'?t ask|ne me demande pas|just do it|fais-le",
    "autonomy": r"without asking|sans (me )?demander|ask (me )?first|demande[- ]moi (d'abord|avant)|i didn'?t (ask|tell) you to|je ne t'ai pas demand",
    "tone": r"too (formal|casual|chatty)|trop (formel|familier|bavard)|be more (direct|blunt)|sois plus direct",
    "repeat": r"i (already )?told you|je (te l'ai|te l')?\s*(d[ée]j[àa] )?dit|i said|j'ai dit|encore une fois|again",
}
CORRECTION_THRESHOLD = 3


def detect_correction(text: str) -> str | None:
    t = (text or "").lower()
    if len(t) > 600:        # long messages are tasks, not corrections
        return None
    for theme, rx in _CORR.items():
        if re.search(rx, t):
            return theme
    return None


def note_correction(store: Store, theme: str, quote: str) -> dict[str, Any]:
    m = store.meta()
    c = m.get("corrections", {})
    entry = c.get(theme, {"n": 0, "quotes": []})
    entry["n"] += 1
    entry["quotes"] = (entry["quotes"] + [_clip(quote, 160)])[-3:]
    c[theme] = entry
    upd: dict[str, Any] = {"corrections": c}
    if entry["n"] >= CORRECTION_THRESHOLD and theme not in m.get("proposed_rules", []):
        upd["rule_pending"] = {"theme": theme, "quotes": entry["quotes"]}
    store.set_meta(**upd)
    return entry


def clear_rule(store: Store, theme: str, accepted: bool) -> None:
    m = store.meta()
    prop = list(m.get("proposed_rules", [])) + [theme]
    c = m.get("corrections", {})
    c.pop(theme, None)
    store.set_meta(rule_pending=None, proposed_rules=prop, corrections=c)


def nudge(store: Store, is_first_turn: bool, now: float | None = None) -> str | None:
    """Context to inject on the first turn of a session, or None. Never more than one nudge per
    24 h, one initial offer ever, and a weekly check-in only for users who started the interview."""
    now = now or _now()
    m = store.meta()
    if m.get("off"):
        return None
    if m.get("rule_pending"):
        r = m["rule_pending"]
        return ("[ikigai] The user has corrected you on the same theme (%s) %d times: %s. "
                "Ask once, briefly, whether this should become a lasting rule. If yes, save it with "
                "ikigai_save(id='10.5', source='corrected', ...) and call ikigai_rule_done; if no, "
                "call ikigai_rule_done with accepted=false." % (r["theme"], CORRECTION_THRESHOLD,
                                                                " | ".join(r["quotes"])))
    if not is_first_turn:
        return None
    if now - m.get("last_nudge", 0) < NUDGE_COOLDOWN_H * 3600:
        return None
    if not m.get("started") and not m.get("offered"):
        store.set_meta(offered=True, last_nudge=now)
        return ("[ikigai] You have not met this user's profile yet. At a natural pause in this first "
                "exchange (never instead of answering them), offer ONCE and briefly a 10-minute "
                "getting-to-know-you interview, started with /ikigai start. Accept a refusal silently.")
    last = m.get("last_checkin", m.get("started", 0))
    if m.get("started") and now - last > CHECKIN_DAYS * 86400 and not m.get("checkin_declined_until", 0) > now:
        store.set_meta(last_nudge=now)
        return ("[ikigai] A weekly check-in is due (3 short questions: a friction, a success, a priority "
                "that changed). At a natural pause, offer it once. If declined, call ikigai_snooze.")
    return None


def awaiting_context(store: Store, user_message: str, now: float | None = None) -> str | None:
    """While an interview question is open, tell the agent how to treat the user's next message."""
    now = now or _now()
    m = store.meta()
    qid = m.get("awaiting")
    if not qid or now - m.get("awaiting_ts", 0) > AWAIT_TTL_H * 3600:
        return None
    q = render(qid, m.get("lang", "en"))
    return ("[ikigai] An interview question is open: %s «%s». If the user's message answers it, call "
            "ikigai_save(id='%s', value=<answer>, quote=<their exact words>), then call ikigai_next and "
            "ask the next question: ONE question per turn, no commentary on the answer beyond one short "
            "acknowledgement. If they want to skip, save with skipped=true. If the message is about "
            "something else, answer it normally and leave the question open." % (qid, q["text"], qid))
