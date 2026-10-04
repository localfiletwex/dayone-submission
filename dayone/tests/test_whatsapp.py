"""WhatsApp connector (bonus), against a SIMULATED Graph API.
It has not been run against the real WhatsApp service."""
import hashlib
import hmac
import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import lifecycle as L, whatsapp as W
from app.extractors.fake import FakeExtractor
from tests.conftest import Rig, photo_bytes
from tests.test_linking import cover

NUMBER = "212600000001"


class Graph:
    """Stands in for graph.facebook.com: records what is sent, serves one media file."""

    def __init__(self):
        self.sent, self.fail, self.media = [], False, photo_bytes("sharp")

    def __call__(self, method, url, headers, body=None, timeout=20.0):
        assert headers["Authorization"] == "Bearer TOKEN"
        if method == "POST":
            if self.fail == "network":
                raise OSError("down")
            if self.fail:
                return 500, b'{"error":{"message":"temporary"}}'
            assert url == "https://graph.facebook.com/v25.0/PHONE/messages"
            self.sent.append(json.loads(body))
            return 200, b"{}"
        if url.endswith("/MEDIA1"):
            return 200, json.dumps({"url": "https://lookaside.example/file", "mime_type": "image/jpeg"}).encode()
        if url == "https://lookaside.example/file":
            return 200, self.media
        return 404, b"{}"

    def texts(self):
        return [m["text"]["body"] if m["type"] == "text" else m["interactive"]["body"]["text"] for m in self.sent]


@pytest.fixture
def wa(tmp_path):
    rig = Rig(tmp_path / "d", FakeExtractor(script=[cover("AAA-111", 0.6)]))
    graph = Graph()
    device = SimpleNamespace(store=rig.store, agent=rig.agent, connectivity=rig.net)
    bridge = W.WhatsAppBridge(device, "TOKEN", "PHONE", "VERIFY", {NUMBER: "midwife1"}, http=graph)
    yield rig, bridge, graph
    rig.store.close()


def incoming(kind="text", body="help", mid="wamid.1", sender=NUMBER, **extra):
    msg = {"from": sender, "id": mid, "type": kind}
    if kind == "text":
        msg["text"] = {"body": body}
    msg.update(extra)
    return {"entry": [{"changes": [{"value": {"messaging_product": "whatsapp", "messages": [msg]}}]}]}


def tap(payload, mid, kind="button_reply"):
    return incoming("interactive", mid=mid, interactive={"type": kind, kind: {"id": payload, "title": "x"}})


def test_rendering_respects_whatsapp_limits():
    b = lambda label, payload: {"label": label, "payload": payload}          # noqa: E731
    assert W.render("hello", []) == ([{"type": "text", "text": {"body": "hello"}}], [])
    msgs, opts = W.render("ok?", [b("Yes, keep it", "ans:keep"), b("No, I will retype", "ans:retype")])
    assert opts == [] and msgs[0]["interactive"]["type"] == "button"
    assert [x["reply"] for x in msgs[0]["interactive"]["action"]["buttons"]] == [
        {"id": "ans:keep", "title": "Yes, keep it"}, {"id": "ans:retype", "title": "No, I will retype"}]
    five = [b(f"A rather long label for option number {i}", f"opt:{i}") for i in range(5)]
    msgs, opts = W.render("which?", five, "fr")
    rows = msgs[0]["interactive"]["action"]["sections"][0]["rows"]
    assert msgs[0]["interactive"]["type"] == "list" and msgs[0]["interactive"]["action"]["button"] == "Choisir"
    assert [r["id"] for r in rows] == [f"opt:{i}" for i in range(5)] and len({r["title"] for r in rows}) == 5
    assert all(len(r["title"]) <= 24 and len(r["description"]) <= 72 for r in rows)
    twelve = [b(f"Option {i}", f"opt:{i}") for i in range(12)]
    msgs, opts = W.render("which?", twelve)
    assert opts == [f"opt:{i}" for i in range(12)] and msgs[0]["type"] == "text"
    assert "Reply with the NUMBER" in msgs[0]["text"]["body"] and "12. Option 11" in msgs[0]["text"]["body"]
    long = "\n".join(f"line {i} " + "x" * 60 for i in range(200))                  # a "show all fields" listing
    parts, _ = W.render(long, [])
    assert len(parts) > 1 and all(len(p["text"]["body"]) <= 4000 for p in parts)
    assert "\n".join(p["text"]["body"] for p in parts) == long
    msgs, _ = W.render("y" * 1500, [b("OK", "x")])
    assert [m["type"] for m in msgs] == ["text", "interactive"] and len(msgs[1]["interactive"]["body"]["text"]) <= 1024


def test_webhook_handshake_and_signature(wa):
    _, bridge, _ = wa
    assert bridge.verify_subscription("subscribe", "VERIFY", "1234") == "1234"
    assert bridge.verify_subscription("subscribe", "wrong", "1234") is None
    assert bridge.signature_ok(b"{}", None)                                  # no app secret configured: not checked
    bridge.app_secret = "secret"
    good = "sha256=" + hmac.new(b"secret", b"{}", hashlib.sha256).hexdigest()
    assert bridge.signature_ok(b"{}", good) and not bridge.signature_ok(b"{}", "sha256=00") and not bridge.signature_ok(b"{}", None)
    assert W.parse_numbers("+212 600-000-001=midwife1; 212600000002 = midwife2,bad") == {
        "212600000001": "midwife1", "212600000002": "midwife2"}


def test_capture_review_and_notifications_over_whatsapp(wa):
    rig, bridge, graph = wa
    rig.say("status")                                                        # older web chat: must not be replayed
    assert bridge.handle_webhook(incoming(body="help", mid="wamid.1")) == 1
    assert len(graph.sent) == 1 and graph.sent[0]["to"] == NUMBER and "Send me a photo" in graph.texts()[0]
    assert bridge.handle_webhook(incoming(body="help", mid="wamid.1")) == 0  # re-delivered by Meta: handled once
    assert len(graph.sent) == 1
    # a photo: downloaded from Meta, stored encrypted, acknowledged with a button
    bridge.handle_webhook(incoming("image", mid="wamid.2", image={"id": "MEDIA1", "mime_type": "image/jpeg"}))
    rid = rig.rid()
    assert rig.store.image_for_processing(rig.store.pages(rid)[0]["id"]) == graph.media
    last = graph.sent[-1]["interactive"]
    assert "Page 1 saved" in last["body"]["text"] and last["action"]["buttons"][0]["reply"] == {
        "id": "cmd:done", "title": "Done - no more pages"}
    bridge.handle_webhook(tap("cmd:done", "wamid.3"))
    assert rig.state(rid) == L.PENDING_AI
    # the worker reads the page later; its message reaches WhatsApp through the pump, not through a webhook
    before = len(graph.sent)
    rig.tick()
    assert bridge.pump() >= 1 and len(graph.sent) > before
    assert any("here is what I read" in t for t in graph.texts())
    question = graph.sent[-1]["interactive"]                                 # the linking code: always confirmed
    assert question["type"] == "list" and "AAA-111" in question["body"]["text"]
    assert question["action"]["sections"][0]["rows"][0] == {"id": "ans:ok", "title": "✓ It is correct"}
    bridge.handle_webhook(tap("ans:ok", "wamid.4", "list_reply"))
    assert rig.store.record(rid)["data"]["sections"]["cover"]["fields"]["fiche_number"]["status"] == "KNOWN"
    assert bridge.pump() == 0                                                # nothing is sent twice


def test_numbered_options_are_answered_by_their_number(wa):
    rig, bridge, graph = wa
    bridge.handle_webhook(incoming(body="help", mid="wamid.1"))
    buttons = [{"label": f"Choice {i}", "payload": f"cmd:manual:x{i}"} for i in range(12)]
    rig.store.add_message(rig.mw["id"], "bot", "Pick one", buttons)
    bridge.pump()
    assert "3. Choice 2" in graph.texts()[-1]
    bridge.handle_webhook(incoming(body="3", mid="wamid.2"))
    users = [m["text"] for m in rig.store.messages(rig.mw["id"]) if m["sender"] == "user"]
    assert users[-1] == "Choice 2"                                           # the number became that button's payload


def test_messages_wait_while_offline_or_refused_and_arrive_in_order(wa):
    rig, bridge, graph = wa
    bridge.handle_webhook(incoming(body="help", mid="wamid.1"))
    n = len(graph.sent)
    for failure in ("network", True):
        graph.fail = failure
        bridge.handle_webhook(incoming(body="status", mid=f"wamid.{failure}"))
        assert len(graph.sent) == n                                          # not delivered, not lost
    graph.fail = False
    rig.net.online = False
    assert bridge.pump() == 0 and len(graph.sent) == n                       # simulated "no connection"
    rig.net.online = True
    assert bridge.pump() == 2
    assert len(graph.sent) == n + 2 and bridge.pump() == 0


def test_unknown_numbers_are_refused_and_no_phone_number_is_stored(wa):
    rig, bridge, graph = wa
    assert bridge.handle_webhook(incoming(body="status", sender="33700000000", mid="wamid.9")) == 0
    assert graph.sent[-1]["to"] == "33700000000" and "not registered" in graph.texts()[-1]
    bridge.handle_webhook(incoming(body="help", mid=f"wamid.{NUMBER}.abc"))  # real ids embed the sender's number
    bridge.handle_webhook(incoming("audio", mid="wamid.10"))
    assert "only read text messages and photos" in graph.texts()[-1]
    dump = json.dumps([rig.store.q(f"SELECT * FROM {t}") for t in ("meta", "wa_seen", "messages", "events", "sessions")])
    assert NUMBER not in dump and "33700000000" not in dump


def test_locked_device_answers_that_it_is_locked(tmp_path):
    graph = Graph()
    device = SimpleNamespace(store=None, agent=None, connectivity=SimpleNamespace(online=True))
    bridge = W.WhatsAppBridge(device, "TOKEN", "PHONE", "VERIFY", {NUMBER: "midwife1"}, http=graph)
    assert bridge.handle_webhook(incoming()) == 0 and "locked" in graph.texts()[-1]


def test_server_routes(tmp_path, monkeypatch):
    for k, v in {"DAYONE_DEVICE_DIR": str(tmp_path / "dev"), "DAYONE_EXTRACTOR": "fake", "DAYONE_PIN": "1234",
                 "DAYONE_THRESHOLD_FILE": str(tmp_path / "none.json"), "DAYONE_SYNC_URL": "http://127.0.0.1:9",
                 "WHATSAPP_TOKEN": "TOKEN", "WHATSAPP_PHONE_NUMBER_ID": "PHONE", "WHATSAPP_VERIFY_TOKEN": "VERIFY",
                 "WHATSAPP_APP_SECRET": "secret", "WHATSAPP_NUMBERS": f"{NUMBER}=midwife1"}.items():
        monkeypatch.setenv(k, v)
    import app.config as cfg
    import app.server as srv
    importlib.reload(cfg)
    importlib.reload(srv)
    try:
        graph = Graph()
        srv.whatsapp.http = graph
        c = TestClient(srv.app)
        assert srv.device.store is not None                                  # DAYONE_PIN unlocked the device at start-up
        r = c.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "VERIFY", "hub.challenge": "42"})
        assert r.status_code == 200 and r.text == "42"
        assert c.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"}).status_code == 403
        body = json.dumps(incoming(body="help")).encode()
        assert c.post("/whatsapp/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 403
        sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        r = c.post("/whatsapp/webhook", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
        assert r.status_code == 200 and r.json() == {"handled": 1} and "Send me a photo" in graph.texts()[-1]
    finally:
        srv.whatsapp.stop()
        srv.device.worker and srv.device.worker.stop()
        monkeypatch.undo()
        importlib.reload(cfg)
        importlib.reload(srv)                                                # leave the module without WhatsApp
