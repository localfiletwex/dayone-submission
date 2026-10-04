import importlib

import pytest
from fastapi.testclient import TestClient

from tests.conftest import img


def test_sync_server_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYONE_SERVER_DB", str(tmp_path / "srv.db"))
    import app.sync_server as ss
    importlib.reload(ss)
    c = TestClient(ss.app)
    p = {"record_id": "r1", "version": 1, "sections": {}, "pages": []}
    assert c.post("/sync/records", json=p).json() == {"result": "stored"}
    assert c.post("/sync/records", json=p).json() == {"result": "duplicate"}
    assert c.post("/sync/records", json={**p, "version": 2}).json() == {"result": "stored"}
    assert c.get("/sync/records").json()["count"] == 2
    assert c.post("/sync/records", json={"version": 1}).status_code == 422
    assert c.post("/sync/records", json={**p, "pages": [{"image": "abc"}]}).status_code == 422


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYONE_DEVICE_DIR", str(tmp_path / "dev"))
    monkeypatch.setenv("DAYONE_EXTRACTOR", "fake")
    monkeypatch.setenv("DAYONE_THRESHOLD_FILE", str(tmp_path / "no-threshold.json"))   # not the calibration file of this machine
    monkeypatch.setenv("DAYONE_SYNC_URL", "http://127.0.0.1:9")      # nothing listens: sync just waits
    import app.config as cfg
    import app.server as srv
    importlib.reload(cfg)
    importlib.reload(srv)
    c = TestClient(srv.app)
    yield c, srv
    srv.device.worker and srv.device.worker.stop()


def login(c, user="midwife1", pin="1234"):
    r = c.post("/api/login", json={"username": user, "pin": pin})
    return r, {"Authorization": "Bearer " + r.json().get("token", "")}


def test_api_login_capture_and_roles(api):
    c, srv = api
    assert c.get("/api/state").status_code == 401
    r, h = login(c)
    assert r.status_code == 200 and r.json()["user"]["role"] == "midwife"
    assert login(c, pin="9999")[0].status_code == 401             # wrong PIN
    assert login(c, user="nobody")[0].status_code == 401
    msgs = c.post("/api/image", files={"file": ("p.jpg", img(1), "image/jpeg")}, headers=h).json()
    assert "saved" in msgs[-1]["text"]
    page_id = msgs[0]["page_id"]
    assert c.get(f"/api/pages/{page_id}/image", headers=h).content == img(1)
    _, hd = login(c, "data1")
    assert c.get(f"/api/pages/{page_id}/image", headers=hd).status_code == 403
    assert c.post("/api/message", json={"text": "status"}, headers=hd).status_code == 403
    _, hs = login(c, "supervisor1")
    assert c.get(f"/api/pages/{page_id}/image", headers=hs).status_code == 200
    _, h2 = login(c, "midwife2")
    assert c.get(f"/api/pages/{page_id}/image", headers=h2).status_code == 403
    assert c.get("/api/state", headers=h2).json()["records"] == []
    # offline toggle: capture still works, record waits
    assert c.post("/api/connectivity", json={"value": False}, headers=h).json() == {"online": False}
    out = c.post("/api/message", json={"text": "cmd:done"}, headers=h).json()
    assert "Pending AI processing" in out[-1]["text"]
    st = c.get("/api/state", headers=h).json()
    assert st["online"] is False and st["records"][0]["state"] == "PENDING_AI"
    assert st["threshold"]["calibrated"] is False
