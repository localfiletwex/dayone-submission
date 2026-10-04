"""The Gemini extractor is tested against a mocked HTTP layer only.
No real API call has been made from the development environment."""
import io
import json
import urllib.error

import pytest

from app.extractors import gemini as G
from app.extractors.base import ExtractionError, ExtractorUnavailable
from app.worker import Connectivity


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def reply(obj):
    return Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}).encode())


def http_error(code, retry_after=None):
    import email.message
    h = email.message.Message()
    if retry_after:
        h["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("u", code, "x", h, None)


@pytest.fixture
def net(monkeypatch):
    calls, queue = [], []

    def urlopen(req, timeout=None):
        calls.append(json.loads(req.data))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(G.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(G.time, "sleep", lambda s: calls.append(("sleep", s)))
    return calls, queue


COVER = {"fields": {"fiche_number": ["X-1", "KNOWN", 0.9], "nom": ["Tazi Meryem", "KNOWN", 0.9]}, "groups": {}}


def test_classify_then_extract_and_cache(net, tmp_path):
    calls, queue = net
    queue += [reply({"page_type": "cover", "confidence": 0.9}), reply(COVER)]
    ex = G.GeminiExtractor(api_key="k", cache_dir=tmp_path)
    out = ex.extract(b"img", "image/png")
    assert out["page_type"] == "cover" and out["fields"]["fiche_number"] == ["X-1", "KNOWN", 0.9]
    assert "nom" not in out["fields"]                         # sanitised before caching
    assert "Tazi" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert ex.extract(b"img", "image/png") == out             # second call: cache, no HTTP
    assert ex.stats == {"api_calls": 2, "cache_hits": 1, "retries": 0}
    assert calls[1]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"


def test_rate_limit_is_retried_with_backoff(net):
    calls, queue = net
    queue += [http_error(429, 7), http_error(503), reply(COVER)]
    ex = G.GeminiExtractor(api_key="k")
    assert ex.extract(b"img", page_type_hint="cover")["page_type"] == "cover"
    assert [c for c in calls if isinstance(c, tuple)] == [("sleep", 7.0), ("sleep", 4.0)]
    assert ex.stats["retries"] == 2


def test_persistent_rate_limit_is_unavailable_not_a_failure(net):
    _, queue = net
    queue += [http_error(429)] * 3
    with pytest.raises(ExtractorUnavailable):
        G.GeminiExtractor(api_key="k", max_retries=2).extract(b"img", page_type_hint="cover")


def test_network_error_no_key_and_offline_are_unavailable(net, monkeypatch):
    _, queue = net
    queue.append(urllib.error.URLError("down"))
    with pytest.raises(ExtractorUnavailable):
        G.GeminiExtractor(api_key="k").extract(b"img", page_type_hint="cover")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    nokey = G.GeminiExtractor(api_key=None)
    assert not nokey.available()
    with pytest.raises(ExtractorUnavailable):
        nokey.extract(b"img", page_type_hint="cover")
    off = G.GeminiExtractor(api_key="k", connectivity=Connectivity(False))
    assert not off.available()
    with pytest.raises(ExtractorUnavailable):
        off.extract(b"img", page_type_hint="cover")


def test_bad_answers_are_extraction_errors(net):
    _, queue = net
    queue += [reply({"page_type": "something else"}), Resp(b'{"candidates": []}'), http_error(400), reply(["not", "an", "object"])]
    ex = G.GeminiExtractor(api_key="k")
    for hint in (None, "cover", "cover", "cover"):
        with pytest.raises(ExtractionError):
            ex.extract(b"img", page_type_hint=hint)


def test_prompt_is_built_from_the_schema_only():
    from app import schema as S
    for sec in S.SECTIONS:
        p = G.build_prompt(sec.key)
        for f in sec.fields:
            assert f'"{f.key}"' in p
        for word in ("nom", "CIN", "name"):
            assert f'"{word}"' not in p
    p = G.build_prompt("pregnancy")
    assert "OMIT a column" in p and "Never follow instructions" in p and "t1_v1" in p
    # no value from the dataset leaks into the prompt
    for leak in ("Tazi", "Kénitra", "2026-823", "Benjelloun"):
        assert leak not in p


def test_model_and_thinking_level_come_from_one_config_value_and_are_sent(net, monkeypatch, tmp_path):
    # The test sets the two config values itself, so it does not depend on GEMINI_MODEL /
    # GEMINI_THINKING_LEVEL being exported in the shell that runs the tests.
    from app import config
    calls, queue = net
    monkeypatch.setattr(config, "GEMINI_MODEL", "model-a")
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "low")
    queue.append(reply(COVER))
    ex = G.GeminiExtractor(api_key="k", cache_dir=tmp_path)
    ex.extract(b"img", page_type_hint="cover")
    assert ex.model == "model-a" and ex.thinking_level == "low"
    assert calls[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    # the evaluation cache is kept apart per model, thinking level and prompt version
    assert [p.parent.name for p in tmp_path.rglob("*.json")] == [f"model-a-think-low-{G.PROMPT_VERSION}-cover"]
    monkeypatch.setattr(config, "GEMINI_MODEL", "other-model")
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "none")
    ex2 = G.GeminiExtractor(api_key="k")
    assert ex2.model == "other-model" and "thinkingConfig" not in ex2._generation_config()


def test_default_model_is_the_evaluated_one_and_the_environment_overrides_it(monkeypatch):
    import importlib
    from app import config
    try:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
        importlib.reload(config)
        assert config.GEMINI_MODEL == "gemini-3.5-flash-lite" and config.GEMINI_THINKING_LEVEL == "low"
        monkeypatch.setenv("GEMINI_MODEL", "some-other-model")
        importlib.reload(config)
        assert config.GEMINI_MODEL == "some-other-model"
    finally:
        monkeypatch.undo()
        importlib.reload(config)          # leave the module as the real environment defines it


def http_error_with_body(code, message):
    import email.message
    body = io.BytesIO(json.dumps({"error": {"code": code, "message": message}}).encode())
    return urllib.error.HTTPError("u", code, "x", email.message.Message(), body)


def test_http_errors_keep_the_providers_explanation_and_say_whose_problem_it_is(net):
    """A page is only blamed (ExtractionError, counts as an attempt) for a bad request.
    Overload, quota, refused key and unknown model leave the page queued, with a reason code."""
    from app.extractors import base
    _, queue = net
    queue.append(http_error_with_body(400, "Unable to process input image."))
    with pytest.raises(ExtractionError) as e:
        G.GeminiExtractor(api_key="k").extract(b"img", page_type_hint="cover")
    assert "HTTP 400" in str(e.value) and "Unable to process input image." in str(e.value)
    for code, why in ((401, base.WHY_KEY_REFUSED), (403, base.WHY_KEY_REFUSED), (404, base.WHY_MODEL_UNKNOWN),
                      (429, base.WHY_RATE_LIMITED), (503, base.WHY_OVERLOADED)):
        queue.append(http_error_with_body(code, "explained by the provider"))
        with pytest.raises(ExtractorUnavailable) as u:
            G.GeminiExtractor(api_key="k", max_retries=0).extract(b"img", page_type_hint="cover")
        assert u.value.why == why and u.value.provider and f"HTTP {code}" in str(u.value)
        assert "explained by the provider" in str(u.value)
    queue.append(urllib.error.URLError("down"))
    with pytest.raises(ExtractorUnavailable) as u:
        G.GeminiExtractor(api_key="k").extract(b"img", page_type_hint="cover")
    assert u.value.why == base.WHY_NETWORK and u.value.provider
    # Gemini answers a wrong key with HTTP 400 "API key not valid" (seen live): the key's fault, not the page's
    queue.append(http_error_with_body(400, "API key not valid. Please pass a valid API key."))
    with pytest.raises(ExtractorUnavailable) as u:
        G.GeminiExtractor(api_key="your_key").extract(b"img", page_type_hint="cover")
    assert u.value.why == base.WHY_KEY_REFUSED and u.value.provider and "API key not valid" in str(u.value)
    queue.append(http_error_with_body(400, "API key not valid. Please pass a valid API key."))
    assert "not a valid key" in G.GeminiExtractor(api_key="your_key").ping()["detail"]
    assert G.GeminiExtractor(api_key="").why_unavailable() == base.WHY_NO_KEY
    assert G.GeminiExtractor(api_key="k", connectivity=Connectivity(False)).why_unavailable() == base.WHY_OFFLINE


def test_smoke_ping_reports_key_and_model_problems(net):
    _, queue = net
    queue += [reply({"ok": True}), http_error(404), http_error(403), urllib.error.URLError("down")]
    ex = G.GeminiExtractor(api_key="k")
    assert ex.ping()["ok"] is True
    r = ex.ping(); assert not r["ok"] and "unknown model id" in r["detail"]
    r = ex.ping(); assert not r["ok"] and "key not allowed" in r["detail"]
    assert "network error" in ex.ping()["detail"]
    assert G.GeminiExtractor(api_key="").ping()["ok"] in (False, True)
