"""French / English interface (bonus): same logic, two wordings."""
import re
import string

from app import i18n, lifecycle as L, schema as S
from app.extractors.fake import FakeExtractor
from tests.conftest import Rig, photo_bytes
from tests.test_conversation import ident
from tests.test_linking import cover


def placeholders(text):
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name)


def test_every_message_exists_in_both_languages_with_the_same_placeholders():
    for key, (en, fr) in i18n.M.items():
        assert isinstance(en, str) and isinstance(fr, str), key
        assert (en == "") == (fr == ""), key
        assert placeholders(en) == placeholders(fr), key


def test_every_field_has_a_distinct_french_name_on_its_page():
    for sec in S.SECTIONS:
        names = [i18n.field_title("fr", sec.key, f) for f in sec.fields]
        assert all(names) and len(set(names)) == len(names), (sec.key, [n for n in names if names.count(n) > 1])
        assert i18n.section_title("fr", sec) == sec.title_fr and i18n.section_title("en", sec) == sec.en
        for g in sec.groups:
            for slot, _, _ in g.slots:
                for f in g.fields:
                    path = f"{g.name}[{slot}].{f.key}"
                    assert i18n.path_title("en", sec.key, path) == S.path_title(sec.key, path)
                    assert i18n.path_title("fr", sec.key, path).startswith(i18n.group_title("fr", g))
    assert i18n.path_title("fr", "pregnancy", "visits[t1_v2].weight_kg") == "Consultation prénatale 1er trimestre, visite 2 - Poids (kg)"
    assert i18n.field_title("fr", "identification", S.IDENTIFICATION.field("family_diabetes_husband")) == \
        "Antécédents familiaux - Diabète (mari / famille)"
    assert i18n.field_title("fr", "identification", S.IDENTIFICATION.field("abortion_date")) == "Avortement - date"


def test_notes_values_and_reasons_are_translated_only_for_display():
    assert i18n.note("fr", "low confidence; the value is outside the expected range") == \
        "confiance faible; la valeur est en dehors de la plage attendue"
    assert i18n.note("fr", "I could not interpret this as a date") == "je n'ai pas pu interpréter ceci comme une date"
    assert i18n.note("en", "low confidence") == "low confidence" and i18n.note("fr", "something new") == "something new"
    f = S.IDENTIFICATION.field("consanguinity")
    assert (i18n.value("fr", f, S.TICKED), i18n.value("en", f, S.TICKED)) == ("cochée", "ticked")
    assert i18n.value("fr", S.COVER.field("coverage_mode"), "Fixe") == "Fixe"          # printed options stay as printed
    assert i18n.reasons("fr", ["same code", "same province"]) == "même code, même province"


# English sentences that must never appear once the midwife chose French.
ENGLISH = re.compile(r"\b(the|is|are|you|your|this|that|what|which|page saved|please|registry|not sure|"
                     r"confidence|blank|ticked|none|tap|send|photo of|written|saved|nothing|done)\b", re.I)


def french_only(rig, since=0):
    for m in rig.store.messages(rig.mw["id"], since):
        if m["sender"] != "bot":
            continue
        text = re.sub(r"“[^”]*”|« [^»]* »|\([^)]*\)", " ", m["text"])       # values and printed labels are data
        assert not ENGLISH.search(text), m["text"]
        for b in m["buttons"]:
            if not b["payload"].startswith("opt:") or b["payload"] in ("opt:0", "opt:1", "opt:none"):
                assert not ENGLISH.search(b["label"]), b["label"]


def press(rig, payload_prefix):
    b = next(b for b in rig.prompt()["buttons"] if b["payload"].startswith(payload_prefix))
    return rig.say(b["payload"])


def answer_followups(rig):
    """Answer every follow-up with the buttons (payloads are the same in both languages)."""
    for _ in range(300):
        payloads = [b["payload"] for b in rig.prompt()["buttons"]]
        if "menu:confirm" in payloads:
            return
        if "menu:followups" in payloads:
            rig.say("menu:followups")
        elif "dupsec:new" in payloads:
            rig.say("dupsec:new")
        else:
            rig.say("ans:ok" if "ans:ok" in payloads else "slot:none" if "slot:none" in payloads else "ans:blank")
    raise AssertionError("follow-ups did not finish")


def test_whole_flow_in_french(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111", 0.6), ident()]))
    assert rig.agent.lang(rig.mw["id"]) == "en"
    out = rig.say("français")
    assert out[-1]["text"].startswith("Langue : français") and rig.agent.lang(rig.mw["id"]) == "fr"
    rig.say("aide"); rig.say("statut"); rig.say("historique")
    rig.agent.handle_image(rig.mw, photo_bytes("blurred"))                 # quality question, in French
    assert "elle semble floue" in rig.last()["text"]
    press(rig, "q:keep")
    rig.photo(2)
    rid = rig.rid()
    assert "Page 2 enregistrée sur ce téléphone (chiffrée)" in rig.last()["text"]
    rig.net.online = False
    press(rig, "cmd:done")
    assert "En attente de traitement par l'IA* (pas de connexion)" in rig.last()["text"]
    rig.net.online = True
    rig.tick()
    summary = next(m["text"] for m in rig.store.messages(rig.mw["id"]) if "voici ce que j'ai lu" in m["text"])
    assert "Fiche de surveillance (couverture)" in summary and "à vérifier" in summary
    first = rig.prompt()["text"]                                             # the linking code: always confirmed
    assert "N° de la fiche / code" in first and "J'ai lu « AAA-111 »" in first and "confiance 60%" in first
    answer_followups(rig)
    assert [b["label"] for b in rig.prompt()["buttons"]][0] == "✅ Confirmer"
    press(rig, "menu:all"); press(rig, "menu:edit"); press(rig, "sec:identification")
    assert "Tapez le NUMÉRO du champ à modifier" in rig.last()["text"] and "Tapez *ajouter*" in rig.last()["text"]
    assert "1. Age: 29" in rig.last()["text"] and "Consanguinité: non cochée" in rig.last()["text"]
    rig.say("1")                                                             # edit "Age"
    assert "Valeur actuelle : « 29 ans »" in rig.last()["text"] and "Tapez le nombre (ans)." in rig.last()["text"]
    rig.say("abc")
    assert "Je n'ai pas compris cette réponse comme un nombre entier" in rig.last()["text"]
    rig.say("99")
    assert "en dehors de la plage" in rig.last()["text"]
    press(rig, "ans:retype"); rig.say("31")
    press(rig, "menu:confirm")
    assert rig.state(rid) == L.VALIDATED
    assert "Aucune patiente enregistrée ne ressemble à ce registre (code AAA-111)" in rig.last()["text"]
    press(rig, "link:new")
    assert "✅ Enregistré. Cette patiente a maintenant 1 registre(s)" in rig.last()["text"]
    rig.tick()
    assert "synchronisé avec le serveur" in rig.last()["text"]
    rig.say("statut"); rig.say("historique")
    assert "Patiente 1" in rig.last()["text"] and "âge 31" in rig.last()["text"]
    french_only(rig)
    # back to English: later messages are English again, earlier ones are not rewritten
    n = rig.store.last_message_id(rig.mw["id"])
    assert rig.say("english")[-1]["text"].startswith("Language: English")
    rig.say("status")
    assert "Connection: online" in rig.last()["text"]
    assert all("Connexion" not in m["text"] for m in rig.store.messages(rig.mw["id"], n))
    rig.store.close()


def test_french_duplicate_and_matching_dialogue(tmp_path):
    second = cover("AAA-111"); second["fields"]["province"] = ["Other", "KNOWN", 0.99]
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111"), second]))
    rig.say("lang:fr")
    rig.capture(1); rig.tick()
    answer_followups(rig)
    press(rig, "menu:confirm"); press(rig, "link:new")
    rig.capture(2); rig.tick()                                               # the same code again: possible duplicate
    assert "Doublon possible" in rig.prompt()["text"] and "« AAA-111 »" in rig.prompt()["text"]
    press(rig, "dup:update")
    assert "diffèrent entre le registre enregistré et la nouvelle photo" in rig.prompt()["text"]
    assert "Province : enregistré “Prov” → nouveau “Other”" in rig.prompt()["text"]
    answer_followups(rig)
    press(rig, "menu:confirm")
    assert "À quelle femme correspond ce registre" in rig.last()["text"]
    assert "pourquoi : même code" in rig.last()["text"]
    assert [b["label"] for b in rig.last()["buttons"]] == ["Patiente 1", "Aucune, créer une nouvelle", "Je ne suis pas sûre"]
    press(rig, "link:unsure")
    assert "vérification manuelle requise" in rig.last()["text"]
    french_only(rig)
    rig.store.close()
