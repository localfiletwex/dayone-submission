"""Connector to a real WhatsApp sandbox (WhatsApp Business Platform, Cloud API).

The same `Agent` that drives the web chat answers on WhatsApp: this module
only translates between the two.

    WhatsApp user --> Meta --> POST /whatsapp/webhook --> Agent.handle_text / handle_image
    Agent's messages (replies AND later notifications) --> `pump()` --> Meta --> WhatsApp user

Design points
* Nothing here knows the review logic.  Buttons keep their payloads; a tapped
  WhatsApp button comes back as the same payload the web UI would send.
* Outgoing messages are read from the encrypted message table, with a cursor
  per account stored in the same database.  A message is marked as sent only
  after Meta accepted it, so a lost connection delays messages, never drops
  or reorders them (the same idea as the sync outbox).
* WhatsApp allows 3 reply buttons (20 characters each) and 10 list rows.
  Messages with more options become a numbered list answered by its number.
* Only phone numbers listed in WHATSAPP_NUMBERS may talk to the agent, each
  mapped to an existing midwife account.  The numbers live in the environment
  only; they are never written to the database or the logs.

PRIVACY: photos sent through WhatsApp pass through Meta's servers, a third
party.  That is acceptable for the synthetic hackathon data ONLY.

STATUS: tested against a simulated Graph API (tests/test_whatsapp.py).  It has
not been run against the real WhatsApp service from the development sandbox.
Setup steps: see WHATSAPP.md.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import urllib.error
import urllib.request

from . import i18n
from .redaction import get_logger

log = get_logger("dayone.whatsapp")

MAX_BUTTONS, MAX_BUTTON_TITLE = 3, 20          # interactive reply buttons
MAX_ROWS, MAX_ROW_TITLE, MAX_ROW_DESCRIPTION = 10, 24, 72      # interactive list
MAX_INTERACTIVE_BODY, MAX_TEXT = 1024, 4000    # characters (the API allows 4096 for text)

TXT = {  # the few sentences that exist only on WhatsApp: (English, French)
    "choose": ("Choose", "Choisir"),
    "options": ("Options", "Options"),
    "choose_body": ("Choose an answer:", "Choisissez une réponse :"),
    "reply_number": ("Reply with the NUMBER of your choice:", "Répondez avec le NUMÉRO de votre choix :"),
    "locked": ("The registry device is locked. Someone must unlock it first (log in once in the web app).",
               "L'appareil du registre est verrouillé. Quelqu'un doit d'abord le déverrouiller "
               "(se connecter une fois dans l'application web)."),
    "unknown_number": ("This phone number is not registered for the registry agent.",
                       "Ce numéro de téléphone n'est pas enregistré pour l'agent du registre."),
    "unsupported": ("I can only read text messages and photos of registry pages.",
                    "Je ne peux lire que des messages texte et des photos de pages du registre."),
    "media_failed": ("I could not download that photo from WhatsApp. Please send it again.",
                     "Je n'ai pas pu télécharger cette photo depuis WhatsApp. Veuillez la renvoyer."),
}


def _txt(lang: str, key: str) -> str:
    return TXT[key][i18n.LANGS.index(i18n.norm_lang(lang))]


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Cut a long message at line breaks so that each part fits in one WhatsApp message."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                       # a single enormous line: hard cut
            parts.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur or not parts:
        parts.append(cur)
    return parts


def render(text: str, buttons: list[dict], lang: str = "en") -> tuple[list[dict], list[str]]:
    """One agent message -> (WhatsApp message bodies to send in order, payloads
    selectable by number).  The second list is empty unless the options had to
    be sent as a numbered list."""
    def text_msg(body):
        return {"type": "text", "text": {"body": body}}

    if not buttons:
        return [text_msg(p) for p in split_text(text)], []
    out = []
    body = text
    if len(body) > MAX_INTERACTIVE_BODY:               # too long for an interactive message: text first
        out += [text_msg(p) for p in split_text(body)]
        body = _txt(lang, "choose_body")
    labels = [b["label"] for b in buttons]
    if len(buttons) <= MAX_BUTTONS and all(len(x) <= MAX_BUTTON_TITLE for x in labels) and len(set(labels)) == len(labels):
        out.append({"type": "interactive", "interactive": {
            "type": "button", "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["payload"], "title": b["label"]}}
                                   for b in buttons]}}})
        return out, []
    if len(buttons) <= MAX_ROWS and all(len(b["payload"]) <= 200 for b in buttons):
        rows, seen = [], set()
        for i, b in enumerate(buttons, 1):
            title = _short(b["label"], MAX_ROW_TITLE)
            if title in seen:                           # row titles must differ
                title = _short(f"{i}. {b['label']}", MAX_ROW_TITLE)
            seen.add(title)
            row = {"id": b["payload"], "title": title}
            if title != b["label"]:
                row["description"] = _short(b["label"], MAX_ROW_DESCRIPTION)
            rows.append(row)
        out.append({"type": "interactive", "interactive": {
            "type": "list", "body": {"text": body},
            "action": {"button": _txt(lang, "choose"), "sections": [{"title": _txt(lang, "options"), "rows": rows}]}}})
        return out, []
    numbered = "\n".join(f"{i}. {b['label']}" for i, b in enumerate(buttons, 1))
    full = f"{text}\n\n{_txt(lang, 'reply_number')}\n{numbered}"
    return [text_msg(p) for p in split_text(full)], [b["payload"] for b in buttons]


def _urllib_http(method: str, url: str, headers: dict, body: bytes | None = None, timeout: float = 20.0):
    """(status, body_bytes).  Network problems raise OSError."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def parse_numbers(spec: str) -> dict[str, str]:
    """'212600000001=midwife1, 212600000002=midwife2' -> {digits: username}."""
    out = {}
    for item in (spec or "").replace(";", ",").split(","):
        if "=" in item:
            number, username = item.split("=", 1)
            digits = "".join(c for c in number if c.isdigit())
            if digits and username.strip():
                out[digits] = username.strip()
    return out


class WhatsAppBridge:
    def __init__(self, device, token: str, phone_number_id: str, verify_token: str, numbers: dict[str, str],
                 app_secret: str = "", api_version: str = "v25.0", http=_urllib_http,
                 api_base: str = "https://graph.facebook.com"):
        self.device = device
        self.token, self.phone_number_id, self.verify_token = token, phone_number_id, verify_token
        self.numbers, self.app_secret = numbers, app_secret
        self.base = f"{api_base.rstrip('/')}/{api_version}"
        self.http = http
        self._lock = threading.Lock()
        self._stop = threading.Event()

    @classmethod
    def from_env(cls, device, env=os.environ):
        """The bridge described by the WHATSAPP_* environment variables, or None."""
        token, pid, verify = env.get("WHATSAPP_TOKEN"), env.get("WHATSAPP_PHONE_NUMBER_ID"), env.get("WHATSAPP_VERIFY_TOKEN")
        if not (token and pid and verify):
            return None
        return cls(device, token, pid, verify, parse_numbers(env.get("WHATSAPP_NUMBERS", "")),
                   env.get("WHATSAPP_APP_SECRET", ""), env.get("WHATSAPP_API_VERSION", "v25.0"),
                   # WHATSAPP_API_BASE exists so that the connector can be exercised against a local stand-in
                   api_base=env.get("WHATSAPP_API_BASE", "https://graph.facebook.com"))

    # ---- webhook verification -------------------------------------------
    def verify_subscription(self, mode, token, challenge):
        """Meta's one-time handshake when the webhook URL is registered."""
        return challenge if mode == "subscribe" and token and hmac.compare_digest(token, self.verify_token) else None

    def signature_ok(self, body: bytes, header: str | None) -> bool:
        """Check X-Hub-Signature-256 when an app secret is configured."""
        if not self.app_secret:
            return True
        expected = "sha256=" + hmac.new(self.app_secret.encode(), body, hashlib.sha256).hexdigest()
        return bool(header) and hmac.compare_digest(header, expected)

    # ---- helpers ----------------------------------------------------------
    def _user(self, wa_id: str):
        store = self.device.store
        username = self.numbers.get("".join(c for c in str(wa_id) if c.isdigit()))
        user = store.user(username) if store is not None and username else None
        return user if user and user["role"] == "midwife" else None

    def _wa_id(self, user_id: str):
        store = self.device.store
        for number, username in self.numbers.items():
            u = store.user(username)
            if u and u["id"] == user_id:
                return number
        return None

    def _post(self, to: str, message: dict) -> bool:
        body = json.dumps({"messaging_product": "whatsapp", "to": to, **message}).encode()
        try:
            status, answer = self.http("POST", f"{self.base}/{self.phone_number_id}/messages",
                                       {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}, body)
        except OSError as e:
            log.info("whatsapp: send postponed (%s)", type(e).__name__)
            return False
        if status >= 300:
            log.warning("whatsapp: send refused, HTTP %s %s", status, answer[:200].decode("utf-8", "replace"))
            return False
        return True

    def _download(self, media_id: str):
        """(bytes, mime) of a media object, or None.  Two calls: where it is, then the file."""
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            status, body = self.http("GET", f"{self.base}/{media_id}", headers)
            if status >= 300:
                return None
            info = json.loads(body)
            status, data = self.http("GET", info["url"], headers)
        except (OSError, ValueError, KeyError):
            return None
        return (data, info.get("mime_type") or "image/jpeg") if status < 300 and data else None

    # ---- incoming -----------------------------------------------------------
    def handle_webhook(self, payload: dict) -> int:
        """Process a webhook notification.  Returns the number of messages handled."""
        handled = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for msg in (change.get("value") or {}).get("messages", []) or []:
                    handled += self._incoming(msg)
        self.pump()
        return handled

    def _incoming(self, msg: dict) -> int:
        sender, store = str(msg.get("from", "")), self.device.store
        if store is None:
            self._post(sender, {"type": "text", "text": {"body": _txt(i18n.DEFAULT_LANG, "locked")}})
            return 0
        user = self._user(sender)
        if user is None:
            self._post(sender, {"type": "text", "text": {"body": _txt(i18n.DEFAULT_LANG, "unknown_number")}})
            return 0
        if not store.wa_first_time(msg.get("id") or ""):        # Meta re-delivers webhooks: handle each message once
            return 0
        agent, uid = self.device.agent, user["id"]
        if store.meta(f"wa_cursor:{uid}") is None:              # first contact: do not replay the older web chat
            store.set_meta(f"wa_cursor:{uid}", store.last_message_id(uid))
        lang, kind = agent.lang(uid), msg.get("type")
        if kind == "text":
            text = (msg.get("text") or {}).get("body", "").strip()
            options = json.loads(store.meta(f"wa_options:{uid}") or "[]")
            if text.isdigit() and 1 <= int(text) <= len(options):
                text = options[int(text) - 1]                   # answer to a numbered list
            agent.handle_text(user, text)
        elif kind == "interactive":
            reply = (msg.get("interactive") or {})
            reply = reply.get("button_reply") or reply.get("list_reply") or {}
            agent.handle_text(user, reply.get("id", ""))
        elif kind == "image" or (kind == "document" and str((msg.get("document") or {}).get("mime_type", "")).startswith("image/")):
            media = self._download((msg.get(kind) or {}).get("id", ""))
            if media is None:
                agent._say(uid, _txt(lang, "media_failed"))
            else:
                agent.handle_image(user, media[0], media[1].split(";")[0])
        else:
            agent._say(uid, _txt(lang, "unsupported"))
        return 1

    # ---- outgoing -------------------------------------------------------------
    def pump(self) -> int:
        """Send every agent message not yet delivered to WhatsApp.  Safe to call at any time."""
        store = self.device.store
        if store is None or not self.device.connectivity.online:
            return 0
        sent = 0
        with self._lock:
            for username in set(self.numbers.values()):
                user = store.user(username)
                cursor = store.meta(f"wa_cursor:{user['id']}") if user else None
                if cursor is None:                              # this account has not written on WhatsApp yet
                    continue
                to, lang = self._wa_id(user["id"]), self.device.agent.lang(user["id"])
                for m in store.messages(user["id"], int(cursor)):
                    if m["sender"] == "bot":
                        bodies, options = render(m["text"], m["buttons"], lang)
                        if not all(self._post(to, b) for b in bodies):
                            break                               # try again at the next pump, same order
                        store.set_meta(f"wa_options:{user['id']}", json.dumps(options))
                        sent += 1
                    store.set_meta(f"wa_cursor:{user['id']}", m["id"])
        return sent

    def run_forever(self, interval: float = 1.0):
        """Delivers the worker's notifications (page read, synchronised, AI unavailable...)."""
        while not self._stop.wait(interval):
            try:
                self.pump()
            except Exception:                                   # keep delivering; details stay out of logs
                log.exception("whatsapp pump failed")

    def stop(self):
        self._stop.set()
