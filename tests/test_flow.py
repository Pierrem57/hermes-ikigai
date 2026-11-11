"""Flow tests: multi-select, answer buttons, session ownership, module scope, USER.md merge, and the
slash command and hook wiring (loaded without Hermes, with the store swapped for a temp directory)."""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

import core

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def st(tmp_path):
    return core.Store(tmp_path / "ikigai")


@pytest.fixture
def plugin(st, monkeypatch):
    spec = importlib.util.spec_from_file_location("ikigai_plugin_under_test", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_store", lambda: st)
    mod.user_md = ""
    monkeypatch.setattr(mod, "_user_md", lambda: mod.user_md)
    return mod


def start(st, lang="fr", module=None, full=False):
    st.set_meta(lang=lang, started=1.0, module=module, full=full)
    core.next_in_scope(st, lang)
    core.set_active(st, True)


def answer(st, qid, value):
    st.save(qid, value, value)


# ------------------------------------------------------------------ multi-select (question 0.2)

OPTS = ["Santé", "Argent personnel", "Famille", "Politique", "Aucun"]


@pytest.mark.parametrize("reply,expected", [
    ("1,3", ["Santé", "Famille"]), ("3, 1", ["Santé", "Famille"]), ("1 3", ["Santé", "Famille"]),
    ("13", ["Santé", "Famille"]), ("santé et famille", ["Santé", "Famille"]),
    ("Santé; argent personnel / politique", ["Santé", "Argent personnel", "Politique"]),
    ("2", ["Argent personnel"]), ("aucun", ["Aucun"]), ("famille", ["Famille"]),
    ("1,7", []), ("bof je sais pas trop", []), ("", []),
])
def test_match_options(reply, expected):
    assert core.match_options(reply, OPTS) == expected


def test_multi_reply_is_saved_joined_and_interview_moves_on(st):
    start(st)
    core.interview_turn(st, "tout")
    r = core.interview_turn(st, "1,3")
    a = st.answers()["0.2"]
    assert a["value"] == "Santé, Famille" and a["quote"] == "Santé, Famille" and not a["skipped"]
    assert r["message"].startswith("[0.3]")


def test_multi_keeps_the_users_words_as_quote(st):
    start(st)
    core.interview_turn(st, "tout")
    core.interview_turn(st, "santé et famille")
    assert st.answers()["0.2"] == {**st.answers()["0.2"], "value": "Santé, Famille", "quote": "santé et famille"}


def test_exclusive_option_cannot_be_combined(st):
    start(st)
    core.interview_turn(st, "tout")
    r = core.interview_turn(st, "1,5")
    assert "0.2" not in st.answers() and "Aucun" in r["message"] and "[0.2]" in r["message"]
    core.interview_turn(st, "5")
    assert st.answers()["0.2"]["value"] == "Aucun"


def test_multi_question_text_says_so(st):
    q = core.render("0.2", "fr")
    out = core.format_question(q, True)
    assert "plusieurs choix possibles" in out and "ex. 1,3" in out
    assert "several choices allowed" in core.format_question(core.render("0.2", "en"), False)
    assert "plusieurs" not in core.format_question(core.render("0.1", "fr"), True)


def test_forbidden_topics_reach_user_md_but_none_does_not(st):
    answer(st, "0.1", "Tout")
    answer(st, "0.2", "Santé, Famille")
    assert any("Ne pas mémoriser: Santé, Famille" in l for l in core.compile_user(st, "fr")["lines"])
    answer(st, "0.2", "Aucun")
    assert core.compile_user(st, "fr")["lines"] == []
    answer(st, "0.2", "None")
    assert core.compile_user(st, "en")["lines"] == []


# ------------------------------------------------------------------ answer buttons

def test_every_question_gets_buttons_on_desktop(st):
    st.set_meta(platform="desktop")
    for qid in core.questionnaire()["questions"]:
        out = core.format_question(core.render(qid, "fr"), True, st)
        assert out.count("::preview{file=") == 1, qid


def test_widget_modes(st):
    single = core.widget_html(core.render("0.1", "fr"), True)
    multi = core.widget_html(core.render("0.2", "fr"), True)
    assert "QID=\"0.1\",MULTI=false,EXCL=[]" in single and 'class="ik"' in single
    assert "QID=\"0.2\",MULTI=true,EXCL=[5]" in multi and 'class="ik multi"' in multi
    assert multi.count('class="opt"') == 5 and "'ikigai:'+QID+':'+t" in multi
    assert "Confirm" in core.widget_html(core.render("0.1", "en"), False)


def test_widget_escapes_option_text(st):
    q = {"id": "9.9", "type": "choice", "text": "x", "options": ['<b>"a"</b> & co']}
    html = core.widget_html(q, True)
    assert "&lt;b&gt;&quot;a&quot;&lt;/b&gt; &amp; co" in html and '<b>"a"' not in html


def test_button_click_is_tagged_and_consumed(st):
    start(st)
    r = core.interview_turn(st, "ikigai:0.1:2")
    assert st.answers()["0.1"]["value"] == "Seulement le professionnel"
    r = core.interview_turn(st, "ikigai:0.2:1,3")
    assert st.answers()["0.2"]["value"] == "Santé, Famille" and r["message"].startswith("[0.3]")
    core.interview_turn(st, "ikigai:0.3:passer")
    assert st.answers()["0.3"]["skipped"]
    r = core.interview_turn(st, "ikigai:1.1:pause")
    assert r["done"] and not st.meta()["active"]


def test_button_of_an_old_question_never_answers_the_current_one(st):
    start(st)
    core.interview_turn(st, "1")
    r = core.interview_turn(st, "ikigai:0.1:3")            # the user scrolled up and clicked again
    assert st.answers()["0.1"]["value"] == "Tout" and "0.2" not in st.answers()
    assert "déjà passée" in r["message"] and "[0.2]" in r["message"]


def test_button_after_the_end_is_answered_by_the_plugin_not_the_model(st):
    r = core.interview_turn(st, "ikigai:0.1:1")
    assert r is not None and r["done"] and "resume" in r["message"] and st.answers() == {}


def test_invalid_reply_repeats_the_question_with_its_buttons(st):
    st.set_meta(platform="desktop")
    start(st)
    r = core.interview_turn(st, "peut-être")
    assert "0.1" not in st.answers() and "::preview{file=" in r["message"]


# ------------------------------------------------------------------ whose interview is it

def test_other_sessions_cron_and_subagents_are_never_consumed(st):
    start(st)
    core.bind_owner(st, "sessA", "sessA", "desktop")
    assert core.interview_turn(st, "tout", session_id="sessA", platform="desktop")["message"].startswith("[0.2]")
    assert core.interview_turn(st, "1", session_id="sessB", platform="desktop") is None
    assert core.interview_turn(st, "1", session_id="tg1", platform="telegram") is None
    assert core.interview_turn(st, "run the backup", session_id="c1", platform="cron") is None
    assert core.interview_turn(st, "1", session_id="sub", parent_session_id="sessA", platform="subagent") is None
    assert "0.2" not in st.answers()
    assert core.interview_turn(st, "1", session_id="sessA", platform="desktop") is not None
    assert st.answers()["0.2"]["value"] == "Santé"


def test_first_reply_on_the_same_surface_adopts_the_session(st):
    start(st)
    core.bind_owner(st, "", "agent:main:telegram:dm:42", "telegram")   # only a routing key, no session id yet
    assert core.interview_turn(st, "1", session_id="d1", platform="desktop") is None
    assert core.interview_turn(st, "1", session_id="t1", platform="telegram") is not None
    assert core.interview_turn(st, "1", session_id="t2", platform="telegram") is None      # adopted once
    assert st.meta()["owner"]["confirmed"] and "t1" in st.meta()["owner"]["ids"]


def test_rotated_session_keeps_the_interview(st):
    start(st)
    core.bind_owner(st, "old", "old", "desktop")
    core.interview_turn(st, "1", session_id="old", platform="desktop")
    assert core.interview_turn(st, "1", session_id="new", parent_session_id="old", platform="desktop") is not None
    assert "new" in st.meta()["owner"]["ids"]


# ------------------------------------------------------------------ module scope

def test_more_module_stays_in_its_module_and_ends_there(st):
    for qid in ("0.1", "0.2", "0.3"):
        answer(st, qid, "x")
    start(st, module="rhythm", full=True)
    seen = [st.meta()["awaiting"]]
    while True:
        qid = st.meta()["awaiting"]
        r = core.interview_turn(st, "1" if core.render(qid, "fr").get("options") else "libre")
        if r["done"]:
            break
        seen.append(st.meta()["awaiting"])
    assert seen == core.questionnaire()["modules"][7]["questions"] and all(s.startswith("7.") for s in seen)
    assert "rhythm" in r["message"] and "1.1" not in st.answers()


# ------------------------------------------------------------------ USER.md merge

MINE = "User communicates in English and French." + core.SEP + "User prefers buttons.\nSecond line of the same entry."


def test_compile_keeps_foreign_entries_and_only_adds(st):
    answer(st, "0.1", "Tout")
    answer(st, "1.1", "Alex, FR")
    r = core.compile_user(st, "fr", current=MINE)
    assert r["kept_entries"] == core.split_entries(MINE)
    assert r["merged"][:2] == core.split_entries(MINE) and r["merged"][2].startswith("Identité — Appel: Alex")
    assert all(o["action"] == "add" for o in r["operations"]) and len(r["operations"]) == 2
    assert r["chars"] == len(core.SEP.join(r["merged"])) <= core.USER_LIMIT


def test_compile_is_idempotent_and_replaces_only_its_own_stale_lines(st):
    answer(st, "0.1", "Tout")
    answer(st, "1.1", "Alex, FR")
    first = core.compile_user(st, "fr", current=MINE)
    applied = core.SEP.join(first["merged"])
    assert core.compile_user(st, "fr", current=applied)["operations"] == []
    answer(st, "1.1", "Alexandra, FR")
    ops = core.compile_user(st, "fr", current=applied)["operations"]
    assert ops == [{"action": "remove", "old_text": "Identité — Appel: Alex, FR"},
                   {"action": "add", "content": "Identité — Appel: Alexandra, FR"}]


def test_old_pointer_line_is_replaced_not_duplicated(st):
    answer(st, "0.1", "Tout")
    answer(st, "1.1", "Alex")
    r = core.compile_user(st, "fr", current=MINE + core.SEP + "Détail : profile/")
    assert {"action": "remove", "old_text": "Détail : profile/"} in r["operations"]
    assert sum(1 for l in r["merged"] if l.startswith("Détail")) == 1


def test_existing_entries_count_against_the_budget_first(st):
    answer(st, "0.1", "Tout")
    for qid in ("1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.11", "1.12", "5.1", "5.2", "5.3", "5.4"):
        answer(st, qid, "y" * 60)
    foreign = core.SEP.join("z" * 195 for _ in range(5))            # 987 chars the plugin must not touch
    r = core.compile_user(st, "fr", current=foreign)
    assert r["kept_entries"] == core.split_entries(foreign) and r["chars"] <= core.USER_LIMIT
    assert {"1.1", "1.12"} <= set(r["kept"])                        # the name and the red line go last
    assert {"5.3", "5.4", "1.11"} <= set(r["dropped"])
    assert r["dropped"].index("1.11") < r["dropped"].index("5.4") < r["dropped"].index("5.3")
    assert not any(o["action"] == "remove" for o in r["operations"])
    full = core.SEP.join("z" * 195 for _ in range(7))               # nothing fits: propose nothing
    r = core.compile_user(st, "fr", current=full)
    assert r["lines"] == [] and r["operations"] == [] and r["merged"] == core.split_entries(full)


def test_pointer_line_names_a_real_path_and_stays_short(st, tmp_path, monkeypatch):
    answer(st, "0.1", "Tout")
    answer(st, "1.1", "Alex")
    line = core.compile_user(st, "fr")["lines"][-1]
    assert line.endswith("/profile/") and len(line) <= core.LINE_LIMIT
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert core.compile_user(st, "fr")["lines"][-1] == "Détail du profil Ikigai : ~/ikigai/profile/"
    deep = core.Store(tmp_path / ("d" * 130) / "ikigai")
    answer(deep, "1.1", "Alex")
    assert core.compile_user(deep, "en")["lines"][-1] == "Ikigai profile detail: $HERMES_HOME/plugin-data/ikigai/profile/"


# ------------------------------------------------------------------ slash command and hooks

DESK = {"session_id": "s1", "session_key": "s1", "platform": "desktop"}


def hook(plugin, text, session_id="s1", platform="desktop", **kw):
    return plugin._pre_llm_call(session_id=session_id, user_message=text, platform=platform, **kw)


def test_desktop_start_shows_the_first_question_with_buttons_on_the_next_message(plugin, st):
    out = plugin._cmd("start fr", **DESK)
    assert "::preview" not in out and "[0.1]" not in out and "go" in out      # slash output is plain text
    r = hook(plugin, "go")
    assert r["action"] == "block" and r["message"].startswith("[0.1]") and "::preview{file=" in r["message"]
    assert st.answers() == {}                                                  # "go" is not an answer
    r = hook(plugin, "ikigai:0.1:1")
    assert st.answers()["0.1"]["value"] == "Tout" and "::preview{file=" in r["message"]


def test_other_surfaces_get_the_question_at_once_and_never_a_preview(plugin, st):
    out = plugin._cmd("start fr", session_id="t1", session_key="t1", platform="telegram")
    assert "[0.1]" in out and "1. Tout" in out and "::preview" not in out
    r = hook(plugin, "1", session_id="t1", platform="telegram")
    assert r["message"].startswith("[0.2]") and "::preview" not in r["message"]


def test_running_interview_leaves_other_chats_and_jobs_alone(plugin, st):
    plugin._cmd("start fr", **DESK)
    hook(plugin, "go")
    assert hook(plugin, "résume ce dossier", session_id="other") is None
    assert hook(plugin, "nightly job", session_id="c1", platform="cron", is_first_turn=True) is None
    assert st.meta()["platform"] == "desktop" and not st.meta().get("offered")
    assert hook(plugin, "/ikigai status") is None                              # slash commands pass through
    assert st.answers() == {}


def test_first_session_offer_is_not_spent_on_a_cron_job(plugin, st):
    assert hook(plugin, "nightly job", session_id="c1", platform="cron", is_first_turn=True) is None
    assert not st.meta().get("offered")
    r = hook(plugin, "bonjour", session_id="d1", is_first_turn=True)
    assert "interview" in r["context"] and st.meta()["offered"]


def test_forget_then_resume_asks_the_question_again(plugin, st):
    plugin._cmd("start fr", **DESK)
    hook(plugin, "go")
    hook(plugin, "1")
    hook(plugin, "passer")
    assert st.answers()["0.2"]["skipped"]
    plugin._cmd("pause", **DESK)
    plugin._cmd("forget 0.2", **DESK)
    plugin._cmd("resume", **DESK)
    assert hook(plugin, "go")["message"].startswith("[0.2]")
    hook(plugin, "ikigai:0.2:1,2")
    assert st.answers()["0.2"]["value"] == "Santé, Argent personnel"


def test_compile_command_and_tool_merge_with_the_existing_file(plugin, st):
    import json
    answer(st, "0.1", "Tout")
    answer(st, "1.1", "Alex, FR")
    st.set_meta(lang="fr")
    plugin.user_md = MINE
    out = plugin._cmd("compile", **DESK)
    assert "2 ligne(s) existante(s) conservée(s)" in out and "2 ligne(s) à ajouter, 0 à retirer" in out
    r = json.loads(plugin._t_compile({"lang": "fr"}))
    assert r["kept_entries"] == core.split_entries(MINE) and len(r["operations"]) == 2
    assert "-User communicates" not in r["diff"] and "+Identité — Appel: Alex, FR" in r["diff"]
    plugin.user_md = core.SEP.join(core.split_entries(MINE) + r["user_md_lines"])
    assert "USER.md est déjà à jour" in plugin._cmd("compile", **DESK)
    assert json.loads(plugin._t_compile({"lang": "fr"}))["operations"] == []


def test_stale_interview_expires_and_frees_the_chat(plugin, st):
    plugin._cmd("start fr", **DESK)
    st.set_meta(awaiting_ts=time.time() - 13 * 3600)
    assert hook(plugin, "une vraie question") is None and not st.meta()["active"]


def test_a_second_chat_on_the_same_app_never_takes_the_interview_over(st):
    start(st)
    core.bind_owner(st, "sessA", "sessA", "desktop")
    assert core.interview_turn(st, "résume ce dossier", session_id="sessB", platform="desktop") is None
    assert core.interview_turn(st, "1", session_id="sessA", platform="desktop") is not None
    start(st)                                        # and not even before the owner's first reply
    core.bind_owner(st, "sessC", "sessC", "desktop")
    assert core.interview_turn(st, "autre chose", session_id="sessD", platform="desktop") is None
    assert st.meta()["owner"]["ids"] == ["sessC"]


# ------------------------------------------------------------------ reading the profile back

def test_sheet_names_follow_the_interview_language(st):
    answer(st, "0.1", "Tout")
    answer(st, "1.2", "J'aide des petites entreprises")
    answer(st, "5.1", "4")
    assert set(core.compile_sheets(st, "fr")) == {"travail", "autonomie"}
    assert set(core.compile_sheets(st, "en")) == {"work", "autonomy"}
    out = core.write_compiled(st, "en")                     # switching language leaves no stale file
    assert out["sheets"] == ["autonomy", "work"]
    core.write_compiled(st, "fr")
    assert sorted(p.name for p in (st.root / "profile").glob("*.md")) == ["autonomie.md", "travail.md"]


def test_show_overview_one_sheet_all_and_soul(st):
    assert "/ikigai start" in core.show(st, "", "en")
    answer(st, "0.1", "Everything")
    answer(st, "1.2", "I help small companies")
    st.save("4.1", "Direct and dry", "be blunt with me", "declared")
    st.save("1.9", "", skipped=True)
    over = core.show(st, "", "en")
    assert "3 of 76 answered, 1 skipped" in over and "work (1)" in over and "communication (1)" in over
    assert "/ikigai show <sheet>" in over and "answers.json" in over and "Not in the sheets" not in over
    sheet = core.show(st, "comm", "en")                      # a prefix is enough
    assert sheet.splitlines()[0] == "— communication (1) —"
    assert "[4.1] Tone?" in sheet and "    Direct and dry" in sheet and "« be blunt with me »" in sheet
    assert "I help small companies" not in sheet
    assert "I help small companies" in core.show(st, "all", "en") and "Direct and dry" in core.show(st, "all", "en")
    assert "## Voice" in core.show(st, "soul", "en")
    assert core.show(st, "nope", "en").startswith("Unknown sheet. Sheets: ")
    assert "Fiches : " in core.show(st, "", "fr") and "travail (1)" in core.show(st, "", "fr")


def test_show_keeps_local_and_inferred_answers_out(st):
    answer(st, "0.1", "Everything")
    answer(st, "1.2", "I help small companies")
    answer(st, "5.5", "the client files")                    # local-only question
    st.save("1.8", "4", "", "inferred")
    blob = core.show(st, "all", "en") + core.show(st, "", "en")
    assert "client files" not in blob and "[1.8]" not in blob
    assert "Not in the sheets: 2 answer(s) kept local" in blob


@pytest.mark.parametrize("entry,owned", [
    ("Contexte — Horaires: lundi-vendredi", True), ("Rules — Red line: never send money", True),
    ("Identity — Call: Alex; Role: consultant", True), ("Détail du profil Ikigai : ~/x/profile/", True),
    ("Ikigai profile detail: ~/x/profile/", True), ("Détail : profile/", True),
    ("Context — I work at ACME", False), ("Rules — be nice to the intern", False),
    ("Goals — ship the plugin", False), ("Detail: the server is in Frankfurt", False),
    ("User communicates in English and French.", False),
])
def test_only_lines_shaped_like_ikigais_are_owned(entry, owned):
    assert core.is_managed(entry) is owned


def test_a_foreign_line_with_a_theme_word_is_never_proposed_for_removal(st):
    answer(st, "0.1", "Everything")
    answer(st, "1.1", "Alex")
    mine = "Context — I work at ACME" + core.SEP + "Rules — be nice to the intern"
    r = core.compile_user(st, "en", current=mine)
    assert r["kept_entries"] == core.split_entries(mine)
    assert not any(o["action"] == "remove" for o in r["operations"])


def test_desktop_handshake_says_how_many_questions_and_which_module(plugin, st):
    out = plugin._cmd("start en", **DESK)
    assert "Ready: 15 questions. Send \"go\"" in out and "::preview" not in out
    out = plugin._cmd("more decision", **DESK)
    assert "Ready: 6 questions in module \"Decisions and reasoning\"." in out
    r = hook(plugin, "go")
    assert r["message"].startswith("[6.1]") and "::preview{file=" in r["message"]
    hook(plugin, "ikigai:6.1:1")
    assert st.meta()["awaiting"] == "6.2"                    # stays in the module
    plugin._cmd("start fr", **DESK)
    assert "Prêt : 15 questions. Envoie « go »" in plugin._cmd("start fr", **DESK)


def test_show_command(plugin, st):
    assert "/ikigai start" in plugin._cmd("show", **DESK)
    plugin._cmd("start en", **DESK)
    hook(plugin, "go")
    hook(plugin, "ikigai:0.1:1")
    out = plugin._cmd("show", **DESK)
    assert "1 of 76 answered" in out and "autonomy (1)" in out
    assert "Everything" in plugin._cmd("show autonomy", **DESK)
    assert st.meta()["active"] and st.meta()["awaiting"] == "0.2"      # reading does not disturb the interview


def test_a_bare_option_number_is_not_shown_as_a_quote(st):
    answer(st, "0.1", "Everything")
    st.save("2.6", "What I can be paid for", "4", "declared")          # as an older version stored a click
    st.save("4.1", "Direct and dry", "1, 2", "declared")
    out = core.show(st, "all", "en") + "".join(core.compile_sheets(st, "en").values())
    assert "« 4 »" not in out and "> 4" not in out and "« 1, 2 »" not in out
    assert "What I can be paid for" in out


# ------------------------------------------------------------------ packaging and file safety

def test_version_is_the_same_in_the_manifest_and_the_code(plugin):
    import re
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert re.search(r"^version: (\S+)$", manifest, re.M).group(1) == plugin.__version__
    assert re.search(r'^requires_hermes: ">=0\.22\.6"$', manifest, re.M)     # the block veto the interview needs


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX permission bits")
def test_every_file_is_owner_only_whatever_the_umask(st):
    import os
    old = os.umask(0o002)
    try:
        st.set_meta(platform="desktop", lang="en")
        answer(st, "0.1", "Everything")
        st.save("4.1", "Direct and dry", "be blunt", "declared")
        core.write_compiled(st, "en")
        core.format_question(core.render("0.2", "en"), False, st)
        loose = st.root / "widgets" / "q_1_1_en.html"             # as an older version left it
        loose.write_text("old", encoding="utf-8")
        os.chmod(loose, 0o664)
        core.format_question(core.render("1.1", "en"), False, st)
    finally:
        os.umask(old)
    files = [p for p in st.root.rglob("*") if p.is_file()]
    assert len(files) >= 6 and {p.suffix for p in files} >= {".json", ".md", ".html"}
    assert [str(p) for p in files if p.stat().st_mode & 0o077] == []
    assert [str(p) for p in st.root.rglob("*") if p.is_dir() and p.stat().st_mode & 0o077] == []
    assert not list(st.root.rglob("*.tmp"))


def test_a_widget_that_cannot_be_written_leaves_the_question_answerable(st, monkeypatch):
    st.set_meta(platform="desktop")

    def boom(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(core, "_write_text", boom)
    out = core.format_question(core.render("0.1", "fr"), True, st)
    assert "1. Tout" in out and "::preview" not in out


def test_show_tolerates_the_help_line_and_both_languages(st):
    answer(st, "0.1", "Everything")
    answer(st, "1.2", "I help small companies")
    for arg in ("[sheet|all|soul]", "sheet|all|soul", "[fiche|all|soul]", ""):
        assert core.show(st, arg, "en").startswith("Ikigai profile: 2 of 76 answered"), arg
    assert core.show(st, "travail", "en").startswith("— work (1) —")       # French name, English profile
    assert core.show(st, "work", "fr").startswith("— travail (1) —")       # and the other way round
