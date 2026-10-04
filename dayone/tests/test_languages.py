"""Entries written in English or Arabic (app/languages.py and its hooks)."""
from app import languages as LG, schema as S
from app.extractors.gemini import build_prompt
from app.normalize import canon
from app.pipeline import postprocess
from app.redaction import MASK, scrub


def field(kind):
    return S.F("x", "X", kind)


def test_arabic_script_digits_are_typed_like_western_ones():
    assert LG.western_digits("٣٦٢٦ g") == "3626 g"
    assert LG.western_digits("۱۲") == "12"                      # Persian/Urdu variants
    assert LG.western_digits(None) is None
    assert canon(field(S.INT), "٣٦٢٦") == (3626, True)
    assert canon(field(S.NUM), "٣٧٫٥") == (37.5, True)          # Arabic decimal separator
    assert canon(field(S.BP), "١٢٠/٨٠") == ("120/80", True)
    assert canon(field(S.DATE), "١٢/٠٥/٢٠٢٢") == ("2022-05-12", True)
    assert canon(field(S.INT), "3626") == (3626, True)           # French input is unchanged


def test_dates_with_month_names_in_three_languages():
    for text in ("12 mai 2022", "12 Mai 2022", "12 May 2022", "May 12, 2022", "12th May 2022",
                 "12 ماي 2022", "١٢ مايو ٢٠٢٢", "12-mai-2022"):
        assert canon(field(S.DATE), text) == ("2022-05-12", True), text
    assert canon(field(S.DATE), "1er août 2021") == ("2021-08-01", True)
    assert canon(field(S.DATE), "3 دجنبر 2021") == ("2021-12-03", True)
    # a numeric date is always day/month/year, and nonsense is kept verbatim and flagged
    assert canon(field(S.DATE), "03/04/2022") == ("2022-04-03", True)
    assert canon(field(S.DATE), "12 blah 2022") == ("12 blah 2022", False)
    assert canon(field(S.DATE), "32 mai 2022")[1] is False


def test_unknown_written_in_english_or_arabic_becomes_status_unknown():
    assert {"inconnu", "unknown", "غير معروف", "not known"} <= set(S.UNKNOWN_MARKS)
    sec = next(s for s in S.SECTIONS if any(f.kind == S.INT for f in s.fields))
    key = next(f.key for f in sec.fields if f.kind == S.INT)
    for written in ("غير معروف", "Not known", "؟", "inconnu"):
        _, section, _ = postprocess({"page_type": sec.key, "fields": {key: [written, "KNOWN", 0.9]}, "groups": {}}, 0.5)
        assert section["fields"][key]["status"] == S.UNKNOWN, written
        assert section["fields"][key]["value"] is None


def test_identifiers_in_arabic_or_english_are_scrubbed():
    clean, kinds = scrub("tel ٠٦٠٠٧٦١٣٤٨")                      # phone in Arabic-script digits
    assert "phone" in kinds and "0600761348" not in clean and "٠٦" not in clean
    clean, kinds = scrub("vue par الدكتورة فاطمة الزهراء")
    assert "person_name" in kinds and "فاطمة" not in clean and "الدكتورة" in clean and MASK in clean
    clean, kinds = scrub("seen by Nurse Jane Smith")
    assert "person_name" in kinds and "Jane" not in clean and "Nurse" in clean
    clean, kinds = scrub("lives at 12 Hassan Road")
    assert "address" in kinds and "Hassan" not in clean
    clean, kinds = scrub("شارع محمد الخامس")
    assert "address" in kinds and "محمد" not in clean
    assert scrub("poids 3626 g, TA 120/80") == ("poids 3626 g, TA 120/80", [])      # clinical text untouched
    assert scrub("حي") == ("حي", [])                                                # "alive" is not an address


def test_language_rules_in_the_prompt_are_optional_and_off_by_default(monkeypatch, tmp_path):
    """Measured: telling the model about Arabic and English lowered its accuracy on French,
    so the prompt only does so when asked (DAYONE_PROMPT_LANGUAGES=1)."""
    from app import config
    from app.extractors import gemini as G
    assert config.PROMPT_LANGUAGE_RULES is False and G.prompt_version() == "v1"
    p = build_prompt(S.SECTIONS[0].key)
    assert "Arabic" not in p and "right to left" not in p and '("?", "inconnu"). value = null' in p
    assert "<LANGUAGES>" not in p and "<UNKNOWN>" not in p and "Absolute rules:\n- Everything written" in p
    monkeypatch.setattr(config, "PROMPT_LANGUAGE_RULES", True)
    p = build_prompt(S.SECTIONS[0].key)
    assert G.prompt_version() == "v2"
    assert "French, Arabic or English" in p and "never translate" in p and "غير معروف" in p
    # the two prompts never share evaluation cache entries
    ex = G.GeminiExtractor(api_key="k", model="m", cache_dir=tmp_path)
    monkeypatch.setattr(ex, "classify", lambda image, mime: "cover")
    monkeypatch.setattr(ex, "_call", lambda prompt, image, mime: {"fields": {}, "groups": {}})
    ex.extract(b"img")
    monkeypatch.setattr(config, "PROMPT_LANGUAGE_RULES", False)
    ex.extract(b"img")
    assert sorted(d.name for d in tmp_path.iterdir()) == ["m-think-low-v1-auto", "m-think-low-v2-auto"]


def test_optional_stricter_threshold_applies_to_arabic_script_values_only(monkeypatch, tmp_path):
    from app import config
    text = S.F("x", "X", S.TEXT)
    number = S.F("n", "N", S.INT)
    ask = lambda f, v, **kw: pipeline_finalize(f, [v, "KNOWN", 0.90], 0.5, **kw)["status"]      # noqa: E731
    from app.pipeline import finalize as pipeline_finalize
    assert ask(text, "سلبي") == S.KNOWN and ask(number, "36") == S.KNOWN                      # one threshold: nothing asked
    on_arabic_page = {"threshold_arabic": 0.92, "arabic": True}
    assert ask(number, "36", **on_arabic_page) == S.NEEDS_REVIEW                              # numbers of an Arabic page: asked
    assert ask(text, "سلبي", **on_arabic_page) == S.KNOWN                                     # words: not, by default
    assert ask(text, "سلبي", arabic_scope="all", **on_arabic_page) == S.NEEDS_REVIEW          # ... unless every field is covered
    assert ask(number, "36", threshold_arabic=0.92, arabic=False) == S.KNOWN                  # another page: unchanged
    assert pipeline_finalize(number, ["36", "KNOWN", 0.95], 0.5, **on_arabic_page)["status"] == S.KNOWN
    # through the whole pipeline, twice (the extractor cleans the answer once, the pipeline again):
    # Arabic-script DIGITS are normalised on the way, and must still count as Arabic
    from app import pipeline
    raw = {"page_type": "pregnancy", "fields": {"height_cm": ["١٦٤", "KNOWN", 0.90], "lmp_date": ["21/11/2025", "KNOWN", 0.90]},
           "groups": {"visits": {"t1_v1": {"weight_kg": ["٧٣٫٧", "KNOWN", 0.90], "edema": ["لا", "KNOWN", 0.90],
                                           "blood_pressure": ["109/61", "KNOWN", 0.90]}}}}
    once = pipeline.sanitize_raw(raw)
    assert once["arabic"] == ["height_cm", "visits[t1_v1].edema", "visits[t1_v1].weight_kg"] and once["fields"]["height_cm"][0] == "164"
    for threshold_arabic, expect in ((None, S.KNOWN), (0.92, S.NEEDS_REVIEW)):
        _, section, _ = pipeline.postprocess(once, 0.5, threshold_arabic)
        visit = section["groups"]["visits"][0]["fields"]
        assert section["fields"]["height_cm"]["status"] == expect and section["fields"]["height_cm"]["value"] == 164
        assert visit["weight_kg"]["status"] == expect and visit["edema"]["status"] == S.KNOWN     # a word: not covered
        # every NUMBER of the page is held to the stricter threshold: the model returns Arabic-script
        # digits as plain numbers, so a misread number cannot be recognised by its own script
        assert section["fields"]["lmp_date"]["status"] == expect and visit["blood_pressure"]["status"] == expect
    _, section, _ = pipeline.postprocess(once, 0.5, 0.92, "all")
    assert section["groups"]["visits"][0]["fields"]["edema"]["status"] == S.NEEDS_REVIEW
    # what the model actually sends for a page written with Arabic digits: numbers, and Arabic words
    as_sent = {"page_type": "pregnancy", "fields": {"height_cm": [164, "KNOWN", 0.90]},
               "groups": {"visits": {"t1_v1": {"edema": ["لا", "KNOWN", 0.95], "weight_kg": [73.7, "KNOWN", 0.90]}}}}
    _, section, _ = pipeline.postprocess(as_sent, 0.5, 0.92)
    assert section["fields"]["height_cm"]["status"] == S.NEEDS_REVIEW
    assert section["groups"]["visits"][0]["fields"]["edema"]["status"] == S.KNOWN            # 0.95 is above 0.92
    french = {"page_type": "pregnancy", "fields": {"height_cm": [164, "KNOWN", 0.90]},
              "groups": {"visits": {"t1_v1": {"edema": ["Non", "KNOWN", 0.90]}}}}
    _, section, _ = pipeline.postprocess(french, 0.5, 0.92)                                    # a French page: unchanged
    assert section["fields"]["height_cm"]["status"] == S.KNOWN and section["groups"]["visits"][0]["fields"]["edema"]["status"] == S.KNOWN
    # where it comes from: the threshold file, or the environment
    f = tmp_path / "threshold.json"
    monkeypatch.setattr(config, "THRESHOLD_FILE", f)
    assert config.load_threshold()["arabic"] is None
    f.write_text('{"threshold": 0.5, "model": "m", "threshold_arabic": 0.92}')
    assert config.load_threshold() == {"value": 0.5, "calibrated": True, "source": str(f), "model": "m",
                                       "arabic": 0.92, "arabic_scope": "numbers"}
    f.write_text('{"threshold": 0.5, "model": "m", "threshold_arabic": 0.92, "threshold_arabic_scope": "all"}')
    assert config.load_threshold()["arabic_scope"] == "all"
    monkeypatch.setenv("DAYONE_THRESHOLD_ARABIC", "0.8")
    assert config.load_threshold()["arabic"] == 0.8
