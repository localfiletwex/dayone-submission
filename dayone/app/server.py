"""HTTP API + static WhatsApp-style chat UI for the prototype.

    uvicorn app.server:app --port 8000

The device database is unlocked by the first successful login (the PIN
derives the SQLCipher key; nothing is readable on disk without it).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import config, i18n, lifecycle as L
from .conversation import Agent
from .extractors.base import DisabledExtractor, Extractor, ExtractorUnavailable, WHY_SWITCHED_OFF
from .redaction import get_logger, install_log_filter
from .storage import AccessDenied, Store, WrongKey
from .sync import HttpSyncTarget
from .whatsapp import WhatsAppBridge
from .worker import Connectivity, Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
install_log_filter()
log = get_logger("dayone.server")
STATIC = Path(__file__).parent / "static"


class Switchable(Extractor):
    """Wraps the configured extractor so the demo can switch the AI off."""

    def __init__(self, inner: Extractor):
        self.inner, self.enabled, self.name = inner, True, inner.name
        self.model = getattr(inner, "model", None)          # shown in the status panel

    def available(self):
        return self.enabled and self.inner.available()

    def why_unavailable(self):
        return WHY_SWITCHED_OFF if not self.enabled else self.inner.why_unavailable()

    def extract(self, image, mime="image/jpeg", page_type_hint=None):
        if not self.enabled:
            raise ExtractorUnavailable("AI switched off", WHY_SWITCHED_OFF)
        return self.inner.extract(image, mime, page_type_hint)


def make_extractor(connectivity) -> Extractor:
    if config.EXTRACTOR == "fake":
        from .extractors.fake import FakeExtractor
        return FakeExtractor()
    if config.EXTRACTOR == "disabled":
        return DisabledExtractor()
    from .extractors.gemini import GeminiExtractor
    # Model and thinking level come from config.py.  One quick retry only: when the service is
    # overloaded the WORKER waits and explains (worker.ai_problem) instead of blocking here for minutes.
    return GeminiExtractor(connectivity=connectivity, max_retries=1)


class Device:
    """Everything that exists once the device is unlocked."""

    def __init__(self):
        self.lock = threading.Lock()
        self.store: Store | None = None
        self.worker: Worker | None = None
        self.agent: Agent | None = None
        self.tokens: dict[str, str] = {}
        self.connectivity = Connectivity(online=True)
        self.extractor = Switchable(make_extractor(self.connectivity))
        self.threshold = config.load_threshold()

    def unlock(self, pin: str):
        with self.lock:
            if self.store is None:
                store = Store(config.DEVICE_DIR, pin)          # raises WrongKey
                self.worker = Worker(store, self.extractor, HttpSyncTarget(config.SYNC_URL), self.connectivity,
                                     threshold=self.threshold["value"], threshold_arabic=self.threshold.get("arabic"),
                                     arabic_scope=self.threshold.get("arabic_scope", "numbers"))
                self.agent = Agent(store, self.worker, self.threshold)
                self.worker.notifier = self.agent
                self.store = store
                threading.Thread(target=self.worker.run_forever, daemon=True).start()
            else:
                Store(config.DEVICE_DIR, pin).close()          # verify the PIN against the same database


device = Device()
app = FastAPI(title="DayOne maternal registry agent (prototype)")


class Login(BaseModel):
    username: str
    pin: str


class Text(BaseModel):
    text: str


class Flag(BaseModel):
    value: bool


class Language(BaseModel):
    lang: str


def current_user(authorization: str = Header(default="")):
    uid = device.tokens.get(authorization.removeprefix("Bearer ").strip())
    if not uid or device.store is None:
        raise HTTPException(401, "not logged in")
    return device.store.user_by_id(uid)


def midwife(user=Depends(current_user)):
    if user["role"] != "midwife":
        raise HTTPException(403, "only midwives capture and review registries")
    return user


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/login")
def login(body: Login):
    try:
        device.unlock(body.pin)
    except WrongKey:
        raise HTTPException(401, "wrong PIN")
    user = device.store.user(body.username.strip())
    if user is None:
        raise HTTPException(401, "unknown account")
    token = secrets.token_urlsafe(24)
    device.tokens[token] = user["id"]
    device.store.event("login", actor=user["id"])
    return {"token": token, "lang": device.agent.lang(user["id"]),
            "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}


@app.post("/api/language")
def language(body: Language, user=Depends(current_user)):
    """Interface language of this account ("en" or "fr"); what is already in the chat is not rewritten."""
    if body.lang not in i18n.LANGS:
        raise HTTPException(400, "unknown language")
    return {"lang": device.agent.set_language(user["id"], body.lang)}


@app.get("/api/messages")
def messages(after: int = 0, user=Depends(current_user)):
    return device.store.messages(user["id"], after)


@app.post("/api/message")
def message(body: Text, user=Depends(midwife)):
    return device.agent.handle_text(user, body.text)


@app.post("/api/image")
async def image(file: UploadFile = File(...), user=Depends(midwife)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    return device.agent.handle_image(user, data, file.content_type or "image/jpeg")


@app.get("/api/pages/{page_id}/image")
def page_image(page_id: str, user=Depends(current_user)):
    try:
        data, mime = device.store.read_image(page_id, user)
    except AccessDenied as e:
        raise HTTPException(403, str(e))
    except KeyError:
        raise HTTPException(404, "no such page")
    return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})


@app.get("/api/records/{record_id}")
def record(record_id: str, user=Depends(current_user)):
    r = device.store.record(record_id)
    if r is None or (user["role"] == "midwife" and r["midwife_id"] != user["id"]):
        raise HTTPException(404, "no such record")
    r["pages"] = [{k: p[k] for k in ("id", "seq", "captured_at", "midwife_id", "status", "page_type", "superseded_by")}
                  for p in device.store.pages(record_id, active_only=False)]
    return r


@app.get("/api/state")
def state(user=Depends(current_user)):
    s = device.store
    where, args = ("midwife_id=?", (user["id"],)) if user["role"] == "midwife" else ("1=1", ())
    recs = s.records(where, args)
    try:
        server = json.loads(urllib.request.urlopen(config.SYNC_URL + "/sync/records", timeout=1).read())["count"] \
            if device.connectivity.online else None
    except Exception:
        server = None
    worker = device.worker
    why, detail = worker.ai_why()
    problem = worker.ai_problem
    calibrated_for = device.threshold.get("model")
    return {
        "lang": device.agent.lang(user["id"]),
        "online": device.connectivity.online, "ai_enabled": device.extractor.enabled,
        "ai_available": worker.ai_available(), "extractor": device.extractor.name,
        # Why the AI is not doing the work right now ("" when it is): a code the page translates,
        # the provider's own explanation, and how long until the next automatic try.
        "ai_why": why, "ai_why_text": i18n.t(device.agent.lang(user["id"]), f"ai_why.{why}") if why else "",
        "ai_detail": detail,
        "ai_retry_in": max(0, round(worker._ai_retry_at - time.monotonic())) if problem else 0,
        "ai_since": problem["since"][11:19] if problem else None,
        "model": device.extractor.model,
        "threshold_model_mismatch": bool(calibrated_for and device.extractor.model
                                         and calibrated_for != device.extractor.model),
        "dashboard_url": config.SYNC_URL + "/dashboard", "whatsapp": whatsapp is not None,
        "threshold": device.threshold, "server_records": server,
        "outbox_pending": len(s.outbox("pending")),
        "records": [{"id": r["id"], "state": r["state"], "pages": len(s.pages(r["id"])),
                     "linking_code": r["linking_code"], "patient_id": r["patient_id"],
                     "superseded_by": r["superseded_by"], "failure_reason": r["failure_reason"],
                     "read": sum(p["status"] == L.AI_PROCESSED for p in s.pages(r["id"]))} for r in recs],
        "events": [{"ts": e["ts"][11:19], "kind": e["kind"], "from": e["from_state"], "to": e["to_state"],
                    "record": (e["record_id"] or "")[:6]} for e in s.events()[-25:]],
        "states": list(L.STATES),
    }


@app.post("/api/connectivity")
def connectivity(body: Flag, user=Depends(current_user)):
    device.connectivity.online = body.value
    device.store.event("connectivity", actor=user["id"], online=body.value)
    return {"online": body.value}


@app.post("/api/ai")
def ai(body: Flag, user=Depends(current_user)):
    device.extractor.enabled = body.value
    device.store.event("ai_toggle", actor=user["id"], enabled=body.value)
    return {"ai_enabled": body.value}


@app.post("/api/ai/retry")
def ai_retry(user=Depends(current_user)):
    """Stop waiting after trouble with the AI service and ask it again now."""
    device.worker.retry_ai_now()
    return {"ok": True}


# ---- WhatsApp sandbox (bonus; active only when the WHATSAPP_* variables are set, see WHATSAPP.md) ----
whatsapp = WhatsAppBridge.from_env(device)


@app.get("/whatsapp/webhook")
def whatsapp_verify(request: Request):
    q = request.query_params
    challenge = whatsapp.verify_subscription(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge")) \
        if whatsapp else None
    if challenge is None:
        raise HTTPException(403, "webhook verification failed")
    return PlainTextResponse(challenge)


@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    if whatsapp is None:
        raise HTTPException(404, "WhatsApp is not configured")
    body = await request.body()
    if not whatsapp.signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "bad signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "not JSON")
    return {"handled": whatsapp.handle_webhook(payload)}


def _start_background() -> None:
    """Things that normally wait for a web login, done at start-up when asked for."""
    pin = os.environ.get("DAYONE_PIN")
    if pin:                                   # lets the WhatsApp agent answer without anyone opening the web page
        try:
            device.unlock(pin)
        except WrongKey:
            log.error("DAYONE_PIN does not open the device database; it stays locked")
    if whatsapp is not None:
        threading.Thread(target=whatsapp.run_forever, daemon=True).start()
        log.info("WhatsApp connector active for %d number(s)", len(whatsapp.numbers))


_start_background()
