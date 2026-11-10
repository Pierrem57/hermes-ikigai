import json, time
from pathlib import Path
import pytest
import core


@pytest.fixture
def st(tmp_path):
    return core.Store(tmp_path / "ikigai")


def fill(st, answers, source="declared"):
    for qid, v in answers.items():
        st.save(qid, v, quote=v, source=source)


def test_questionnaire_is_consistent():
    q = core.questionnaire()
    assert len(q["questions"]) == 76
    in_modules = [i for m in q["modules"] for i in m["questions"]]
    assert sorted(in_modules) == sorted(q["questions"]) and len(set(in_modules)) == 76
    for qid, d in q["questions"].items():
        assert d["fr"] and d["en"] and d["to"]
        if d["type"] == "choice":
            assert len(d["opts_fr"]) == len(d["opts_en"]) >= 2, qid


def test_express_path_is_short(st):
    ids = core.pending_ids(st, express=True)
    assert len([i for i in ids if i.startswith("1.")]) == 12
    assert not any(i.startswith(("2.", "3.", "4.")) for i in ids)


def test_flow_marks_awaiting_and_advances(st):
    q = core.next_question(st, lang="fr")
    assert q["id"] == "0.1" and st.meta()["awaiting"] == "0.1"
    st.save("0.1", "Tout", "tout")
    assert st.meta()["awaiting"] is None
    assert core.next_question(st)["id"] == "0.2"


def test_skip_is_not_asked_again(st):
    st.save("0.1", "", skipped=True)
    assert "0.1" not in core.pending_ids(st)


def test_declared_answer_needs_words(st):
    with pytest.raises(ValueError):
        st.save("1.1", "  ", "", "declared")
    with pytest.raises(KeyError):
        st.save("99.9", "x")


def test_store_permissions(st):
    st.save("1.1", "Alex", "Alex")
    assert oct((st.root / "answers.json").stat().st_mode & 0o777) == "0o600"


def test_inferred_never_reaches_user_or_soul(st):
    fill(st, {"1.1": "Alex", "1.12": "jamais envoyer d'argent"}, source="inferred")
    assert core.compile_user(st)["lines"] == []
    assert core.compile_soul(st) == ""


def test_local_only_never_leaves(st):
    fill(st, {"0.1": "Tout", "5.5": "les dossiers clients", "0.2": "Santé"})
    blob = "\n".join(core.compile_user(st)["lines"]) + core.compile_soul(st) + "".join(core.compile_sheets(st).values())
    assert "dossiers clients" not in blob and "Santé" not in blob


def test_user_md_fits_budget_and_drops_lowest_priority_first(st):
    long = "x" * 59
    fill(st, {"0.1": "Tout", "1.1": "Alex", "1.2": long, "3.7": long, "1.12": "rien sans accord",
              "1.6": "lire seulement", "5.1": "4", "5.2": "1", "5.3": "montrer d'abord", "5.4": long,
              "1.5": long, "1.4": long, "10.3": long, "4.7": long, "7.2": long, "7.4": long,
              "1.11": long, "3.2": long, "3.5": long, "7.3": long, "1.3": long, "8.1": long,
              "2.8": long, "6.2": long, "8.4": long})
    r = core.compile_user(st, "fr")
    assert r["chars"] <= core.USER_LIMIT
    assert all(len(l) <= core.LINE_LIMIT for l in r["lines"])
    kept = "\n".join(r["lines"])
    assert "rien sans accord" in kept and "Alex" in kept        # priority 1 survives
    assert r["lines"][-1].startswith("Détail")


def test_narrow_scope_excludes_ikigai_module(st):
    fill(st, {"0.1": "Seulement les préférences de travail", "2.1": "le jeu d'échecs"})
    assert "échecs" not in "".join(core.compile_sheets(st).values())
    fill(st, {"0.1": "Tout"})
    assert "échecs" in "".join(core.compile_sheets(st).values())


def test_soul_draft_written_but_real_soul_untouched(st, tmp_path):
    fill(st, {"0.1": "Tout", "4.1": "Direct et sec", "1.12": "pas d'envoi sans accord"})
    out = core.write_compiled(st, "fr")
    assert Path(out["soul_draft"]).read_text().count("Direct et sec") == 1
    assert Path(out["soul_draft"]).parent.name == "draft"
    assert (st.root / "profile").is_dir() and list((st.root / "history").glob("*.json"))
    assert not (tmp_path / "SOUL.md").exists()


def test_sheets_keep_exact_quote_and_source(st):
    st.save("1.9", "trop de blabla", "Il m'a répondu 4 paragraphes pour rien", "declared")
    s = core.compile_sheets(st, "fr")["frictions"]
    assert "Il m'a répondu 4 paragraphes pour rien" in s and "declared" in s


def test_diff_snapshots():
    a = {"1.1": {"value": "A"}, "1.2": {"value": "B"}}
    b = {"1.1": {"value": "A2"}, "1.3": {"value": "C"}}
    assert core.diff_snapshots(a, b) == {"added": ["1.3"], "removed": ["1.2"], "changed": ["1.1"]}


def test_stale_answers_flagged(st):
    st.save("1.1", "Alex", "Alex")
    a = st.answers(); a["1.1"]["ts"] = time.time() - 100 * 86400
    (st.root / "answers.json").write_text(json.dumps(a))
    assert core.status(st)["stale"] == ["1.1"]


@pytest.mark.parametrize("msg,theme", [
    ("trop long, plus court stp", "length"), ("stop asking me, just do it", "asking"),
    ("je te l'ai déjà dit", "repeat"), ("Fais-moi un résumé du dossier", None),
    ("x" * 700 + " trop long", None)])
def test_detect_correction(msg, theme):
    assert core.detect_correction(msg) == theme


def test_three_corrections_propose_a_rule_once(st):
    for _ in range(2):
        core.note_correction(st, "length", "trop long")
    assert not st.meta().get("rule_pending")
    core.note_correction(st, "length", "trop long encore")
    assert st.meta()["rule_pending"]["theme"] == "length"
    assert "length" in core.nudge(st, False)
    core.clear_rule(st, "length", True)
    for _ in range(3):
        core.note_correction(st, "length", "trop long")
    assert not st.meta().get("rule_pending")        # not proposed twice


def test_initial_offer_once_then_cooldown(st):
    now = 1_000_000.0
    assert "interview" in core.nudge(st, True, now)
    assert core.nudge(st, True, now + 3600) is None
    assert core.nudge(st, True, now + 10 * 86400) is None   # never re-offered when not started
    assert core.nudge(st, False, now) is None               # only on the first turn


def test_weekly_checkin_only_after_start_and_snooze(st):
    t0 = 1_000_000.0
    st.set_meta(started=t0, offered=True)
    assert core.nudge(st, True, t0 + 3 * 86400) is None
    assert "check-in" in core.nudge(st, True, t0 + 8 * 86400)
    st.set_meta(last_nudge=0, checkin_declined_until=t0 + 30 * 86400)
    assert core.nudge(st, True, t0 + 9 * 86400) is None


def test_off_switch(st):
    st.set_meta(off=True)
    assert core.nudge(st, True, 1e6) is None


def test_awaiting_context_expires(st):
    q = core.next_question(st, lang="en")
    t = st.meta()["awaiting_ts"]
    assert q["id"] in core.awaiting_context(st, "ok", t + 60)
    assert core.awaiting_context(st, "ok", t + 13 * 3600) is None


def test_erase_all(st):
    st.save("1.1", "Alex", "Alex"); core.write_compiled(st)
    st.erase_all()
    assert not list(st.root.rglob("*"))


def test_red_line_is_never_cut_in_the_middle(st):
    line = "Ne jamais envoyer un message à un client sans me le montrer"
    fill(st, {"0.1": "Tout", "1.12": line, "1.6": "Lire et analyser seulement", "5.3": "Je montre le texte exact et j'attends"})
    out = "\n".join(core.compile_user(st, "fr")["lines"])
    assert line in out and "…" not in out
    assert all(len(l) <= core.LINE_LIMIT for l in core.compile_user(st, "fr")["lines"])


def start(st, lang="fr"):
    st.set_meta(lang=lang, started=1.0)
    core.next_question(st, lang=lang)
    core.set_active(st, True)


def test_plain_reply_is_saved_and_next_question_returned(st):
    start(st)
    r = core.interview_turn(st, "tout")
    assert st.answers()["0.1"]["value"] == "Tout" and st.answers()["0.1"]["quote"] == "tout"
    assert "[0.2]" in r["message"] and not r["done"]
    assert st.meta()["awaiting"] == "0.2"


def test_number_reply_and_unclear_reply(st):
    start(st)
    assert core.interview_turn(st, "2")
    assert st.answers()["0.1"]["value"] == "Seulement le professionnel"
    r = core.interview_turn(st, "bof je sais pas trop")
    assert "0.2" not in st.answers() and "[0.2]" in r["message"] and "numéro" in r["message"]


def test_skip_and_stop(st):
    start(st)
    core.interview_turn(st, "passer")
    assert st.answers()["0.1"]["skipped"]
    r = core.interview_turn(st, "pause")
    assert r["done"] and core.interview_turn(st, "tout") is None


def test_inactive_interview_lets_the_model_answer(st):
    assert core.interview_turn(st, "tout") is None
    st.set_meta(awaiting="0.1", awaiting_ts=time.time(), active=False)
    assert core.interview_turn(st, "tout") is None


def test_full_express_run_ends_cleanly(st):
    start(st)
    n = 0
    while True:
        qid = st.meta()["awaiting"]
        q = core.render(qid, "fr")
        reply = "1" if q.get("options") else "une réponse libre"
        r = core.interview_turn(st, reply)
        n += 1
        assert n < 40
        if r["done"]:
            break
    assert n == 15 and not st.meta()["active"] and "compile" in r["message"]
    assert len([q for q in st.answers() if not st.answers()[q]["skipped"]]) == 15


def test_stale_open_question_expires(st):
    start(st)
    assert core.interview_turn(st, "tout", time.time() + 13 * 3600) is None
    assert not st.meta()["active"]
