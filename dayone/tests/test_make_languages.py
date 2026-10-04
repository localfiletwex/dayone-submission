"""Synthetic English / Arabic pages (eval/make_languages.py): the translation of what is
written and the reference values that go with it."""
import json

import pytest

from app import schema as S
from app.normalize import canon
from eval import make_languages as M
from eval.paths import GT_PATH


def test_words_are_translated_and_values_keep_their_meaning():
    assert M.translate("Neg", S.TEXT, "en", False) == ("Neg", True)
    assert M.translate("Neg", S.TEXT, "ar", False) == ("سلبي", True)
    assert M.translate("Voiebasse", S.TEXT, "en", False) == ("Vaginal delivery", True)       # the dataset dropped a space
    assert M.translate("N\ufffdant", S.TEXT, "ar", False) == ("لا شيء", True)                 # ... or a glyph
    assert M.translate("7 jours", S.TEXT, "en", False) == ("7 days", True)
    assert M.translate("44 jours", S.TEXT, "ar", True) == ("٤٤ يوما", True)
    assert M.translate("Kénitra", S.TEXT, "en", False) == ("Kénitra", True)                   # proper nouns stay in English
    assert M.translate("Kénitra", S.TEXT, "ar", False) == ("القنيطرة", True)
    assert M.translate("2026-823-001", S.TEXT, "ar", True) == ("2026-823-001", True)          # a registry code is not rewritten
    assert M.translate("something else", S.TEXT, "en", False) == ("something else", False)
    # numbers, dates and blood pressure: same value, other digits only for the "Arabic digits" patients
    assert M.translate("28/08/2026", S.DATE, "ar", False) == ("28/08/2026", True)
    assert M.translate("28/08/2026", S.DATE, "ar", True) == ("٢٨/٠٨/٢٠٢٦", True)
    assert M.translate("120/63", S.BP, "en", True) == ("120/63", True)
    f = S.F("x", "X", S.DATE)
    assert canon(f, "٢٨/٠٨/٢٠٢٦") == canon(f, "28/08/2026") == ("2026-08-28", True)


@pytest.mark.skipif(not GT_PATH.exists(), reason="ground truth not built (python -m eval.build_ground_truth)")
def test_every_handwritten_word_of_the_dataset_has_a_translation():
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    cells = [(f, c) for page in gt.values() for f, c in M._cells(page) if c.get("mark") == "handwritten"]
    assert len(cells) > 1700
    for lang in M.LANGS:
        left = {c["raw"] for f, c in cells if not M.translate(str(c["raw"]), f.kind, lang, True)[1]}
        assert left == set(), left


@pytest.mark.skipif(not GT_PATH.with_name("gt-ar.json").exists(), reason="language pages not generated")
def test_generated_reference_values_match_the_french_ones_field_by_field():
    base = json.loads(GT_PATH.read_text(encoding="utf-8"))
    for lang in M.LANGS:
        gt = json.loads(GT_PATH.with_name(f"gt-{lang}.json").read_text(encoding="utf-8"))
        assert set(gt) == set(base)
        for key, page in gt.items():
            for (f, new), (_, old) in zip(M._cells(page), M._cells(base[key])):
                assert new["status"] == old["status"] and "_chars" not in new
                if old.get("mark") != "handwritten":
                    assert new == old                                   # checkboxes, blanks: untouched
                elif f.kind != S.TEXT and old.get("typed"):
                    assert new["value"] == old["value"], (key, f.key)   # same number, date or blood pressure
        arabic = [c["raw"] for f, c in M._cells(gt["43"]) if c.get("language")]    # patient 6: Arabic digits
        assert (lang == "ar") == any("٠" <= ch <= "٩" for raw in arabic for ch in raw)
