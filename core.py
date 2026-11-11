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
    if q.get("multi"):
        out["multi"] = True
        out["exclusive"] = list(q.get("exclusive") or [])   # 1-based option numbers
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
    "0.2": ("Ne pas mémoriser", "Never memorize"),
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
    ("Règles", "Rules", [("1.12", 1), ("0.2", 1), ("1.6", 1), ("5.1", 1), ("5.2", 1), ("5.3", 1), ("5.4", 2),
                         ("1.5", 2), ("1.4", 2), ("10.3", 3)]),
    ("Format", "Format", [("4.7", 2), ("7.2", 3), ("7.4", 4)]),
    ("Contexte", "Context", [("1.11", 3), ("3.2", 3), ("3.5", 4), ("7.3", 4)]),
    ("Objectifs", "Goals", [("1.3", 2), ("8.1", 2), ("2.8", 4), ("6.2", 4), ("8.4", 4)]),
]
_SHEET = {  # module -> sheet key; express questions are spread by theme (_EXPRESS_SHEET)
    "m0": "autonomy", "express": None, "ikigai": "ikigai", "work": "work", "style": "communication",
    "autonomy": "autonomy", "decision": "autonomy", "rhythm": "environment", "goals": "goals",
    "values": "autonomy", "friction": "frictions",
}
_EXPRESS_SHEET = {
    "1.1": "communication", "1.2": "work", "1.3": "goals", "1.4": "communication", "1.5": "autonomy",
    "1.6": "autonomy", "1.7": "autonomy", "1.8": "autonomy", "1.9": "frictions", "1.10": "frictions",
    "1.11": "environment", "1.12": "autonomy",
}
_SHEET_NAMES = {  # sheet key -> file name (French, English); listed in display order
    "work": ("travail", "work"), "ikigai": ("ikigai", "ikigai"),
    "communication": ("communication", "communication"), "autonomy": ("autonomie", "autonomy"),
    "environment": ("environnement", "environment"), "goals": ("objectifs", "goals"),
    "frictions": ("frictions", "frictions"),
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


SEP = "\n§\n"   # entry separator of Hermes memory files
# Entries this plugin owns in USER.md: a themed line is "<theme> — <one of its own labels>: ...",
# plus its pointer to the sheets. Everything else in the file belongs to the user or the agent and
# is never proposed for removal: "Context — I work at ACME" is not ours, "Contexte — Horaires: ..." is.
_MANAGED = re.compile(
    "^(?:%s) — (?:%s): |^(?:Détail du profil Ikigai|Ikigai profile detail) ?: |^(?:Détail|Detail) ?: profile/$" % (
        "|".join(re.escape(head) for theme in _USER_THEMES for head in theme[:2]),
        "|".join(re.escape(label) for pair in _LABELS.values() for label in pair)))


def split_entries(text: str) -> list[str]:
    return [e.strip() for e in (text or "").split(SEP) if e.strip()]


def is_managed(entry: str) -> bool:
    return bool(_MANAGED.match(entry))


def _detail_line(store: Store, fr: bool) -> str:
    """Pointer to the detailed sheets, as a path the agent can open. Kept within LINE_LIMIT."""
    head = "Détail du profil Ikigai : " if fr else "Ikigai profile detail: "
    path = str(store.root / "profile")
    home = str(Path.home())
    short = "~" + path[len(home):] if path.startswith(home + os.sep) else path
    for p in (short, path):
        if len(head) + len(p) + 1 <= LINE_LIMIT:
            return f"{head}{p}/"
    return head + "$HERMES_HOME/plugin-data/ikigai/profile/"


def compile_user(store: Store, lang: str | None = None, current: str = "") -> dict[str, Any]:
    """Build the USER.md proposal and MERGE it with the file's current content.

    Ikigai lines: one or more per theme, each <=120 chars. Entries already in USER.md that Ikigai
    does not own are kept as they are and count against the 1,375-character limit first; Ikigai
    then drops its own lowest-priority facts until the whole file fits. ``operations`` is the
    minimal change set for the memory tool: stale Ikigai lines removed, new ones added."""
    lg = _lang(lang or store.meta().get("lang", "en"))
    idx = 0 if lg == "fr" else 1
    use = _usable(store)
    q = questionnaire()["questions"]
    items = []  # [theme_idx, order, prio, qid, text]
    for ti, (tfr, ten, ids) in enumerate(_USER_THEMES):
        for order, (qid, prio) in enumerate(ids):
            if qid not in use or "U" not in q[qid]["to"]:
                continue
            if q[qid].get("multi") and _is_exclusive_answer(qid, use[qid]["value"]):
                continue                     # "None of them" is not a rule worth a line
            items.append([ti, order, prio, qid, _clip(use[qid]["value"], 70)])
    detail = _detail_line(store, lg == "fr")
    existing = split_entries(current)
    kept = [e for e in existing if not is_managed(e)]
    budget = USER_LIMIT - (len(SEP.join(kept)) + len(SEP) if kept else 0)

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
        if len(SEP.join(lines)) <= budget or not items:
            break
        # lowest priority first; among equals, the last one of the last theme (the red line and
        # the name are the first entries of their themes, so they go last)
        worst = max(items, key=lambda i: (i[2], i[0], i[1]))
        items.remove(worst)
        dropped.append(worst[3])
    if lines and len(SEP.join(lines)) > budget:
        lines = []                           # not even the pointer fits: propose nothing
    merged = kept + lines
    stale = [e for e in existing if is_managed(e) and e not in lines]
    fresh = [l for l in lines if l not in existing]
    ops = ([{"action": "remove", "old_text": e} for e in stale]
           + [{"action": "add", "content": l} for l in fresh])
    return {"lines": lines, "kept_entries": kept, "merged": merged, "chars": len(SEP.join(merged)),
            "limit": USER_LIMIT, "dropped": dropped, "kept": [i[3] for i in items],
            "operations": ops, "separator": SEP}


def _is_exclusive_answer(qid: str, value: str) -> bool:
    qd = questionnaire()["questions"][qid]
    texts = {qd[k][n - 1].lower() for k in ("opts_fr", "opts_en") for n in qd.get("exclusive", [])
             if k in qd and 1 <= n <= len(qd[k])}
    return value.strip().lower() in texts


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


def sheet_rows(store: Store, lang: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """The answers allowed out of the store, grouped by sheet (named in the interview language)."""
    lg = _lang(lang or store.meta().get("lang", "en"))
    idx = 0 if lg == "fr" else 1
    use = _usable(store)
    qs = questionnaire()["questions"]
    out: dict[str, list[dict[str, Any]]] = {names[idx]: [] for names in _SHEET_NAMES.values()}
    for qid, v in sorted(use.items(), key=lambda kv: [int(x) for x in kv[0].split(".")]):
        key = _SHEET.get(module_of(qid)) or _EXPRESS_SHEET[qid]
        out[_SHEET_NAMES[key][idx]].append({
            "id": qid, "question": qs[qid][lg], "value": v["value"], "source": v["source"],
            "quote": v["quote"] if v["quote"] and v["quote"] != v["value"] else "",
            "date": time.strftime("%Y-%m-%d", time.localtime(v["ts"]))})
    return {name: rows for name, rows in out.items() if rows}


def compile_sheets(store: Store, lang: str | None = None) -> dict[str, str]:
    sheets = {}
    for name, rows in sheet_rows(store, lang).items():
        text = f"# {name}\n\n"
        for r in rows:
            text += f"- **{r['question']}** ({r['id']}, {r['source']}, {r['date']})\n  {r['value']}\n"
            if r["quote"]:
                text += f"  > {r['quote']}\n"
        sheets[name] = text
    return sheets


def _short_path(path: Path) -> str:
    p, home = str(path), str(Path.home())
    return "~" + p[len(home):] if p.startswith(home + os.sep) else p


def show(store: Store, what: str = "", lang: str | None = None) -> str:
    """What Ikigai holds, as plain text for the chat: an overview, one sheet, every sheet (``all``)
    or the SOUL.md draft (``soul``). Built from the current answers, so it needs no compile first.
    Local-only, inferred and out-of-scope answers stay out, exactly as in the sheets."""
    lg = _lang(lang or store.meta().get("lang", "en"))
    fr = lg == "fr"
    rows = sheet_rows(store, lg)
    what = (what or "").strip().lower()

    def block(name: str) -> str:
        lines = [f"— {name} ({len(rows[name])}) —"]
        for r in rows[name]:
            tag = "" if r["source"] == "declared" else f"  ({r['source']})"
            lines += [f"[{r['id']}] {r['question']}", f"    {r['value']}{tag}"]
            if r["quote"]:
                lines.append(f"    « {r['quote']} »")
        return "\n".join(lines)

    empty = "Rien à montrer pour l'instant : /ikigai start" if fr else "Nothing to show yet: /ikigai start"
    if what == "soul":
        return compile_soul(store, lg) or empty
    if what in ("all", "tout"):
        return "\n\n".join(block(n) for n in rows) or empty
    if what:
        hits = [n for n in rows if n == what] or [n for n in rows if n.startswith(what)]
        if len(hits) == 1:
            return block(hits[0])
        return (("Fiche inconnue. Fiches : " if fr else "Unknown sheet. Sheets: ") + ", ".join(rows)) if rows else empty
    if not rows:
        return empty
    s = status(store)
    held = sum(1 for qid, v in store.answers().items() if not v.get("skipped")) - sum(len(r) for r in rows.values())
    sheets = " · ".join(f"{n} ({len(r)})" for n, r in rows.items())
    root = _short_path(store.root)
    if fr:
        out = [f"Profil Ikigai : {s['answered']} réponses sur {s['total']}, {s['skipped']} passées.", f"Fiches : {sheets}"]
        if held:
            out.append(f"Hors fiches : {held} réponse(s) gardée(s) en local (locales, déduites ou hors périmètre).")
        out += ["Voir : /ikigai show <fiche> · /ikigai show all · /ikigai show soul",
                f"Fichiers : {root}/ (answers.json ; profile/ et draft/ sont écrits par /ikigai compile)"]
    else:
        out = [f"Ikigai profile: {s['answered']} of {s['total']} answered, {s['skipped']} skipped.", f"Sheets: {sheets}"]
        if held:
            out.append(f"Not in the sheets: {held} answer(s) kept local (local-only, inferred or out of scope).")
        out += ["View: /ikigai show <sheet> · /ikigai show all · /ikigai show soul",
                f"Files: {root}/ (answers.json; profile/ and draft/ are written by /ikigai compile)"]
    return "\n".join(out)


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
    """Unified diff between the current USER.md and the merged proposal (kept entries + Ikigai lines)."""
    proposed = SEP.join(proposed_lines)
    return "".join(difflib.unified_diff(current.splitlines(True), proposed.splitlines(True),
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
    if not qid or m.get("active") or now - m.get("awaiting_ts", 0) > AWAIT_TTL_H * 3600:
        return None        # an active interview is answered by interview_turn, in its own session only
    q = render(qid, m.get("lang", "en"))
    return ("[ikigai] An interview question is open: %s «%s». If the user's message answers it, call "
            "ikigai_save(id='%s', value=<answer>, quote=<their exact words>), then call ikigai_next and "
            "ask the next question: ONE question per turn, no commentary on the answer beyond one short "
            "acknowledgement. If they want to skip, save with skipped=true. If the message is about "
            "something else, answer it normally and leave the question open." % (qid, q["text"], qid))


# ---------------------------------------------------------------- deterministic interview turn
# While an interview is active, the plugin itself consumes the user's reply (via a pre_llm_call
# block), saves it and returns the next question. No model call, no tool call: it cannot be
# derailed by a model that ignores the open question, and it costs no tokens.
#
# The interview belongs to the session that started it: replies from any other chat, from cron
# jobs or from subagents are never consumed.

_SKIP = re.compile(r"^\s*(passer?|skip|pass|suivante?|next|\.)\s*$", re.I)
_STOP = re.compile(r"^\s*(pause|stop|arr[êe]te|arr[êe]ter|quit|quitter|exit)\s*$", re.I)
_TAG = re.compile(r"^\s*ikigai:(\d+\.\d+):(.*)$", re.S)      # what a widget button sends
_STRONG = re.compile(r"[,;/+&|]")
_WEAK = re.compile(r"\b(?:et|and)\b", re.I)
_SPACES = re.compile(r"\s+")
NON_INTERACTIVE = frozenset({"cron", "subagent", "oneshot"})
_LOCAL = frozenset({"desktop", "tui", "cli", "local"})


def match_option(text: str, options: list[str]) -> str | None:
    t = text.strip().lower()
    if t.isdigit() and 1 <= int(t) <= len(options):
        return options[int(t) - 1]
    for o in options:
        if t == o.lower():
            return o
    hits = [o for o in options if o.lower().startswith(t) or (len(t) >= 3 and t in o.lower())]
    return hits[0] if len(hits) == 1 else None


def _match_token(tok: str, options: list[str]) -> list[str]:
    one = match_option(tok, options)
    if one:
        return [one]
    if tok.isdigit() and len(options) <= 9 and all(1 <= int(ch) <= len(options) for ch in tok):
        return [options[int(ch) - 1] for ch in tok]            # "13" means 1 and 3
    for splitter in (_WEAK, _SPACES):
        parts = [p for p in (x.strip() for x in splitter.split(tok)) if p]
        if len(parts) > 1:
            got = [_match_token(p, options) for p in parts]
            if all(got):
                return [o for g in got for o in g]
    return []


def match_options(text: str, options: list[str]) -> list[str]:
    """Several options in one reply: "1,3", "1 3", "13", "santé et famille". Returns the chosen
    options in questionnaire order, or [] as soon as one part of the reply matches nothing."""
    whole = match_option(text, options)
    if whole:
        return [whole]
    picked: list[str] = []
    for tok in (t.strip() for t in _STRONG.split(text)):
        if not tok:
            continue
        got = _match_token(tok, options)
        if not got:
            return []
        picked += got
    return [o for o in options if o in picked]


def set_active(store: Store, on: bool) -> None:
    store.set_meta(active=on, active_ts=_now())


_ROUTE = re.compile(r"^(agent:|local-|[a-z]+:[a-z]+:)")


def bind_owner(store: Store, session_id: str = "", session_key: str = "", platform: str = "") -> None:
    """Remember which session started the interview. A chat's routing key (``agent:main:...``,
    ``telegram:dm:...``) is not a session id and is kept apart; ``confirmed`` turns true on the
    first reply that matches."""
    known = [i for i in dict.fromkeys([session_id or "", session_key or ""]) if i and not _ROUTE.match(i)]
    routing = [i for i in dict.fromkeys([session_key or "", session_id or ""]) if i and _ROUTE.match(i)]
    store.set_meta(owner={"ids": known, "routing": routing, "platform": platform or "", "confirmed": False})


def _same_surface(a: str, b: str) -> bool:
    return not b or a == b or (a in _LOCAL and b in _LOCAL)


def _owns(store: Store, m: dict[str, Any], session_id: str, parent_session_id: str, platform: str) -> bool:
    if platform in NON_INTERACTIVE:
        return False
    if not session_id:                      # the host did not say which session this is
        return True
    owner = m.get("owner") or {}
    ids = list(owner.get("ids") or [])
    known = session_id in ids or session_id in (owner.get("routing") or [])
    # Same chat after a compaction: the gateway hands the new session the old one as its parent.
    rotated = bool(parent_session_id) and parent_session_id in ids
    # Adopt only when nothing of this interview has been answered yet and the surface is the same:
    # the chat had no session id (only a routing key) when the interview started.
    adopt = not ids and not owner.get("confirmed") and _same_surface(platform, owner.get("platform", ""))
    if not (known or rotated or adopt):
        return False
    if adopt or rotated:
        store.set_meta(owner={**owner, "ids": list(dict.fromkeys(ids + [session_id])), "confirmed": True})
    elif not owner.get("confirmed"):
        store.set_meta(owner={**owner, "confirmed": True})
    return True


def _scope(m: dict[str, Any]) -> tuple[str | None, bool]:
    module = m.get("module") or None
    return module, module is None and not m.get("full", False)


def next_in_scope(store: Store, lang: str | None = None) -> dict[str, Any] | None:
    """Next question of the interview in progress: the chosen module, else the express or full path."""
    module, express = _scope(store.meta())
    return next_question(store, module=module, express=express, lang=lang)


def interview_turn(store: Store, text: str, now: float | None = None, session_id: str = "",
                   parent_session_id: str = "", platform: str = "") -> dict[str, Any] | None:
    """Handle one reply. Returns None when this message is not for the interview (let the model
    answer), else {"message": str, "done": bool}."""
    now = now or _now()
    m = store.meta()
    lg = m.get("lang", "en")
    fr = lg == "fr"
    tag = _TAG.match(text or "")
    active = bool(m.get("active") and m.get("awaiting"))
    if active and now - m.get("awaiting_ts", 0) > AWAIT_TTL_H * 3600:
        store.set_meta(active=False, awaiting=None, pending_show=False)
        active = False
    if not active:
        if tag:                             # a button of an old question, clicked after the end
            return {"message": "L'entretien n'est pas en cours. /ikigai resume pour reprendre." if fr
                    else "No interview is running. /ikigai resume to continue.", "done": True}
        return None
    if not tag and not _owns(store, m, session_id, parent_session_id, platform):
        return None
    qid = m["awaiting"]
    module, express = _scope(m)
    q = render(qid, lg)
    q["remaining"] = len(pending_ids(store, module, express))
    if tag and tag.group(1) != qid:         # a button of a question that is already behind us
        note = "Cette question est déjà passée. Voici la question en cours." if fr \
            else "That question is already behind us. Here is the current one."
        return {"message": f"{note}\n\n{format_question(q, fr, store)}", "done": False}
    if m.get("pending_show"):
        store.set_meta(pending_show=False)
        if not tag:                         # the "go" after /ikigai start: show, do not consume
            return {"message": format_question(q, fr, store), "done": False}
    if tag:
        text = tag.group(2)
    if _STOP.match(text):
        store.set_meta(active=False, awaiting=None, pending_show=False)
        return {"message": "Entretien en pause. /ikigai resume pour reprendre." if fr
                else "Interview paused. /ikigai resume to continue.", "done": True}
    if _SKIP.match(text):
        store.save(qid, "", "", "declared", skipped=True)
    else:
        value = text.strip()
        opts = q.get("options") or []
        bare = bool(re.fullmatch(r"[\d\s,;]+", value))          # numbers only: quote the option text
        if q.get("multi"):
            chosen = match_options(value, opts)
            excl = {opts[n - 1] for n in q.get("exclusive", []) if 1 <= n <= len(opts)}
            clash = sorted(excl & set(chosen)) if len(chosen) > 1 else []
            if not chosen or clash:
                if clash:
                    hint = (f"« {clash[0]} » ne se combine pas avec les autres options." if fr
                            else f"\"{clash[0]}\" cannot be combined with the other options.")
                else:
                    hint = ("Réponds par un ou plusieurs numéros (ex. 1,3) ou textes d'options, ou « passer »."
                            if fr else "Reply with one or more numbers (e.g. 1,3) or option texts, or \"skip\".")
                return {"message": f"{hint}\n\n{format_question(q, fr, store)}", "done": False}
            joined = ", ".join(chosen)
            store.save(qid, joined, joined if bare else value, "declared")
        elif opts:
            opt = match_option(value, opts)
            if opt is None:
                hint = ("Réponds par le numéro ou le texte d'une option, ou « passer »." if fr
                        else "Reply with the number or the text of an option, or \"skip\".")
                return {"message": f"{hint}\n\n{format_question(q, fr, store)}", "done": False}
            store.save(qid, opt, opt if bare else value, "declared")
        else:
            value = value[:1500]
            store.save(qid, value, value, "declared")
    nxt = next_in_scope(store, lg)
    if nxt is None:
        store.set_meta(active=False, awaiting=None, pending_show=False, last_checkin=now)
        if module:
            end = (f"Module « {module} » terminé. /ikigai more <module> pour un autre, /ikigai show pour relire "
                   "ton profil, /ikigai compile pour voir ce que je propose d'écrire dans USER.md." if fr else
                   f"Module \"{module}\" complete. /ikigai more <module> for another one, /ikigai show to "
                   "read your profile, /ikigai compile to see what I would write to USER.md.")
        else:
            end = ("Terminé, merci. /ikigai show pour relire ton profil, /ikigai compile pour voir ce que je "
                   "propose d'écrire dans USER.md." if fr else
                   "Done, thank you. /ikigai show to read your profile, /ikigai compile to see what I would "
                   "write to USER.md.")
        return {"message": end, "done": True}
    return {"message": format_question(nxt, fr, store), "done": False}


# ---------------------------------------------------------------- answer buttons (desktop widget)

_WIDGET_CSS = """<style>
.ik{display:flex;flex-direction:column;gap:6px;align-items:flex-start;padding:4px 0}
.ik button{font:inherit;color:var(--foreground);background:var(--card);border:1px solid var(--border);
border-radius:8px;padding:8px 12px;cursor:pointer;text-align:left}
.ik button:hover{border-color:var(--accent)}
.ik button:disabled{cursor:default;opacity:.55}
.ik .opt.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.ik .opt b{margin-right:6px;color:var(--muted-foreground)}
.ik.multi .opt::before{content:'\\2610';margin-right:8px;color:var(--muted-foreground)}
.ik.multi .opt.sel::before{content:'\\2611';color:var(--accent)}
.ik .row{display:flex;gap:8px;margin-top:4px}
.ik #ok{background:var(--accent);color:var(--card);border-color:var(--accent);opacity:.45}
.ik #ok.on{opacity:1}
.ik .alt{color:var(--muted-foreground)}
</style>"""

_WIDGET_JS = """<script>
const QID=%(qid)s,MULTI=%(multi)s,EXCL=%(excl)s;
const all=()=>[...document.querySelectorAll('.ik button')],opts=[...document.querySelectorAll('.opt')];
const ok=document.getElementById('ok'),picked=()=>opts.filter(b=>b.classList.contains('sel'));
function refresh(){if(ok){const n=picked().length;ok.disabled=!n;ok.classList.toggle('on',!!n);}}
function send(t){all().forEach(b=>b.disabled=true);
 const back=()=>{all().forEach(b=>b.disabled=false);refresh();};
 try{const r=window.hermes.send('ikigai:'+QID+':'+t);
  if(r&&r.then)r.then(a=>{if(a&&a.ok===false)back();},back);}catch(e){back();}}
opts.forEach(b=>b.onclick=()=>{const n=+b.dataset.n,on=MULTI?!b.classList.contains('sel'):true;
 if(!MULTI||(on&&EXCL.includes(n)))opts.forEach(x=>x.classList.remove('sel'));
 if(MULTI&&on&&!EXCL.includes(n))opts.forEach(x=>{if(EXCL.includes(+x.dataset.n))x.classList.remove('sel');});
 b.classList.toggle('sel',on);refresh();});
if(ok)ok.onclick=()=>{const s=picked().map(b=>b.dataset.n);if(s.length)send(s.join(','));};
document.querySelectorAll('[data-send]').forEach(b=>b.onclick=()=>send(b.dataset.send));
</script>"""


def widget_html(q: dict[str, Any], fr: bool) -> str:
    """Answer buttons for one question, in the host theme. A choice or scale question gets one
    button per option plus Confirm (several can be ticked when the question is ``multi``); every
    question gets Skip and Pause. A click sends ``ikigai:<id>:<answer>`` as a hidden user turn
    (``window.hermes.send``), so a button of an old question can never answer the current one."""
    esc = lambda t: (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                     .replace('"', "&quot;"))
    opts = q.get("options") or []
    multi = bool(q.get("multi"))
    ok, skip, pause = ("Valider", "Passer", "Pause") if fr else ("Confirm", "Skip", "Pause")
    btns = "\n".join(f'<button type="button" class="opt" data-n="{i}"><b>{i}</b> {esc(o)}</button>'
                     for i, o in enumerate(opts, 1))
    confirm = f'<button type="button" id="ok" disabled>{ok}</button>\n' if opts else ""
    js = _WIDGET_JS % {"qid": json.dumps(q["id"]), "multi": "true" if multi else "false",
                       "excl": json.dumps([int(n) for n in q.get("exclusive", [])])}
    return (f'{_WIDGET_CSS}\n<div class="ik{" multi" if multi else ""}">\n{btns}\n<div class="row">{confirm}'
            f'<button type="button" class="alt" data-send="{"passer" if fr else "skip"}">{skip}</button>\n'
            f'<button type="button" class="alt" data-send="pause">{pause}</button></div></div>\n{js}\n')


def write_widget(store: Store, q: dict[str, Any], fr: bool) -> Path:
    html = widget_html(q, fr)
    d = store.root / "widgets"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f"q_{q['id'].replace('.', '_')}_{'fr' if fr else 'en'}.html"
    if not path.exists() or path.read_text(encoding="utf-8") != html:
        path.write_text(html, encoding="utf-8")
    return path


def buttons_enabled(store: Store) -> bool:
    """Buttons need a surface that renders ``::preview`` in an assistant message (the desktop app).
    Auto-detected from the platform of the last call; ``/ikigai buttons on|off|auto`` overrides."""
    m = store.meta()
    forced = m.get("buttons")
    return bool(forced) if forced is not None else m.get("platform") == "desktop"


def format_question(q: dict[str, Any], fr: bool, store: Store | None = None) -> str:
    """The question as text (always answerable by typing), plus its buttons when ``store`` is given
    and the surface renders them. Pass no store for slash-command output: it is shown as plain text."""
    s = f"[{q['id']}] {q['text']}"
    if q.get("multi"):
        s += " (plusieurs choix possibles)" if fr else " (several choices allowed)"
    if q.get("options"):
        s += "\n" + "\n".join(f"  {i}. {o}" for i, o in enumerate(q["options"], 1))
    left = q.get("remaining")
    tail = ("« passer » pour sauter, « pause » pour arrêter" if fr else "\"skip\" to skip, \"pause\" to stop")
    if q.get("multi"):
        tail = ("plusieurs numéros possibles, ex. 1,3 · " if fr else "several numbers allowed, e.g. 1,3 · ") + tail
    if left:
        tail += (f" · {left} restantes" if fr else f" · {left} left")
    s += "\n\n(" + tail + ")"
    if store is not None and buttons_enabled(store):
        s += f'\n\n::preview{{file="{write_widget(store, q, fr)}"}}'
    return s
