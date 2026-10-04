"""The conversational agent (WhatsApp-style): capture, review, manual entry,
re-digitisation and patient matching.

Every handler runs inside ONE database transaction: the state change and the
messages that announce it are committed together, and only then returned to
the UI.  The conversation state itself lives in the database, so the chat
resumes exactly where it was after a crash or restart.

Nothing in here gives clinical advice, scores risk or suggests treatment.

Wording: every sentence comes from app/i18n.py (`self._t(mid, key, ...)`), in
the language the midwife chose (French or English).  Button PAYLOADS are the
same in both languages, so the logic below never depends on the language.
"""
from __future__ import annotations

from . import i18n, lifecycle as L, linking, pipeline, quality, schema as S
from .normalize import canon, plausible
from .redaction import scrub
from .storage import Store

BULK_MIN = 3        # from this many uncertain fields on, offer to see them all in one message
SHORT = 6          # characters of a record id shown to the midwife


def B(label, payload):
    return {"label": label, "payload": payload}


HELP = i18n.t("en", "help")          # kept for callers that import it; the chat uses the midwife's language


class Agent:
    def __init__(self, store: Store, worker=None, threshold_info: dict | None = None):
        self.store, self.worker = store, worker
        self.threshold_info = threshold_info or {"value": getattr(worker, "threshold", 0.85), "calibrated": False}

    # ------------------------------------------------------------------ io
    def _say(self, mid, text, buttons=(), rid=None):
        self.store.add_message(mid, "bot", text, buttons, rid)

    # ------------------------------------------------------------ language
    def lang(self, mid) -> str:
        """The interface language of this account ("en" or "fr")."""
        return i18n.norm_lang(self.store.meta(f"lang:{mid}"))

    def set_language(self, mid, lang) -> str:
        lang = i18n.norm_lang(lang)
        self.store.set_meta(f"lang:{mid}", lang)
        return lang

    def _t(self, mid, key, **kw) -> str:
        return i18n.t(self.lang(mid), key, **kw)

    def _b(self, mid, key, payload, **kw):
        """A button whose label is the message `key` in the midwife's language."""
        return B(self._t(mid, key, **kw), payload)

    def _sec(self, mid, sec) -> str:
        return i18n.section_title(self.lang(mid), sec)

    def _why(self, mid) -> str:
        """Why the AI cannot be used right now, as a phrase ("no connection", ...)."""
        code = self.worker.ai_why()[0] if self.worker is not None else "switched_off"
        return self._t(mid, f"ai_why.{code or 'switched_off'}")

    def _last_id(self, mid):
        return self.store.last_message_id(mid)

    def _online(self):
        return self.worker is None or self.worker.connectivity.online

    def _ai(self):
        return self.worker is not None and self.worker.ai_available()

    # -------------------------------------------------------------- entry
    def handle_image(self, user: dict, image: bytes, mime: str = "image/jpeg") -> list[dict]:
        mid = user["id"]
        before = self._last_id(mid)
        store = self.store
        # 1. persist the capture FIRST, in its own committed step
        s = store.session(mid)
        retake = s.get("mode") == "retake_wait"
        if retake or s.get("mode") == "capturing":
            rid = s["rid"]
        else:
            rid = store.create_record(mid)
        pid, dup = store.add_page(rid, mid, image, mime)
        # 2. the photo is safe; check on the device whether it is likely to be readable
        doubts = [] if dup else self._check_quality(rid, pid, image)
        # 3. only now talk about it
        with store.tx():
            store.add_message(mid, "user", "📷 photo", (), rid, pid)
            if retake:
                self._finish_retake(mid, s, rid, pid, dup, doubts)
            else:
                if s.get("mode") not in ("capturing", "idle", None) and s.get("rid") != rid:
                    self._say(mid, self._t(mid, "new_registry"))
                replaced = s.get("mode") == "capturing" and s.get("replace")
                if replaced and not dup and replaced != pid and store.page(replaced):
                    store.set_page(replaced, superseded_by=pid)      # the doubtful photo gives way to the new one
                n = len(store.pages(rid))
                ns = {"mode": "capturing", "rid": rid}
                if dup:
                    self._say(mid, self._t(mid, "dup_photo"), [self._b(mid, "btn_done_short", "cmd:done")], rid)
                elif doubts:
                    ns["doubt"] = pid
                    self._say(mid, self._t(mid, "quality_warn", n=n, reasons=self._reasons(mid, doubts)),
                              [self._b(mid, "btn_quality_retake", f"q:retake:{pid}"),
                               self._b(mid, "btn_quality_keep", "q:keep")], rid)
                else:
                    self._say(mid, self._t(mid, "quality_replaced" if replaced else "page_saved", n=n),
                              [self._b(mid, "btn_done", "cmd:done")], rid)
                store.set_session(mid, ns)
        return store.messages(mid, before)

    # ------------------------------------------------- image-quality check
    def _check_quality(self, rid, pid, image) -> list[str]:
        """Run the on-device check and record its result (statistics only, never
        content) in the audit trail.  Returns the issue codes; [] when the photo
        looks fine or cannot be decoded (the check is then skipped)."""
        q = quality.assess(image)
        if q is None:
            self.store.event("quality_not_checked", record_id=rid, page_id=pid)
            return []
        self.store.event("quality_checked", record_id=rid, page_id=pid, ok=q["ok"], issues=q["issues"], **q["metrics"])
        return q["issues"]

    def _reasons(self, mid, issues) -> str:
        return ", ".join(self._t(mid, f"quality.{code}") for code in issues)

    def handle_text(self, user: dict, text: str) -> list[dict]:
        mid = user["id"]
        before = self._last_id(mid)
        text = (text or "").strip()
        with self.store.tx():
            shown, _ = scrub(text)                       # never store an identifier typed in chat
            self.store.add_message(mid, "user", shown if not text.startswith(tuple(_PAYLOAD_PREFIXES)) else
                                   self._label_for(mid, text))
            self._dispatch(mid, text)
        return self.store.messages(mid, before)

    def _label_for(self, mid, payload):
        import json
        for row in self.store.q("SELECT buttons FROM messages WHERE midwife_id=? AND sender='bot' ORDER BY id DESC LIMIT 6", (mid,)):
            for b in json.loads(row["buttons"]):
                if b["payload"] == payload:
                    return b["label"]
        return self._t(mid, "button")

    # ----------------------------------------------------------- dispatch
    def _dispatch(self, mid, text):
        store = self.store
        s = store.session(mid)
        low = text.lower()
        if low in i18n.LANG_COMMANDS:
            self.set_language(mid, i18n.LANG_COMMANDS[low])
            return self._say(mid, self._t(mid, "lang_set"))
        if low in ("help", "aide", "?"):
            return self._say(mid, self._t(mid, "help"))
        if low in ("status", "statut"):
            return self._status(mid)
        if low in ("history", "historique"):
            return self._history(mid)
        if text.startswith("review:"):
            return self._open(mid, text.split(":", 1)[1])
        if text.startswith("cmd:manual:"):
            return self._start_manual(mid, text.split(":", 2)[2])
        if text.startswith("retry_ai:"):
            return self._retry_ai(mid, text.split(":", 1)[1])
        if text.startswith("retry_sync:"):
            self.worker.retry_sync(text.split(":", 1)[1])
            return self._say(mid, self._t(mid, "sync_retry"))
        if text == "ai:retry_now":
            if self.worker is not None:
                self.worker.retry_ai_now()
            return self._say(mid, self._t(mid, "ai_now"))
        mode = s.get("mode", "idle")
        handler = getattr(self, f"_m_{mode}", None)
        if handler is None or mode == "idle":
            return self._say(mid, self._t(mid, "help"))
        return handler(mid, s, text)

    # ------------------------------------------------------------ capture
    def _m_capturing(self, mid, s, text):
        store, rid = self.store, s["rid"]
        done = self._b(mid, "btn_done", "cmd:done")
        # answers to the image-quality question ("this photo may be hard to read")
        if text.startswith("q:retake:") and s.get("doubt") == text[9:]:
            store.set_session(mid, {"mode": "capturing", "rid": rid, "replace": text[9:]})
            return self._say(mid, self._t(mid, "quality_retake"), (), rid)
        if text == "q:keep":
            store.set_session(mid, {"mode": "capturing", "rid": rid})
            return self._say(mid, self._t(mid, "quality_kept"), [done], rid)
        if text != "cmd:done" and text.lower() not in ("done", "fini", "terminé", "termine"):
            return self._say(mid, self._t(mid, "send_next_or_done"), [done])
        pages = store.pages(rid)
        for p in pages:
            if p["status"] == L.CAPTURED:
                store.set_page(p["id"], status=L.PENDING_AI)
        store.transition(rid, L.PENDING_AI, actor=mid, reason="capture session closed")
        store.set_session(mid, {"mode": "idle", "rid": rid})
        tag = rid[:SHORT]
        if self._ai():
            self._say(mid, self._t(mid, "queued_reading", tag=tag, n=len(pages)), (), rid)
        else:
            self._say(mid, self._t(mid, "pending", tag=tag, n=len(pages), why=self._why(mid)),
                      [self._b(mid, "btn_manual_now", f"cmd:manual:{rid}")], rid)

    # ------------------------------------------------- worker notifications
    def on_processed(self, rid):
        rec = self.store.record(rid)
        mid = rec["midwife_id"]
        with self.store.tx():
            s = self.store.session(mid)
            if s.get("mode", "idle") == "idle":
                self._begin_review(mid, rid)
            else:
                self._say(mid, self._t(mid, "read_ready", tag=rid[:SHORT]),
                          [self._b(mid, "btn_review_now", f"review:{rid}")], rid)

    def on_failed(self, rid):
        rec = self.store.record(rid)
        mid = rec["midwife_id"]
        with self.store.tx():
            self._say(mid, self._t(mid, "failed", tag=rid[:SHORT], reason=rec["failure_reason"]),
                      [self._b(mid, "btn_manual", f"cmd:manual:{rid}"), self._b(mid, "btn_retry_ai", f"retry_ai:{rid}")], rid)

    def on_ai_waiting(self, rid):
        """The AI service is in trouble (overloaded, quota, refused key...).  Say so,
        once per registry: the midwife must never be left looking at a silent
        "pending" without knowing why."""
        rec = self.store.record(rid)
        mid = rec["midwife_id"]
        problem = self.worker.ai_problem or {}
        detail = problem.get("detail", "")
        with self.store.tx():
            self._say(mid, self._t(mid, "ai_waiting", tag=rid[:SHORT],
                                   why=self._t(mid, f"ai_why.{problem.get('why') or 'network'}"),
                                   secs=problem.get("retry_in", 0),
                                   detail=self._t(mid, "ai_detail", detail=detail) if detail else ""),
                      [self._b(mid, "btn_manual_now", f"cmd:manual:{rid}"), self._b(mid, "btn_ai_now", "ai:retry_now")], rid)

    def on_synced(self, rid):
        rec = self.store.record(rid)
        mid = rec["midwife_id"]
        with self.store.tx():
            self._say(mid, self._t(mid, "synced", tag=rid[:SHORT]), (), rid)

    def on_sync_failed(self, rid):
        rec = self.store.record(rid)
        mid = rec["midwife_id"]
        with self.store.tx():
            self._say(mid, self._t(mid, "sync_refused", tag=rid[:SHORT]),
                      [self._b(mid, "btn_sync_again", f"retry_sync:{rid}")], rid)

    # --------------------------------------------------------------- open
    def _open(self, mid, rid):
        rec = self.store.record(rid)
        if rec is None or rec["midwife_id"] != mid:
            return self._say(mid, self._t(mid, "not_found"))
        st = rec["state"]
        if st == L.CAPTURED:
            self.store.set_session(mid, {"mode": "capturing", "rid": rid})
            return self._say(mid, self._t(mid, "send_next_page"), [self._b(mid, "btn_done", "cmd:done")], rid)
        if st == L.PENDING_AI:
            return self._say(mid, self._t(mid, "still_pending"),
                             [self._b(mid, "btn_manual_now", f"cmd:manual:{rid}")], rid)
        if st in (L.AI_PROCESSED, L.NEEDS_REVIEW, L.DUPLICATE_SUSPECTED):
            return self._begin_review(mid, rid)
        if st == L.VALIDATED:
            return self._start_linking(mid, rid)
        if st == L.MANUAL_REVIEW_REQUIRED:
            if (rec["failure_reason"] or "").startswith("match"):
                self.store.transition(rid, L.VALIDATED, actor=mid, reason="match decision reopened")
                return self._start_linking(mid, rid)
            return self.on_failed(rid)
        self._say(mid, self._t(mid, "nothing_to_do", tag=rid[:SHORT], state=st), (), rid)

    # ------------------------------------------------------------- review
    def _counts(self, rec):
        out = []
        for sec in S.SECTIONS:
            data = rec["data"]["sections"].get(sec.key)
            if not data:
                continue
            c = {"read": 0, "check": 0, "blank": 0, "other": 0}
            for _, _, v in S.iter_fields(sec.key, data):
                if v["status"] == S.KNOWN:
                    c["read"] += 1
                elif v["status"] == S.NEEDS_REVIEW or (v["status"] == S.ILLEGIBLE and v["source"] == "ai"):
                    c["check"] += 1
                elif v["status"] == S.NOT_PROVIDED:
                    c["blank"] += 1
                else:
                    c["other"] += 1
            out.append((sec, c))
        return out

    def _queue(self, rec):
        """Everything the midwife must still look at before she can confirm: the
        uncertain fields, then one question per table ("is another column filled?").

        The table question exists because the most frequent extraction error measured
        on the dataset is a whole table column that the model does not return (the
        previous-deliveries table).  A column that was not returned has no field to
        be unsure about, so without this question the omission would be silent.
        The answer is remembered in the section (`checked`), so it is asked once.
        """
        fields, tables = [], []
        for sec in S.SECTIONS:
            data = rec["data"]["sections"].get(sec.key)
            if data:
                fields += [[sec.key, p] for p in pipeline.uncertain_paths(sec.key, data)]
                tables += [["__group__", sec.key, g.name, "check"] for g in sec.groups
                           if not data.get("checked", {}).get(g.name)
                           and len(data["groups"].get(g.name, [])) < len(g.slots)]
        return fields + tables

    def _begin_review(self, mid, rid):
        rec = self.store.record(rid)
        if rec["state"] == L.DUPLICATE_SUSPECTED:
            return self._dup_start(mid, rec)
        lines = [self._t(mid, "summary_head", tag=rid[:SHORT])]
        for sec, c in self._counts(rec):
            lines.append(self._t(mid, "summary_line", sec=self._sec(mid, sec), read=c["read"], check=c["check"],
                                 blank=c["blank"])
                         + (self._t(mid, "summary_other", other=c["other"]) if c["other"] else ""))
        missing = [p for p in self.store.pages(rid) if p["status"] != L.AI_PROCESSED]
        if missing:
            lines.append(self._t(mid, "summary_missing", n=len(missing)))
        q = self._queue(rec)
        if q:
            t = self.threshold_info
            n_fields = sum(1 for x in q if x[0] != "__group__")
            if n_fields:
                lines.append(self._t(mid, "not_sure_intro", n=n_fields, pct=f"{t['value']:.0%}",
                                     prov="" if t.get("calibrated") else self._t(mid, "provisional"))
                             + (self._t(mid, "not_sure_arabic_all" if t.get("arabic_scope") == "all" else "not_sure_arabic",
                                        pct=f"{t['arabic']:.0%}") if t.get("arabic") else ""))
            self._say(mid, "\n".join(lines), (), rid)
            s = {"mode": "followup", "rid": rid, "queue": q}
            self.store.set_session(mid, s)
            return self._ask(mid, s)
        self._say(mid, "\n".join(lines), (), rid)
        self._menu(mid, rid)

    def _menu(self, mid, rid, note=""):
        rec = self.store.record(rid)
        q = self._queue(rec)
        remaining, n_fields = len(q), sum(1 for x in q if x[0] != "__group__")
        text = (note + "\n" if note else "") + (
            self._t(mid, "menu_remaining", n=n_fields) if n_fields else
            self._t(mid, "menu_tables") if remaining else self._t(mid, "menu_nothing"))
        buttons = []
        if remaining:
            buttons.append(self._b(mid, "btn_review_remaining", "menu:followups", n=remaining))
        else:
            buttons.append(self._b(mid, "btn_confirm", "menu:confirm"))
        buttons += [self._b(mid, "btn_edit", "menu:edit"), self._b(mid, "btn_retake", "menu:retake"),
                    self._b(mid, "btn_show_all", "menu:all")]
        if any(p["status"] in (L.PENDING_AI, L.MANUAL_REVIEW_REQUIRED) for p in self.store.pages(rid)):
            buttons.append(self._b(mid, "btn_ai_read", "menu:ai"))
        self.store.set_session(mid, {"mode": "menu", "rid": rid})
        self._say(mid, text, buttons, rid)

    def _m_menu(self, mid, s, text):
        store, rid = self.store, s["rid"]
        rec = store.record(rid)
        if text == "menu:followups":
            q = self._queue(rec)
            s = {"mode": "followup", "rid": rid, "queue": q}
            store.set_session(mid, s)
            return self._ask(mid, s) if q else self._menu(mid, rid)
        if text == "menu:confirm":
            if self._queue(rec):
                return self._menu(mid, rid, self._t(mid, "cannot_confirm"))
            if rec["state"] not in (L.AI_PROCESSED, L.NEEDS_REVIEW):
                return self._say(mid, self._t(mid, "registry_is", state=rec["state"]))
            store.transition(rid, L.VALIDATED, actor=mid, reason="confirmed by midwife")
            self._say(mid, self._t(mid, "validated"), (), rid)
            return self._start_linking(mid, rid)
        if text == "menu:all":
            for sec in S.SECTIONS:
                data = rec["data"]["sections"].get(sec.key)
                if data:
                    self._say(mid, self._listing(mid, sec, data)[0], (), rid)
            return self._menu(mid, rid)
        if text == "menu:edit":
            secs = [sec for sec in S.SECTIONS if sec.key in rec["data"]["sections"]]
            if not secs:
                return self._menu(mid, rid, self._t(mid, "no_page_to_edit"))
            store.set_session(mid, {"mode": "edit_section", "rid": rid})
            return self._say(mid, self._t(mid, "which_page"),
                             [B(self._sec(mid, sec), f"sec:{sec.key}") for sec in secs] + [self._b(mid, "btn_back", "back")], rid)
        if text == "menu:retake":
            pages = store.pages(rid)
            store.set_session(mid, {"mode": "retake_pick", "rid": rid})
            return self._say(mid, self._t(mid, "which_photo_retake"),
                             [B(self._t(mid, "page_n", n=p["seq"])
                                + (f" - {self._sec(mid, S.SECTION_BY_KEY[p['page_type']])}" if p["page_type"] else ""),
                                f"page:{p['id']}") for p in pages] + [self._b(mid, "btn_back", "back")], rid)
        if text == "menu:ai":
            for p in store.pages(rid):
                if p["status"] in (L.MANUAL_REVIEW_REQUIRED, L.PROCESSING_FAILED):
                    store.set_page(p["id"], status=L.PENDING_AI, attempts=0)
            store.transition(rid, L.PENDING_AI, actor=mid, reason="midwife asked the AI to read the photos")
            store.set_session(mid, {"mode": "idle", "rid": rid})
            return self._say(mid, self._t(mid, "ai_will_read_now" if self._ai() else "ai_will_read_later"), (), rid)
        self._menu(mid, rid)

    def _listing(self, mid, sec, data):
        lang = self.lang(mid)
        lines, paths = [f"*{i18n.section_title(lang, sec)}*"], []
        for i, (path, f, v) in enumerate(S.iter_fields(sec.key, data), 1):
            paths.append(path)
            mark = i18n.t(lang, f"mark.{v['status']}")
            val = i18n.value(lang, f, v["value"]) if v["status"] in S.VALUE_STATUSES and v["value"] is not None else ""
            lines.append(f"{i}. {i18n.path_title(lang, sec.key, path)}: {val}{mark}")
        return "\n".join(lines), paths

    # --------------------------------------------------------------- edit
    def _m_edit_section(self, mid, s, text):
        rid = s["rid"]
        if not text.startswith("sec:"):
            return self._menu(mid, rid)
        key = text[4:]
        rec = self.store.record(rid)
        sec = S.SECTION_BY_KEY[key]
        listing, paths = self._listing(mid, sec, rec["data"]["sections"][key])
        extra = "".join(self._t(mid, "type_add", g=i18n.group_title(self.lang(mid), g).lower()) for g in sec.groups)
        self.store.set_session(mid, {"mode": "edit_field", "rid": rid, "section": key, "paths": paths})
        self._say(mid, listing + self._t(mid, "type_number") + extra, [self._b(mid, "btn_back", "back")], rid)

    def _m_edit_field(self, mid, s, text):
        rid, key = s["rid"], s["section"]
        sec = S.SECTION_BY_KEY[key]
        if text.lower() in ("add", "ajouter") and sec.groups:
            ns = {"mode": "followup", "rid": rid, "queue": [["__group__", key, sec.groups[0].name, "once"]], "edit": True}
            self.store.set_session(mid, ns)
            return self._ask(mid, ns)
        if not text.isdigit() or not 1 <= int(text) <= len(s["paths"]):
            return self._menu(mid, rid)
        ns = {"mode": "followup", "rid": rid, "queue": [[key, s["paths"][int(text) - 1]]], "edit": True}
        self.store.set_session(mid, ns)
        self._ask(mid, ns)

    # ------------------------------------------------------------- retake
    def _m_retake_pick(self, mid, s, text):
        rid = s["rid"]
        if not text.startswith("page:"):
            return self._menu(mid, rid)
        rec = self.store.record(rid)
        if rec["state"] == L.AI_PROCESSED:
            self.store.transition(rid, L.NEEDS_REVIEW, actor=mid, reason="retake requested")
        self.store.transition(rid, L.CAPTURED, actor=mid, reason="retake requested", page_id=text[5:])
        self.store.set_session(mid, {"mode": "retake_wait", "rid": rid, "page_id": text[5:]})
        self._say(mid, self._t(mid, "retake_send"), (), rid)

    def _m_retake_wait(self, mid, s, text):
        self._say(mid, self._t(mid, "retake_waiting"))

    def _finish_retake(self, mid, s, rid, new_pid, dup, doubts=()):
        store = self.store
        old = store.page(s["page_id"])
        if not dup and old and old["id"] != new_pid:
            store.set_page(old["id"], superseded_by=new_pid)
            store.set_page(new_pid, page_type=old["page_type"])
        for p in store.pages(rid):
            if p["id"] == new_pid or p["status"] == L.CAPTURED:
                store.set_page(p["id"], status=L.PENDING_AI)
        store.transition(rid, L.PENDING_AI, actor=mid, reason="page retaken")
        store.set_session(mid, {"mode": "idle", "rid": rid})
        self._say(mid, self._t(mid, "retake_saved_now" if self._ai() else "retake_saved_later"), (), rid)
        if doubts:
            self._say(mid, self._t(mid, "quality_note", reasons=self._reasons(mid, doubts)), (), rid)

    # ---------------------------------------------------------- follow-ups
    def _field(self, key, path):
        sec = S.SECTION_BY_KEY[key]
        if "[" in path:
            g = next(x for x in sec.groups if x.name == path.split("[")[0])
            return next(f for f in g.fields if f.key == path.split("].")[1])
        return sec.field(path)

    def _ask(self, mid, s):
        store, rid = self.store, s["rid"]
        rec = store.record(rid)
        # drop queue items that are no longer uncertain (e.g. made not-applicable by an answer)
        while s["queue"]:
            head = s["queue"][0]
            if head[0] == "__group__" or s.get("edit"):
                break
            v = S.get_path(rec["data"]["sections"][head[0]], head[1])
            if v["status"] == S.NEEDS_REVIEW or (v["status"] == S.ILLEGIBLE and v["source"] == "ai"):
                break
            s["queue"].pop(0)
        if not s["queue"]:
            # "that was the last question" only when it really was (nothing skipped, no table left to check)
            return self._menu(mid, rid, "" if self._queue(rec) else self._t(mid, "last_question"))
        head = s["queue"][0]
        store.set_session(mid, s)
        lang = self.lang(mid)
        T = lambda key, **kw: i18n.t(lang, key, **kw)                 # noqa: E731
        b = lambda key, payload: B(T(key), payload)                   # noqa: E731
        if head[0] == "__group__":
            _, key, gname, *flag = head
            sec = S.SECTION_BY_KEY[key]
            g = next(x for x in sec.groups if x.name == gname)
            gtitle = i18n.group_title(lang, g)
            have = {it["slot"] for it in rec["data"]["sections"][key]["groups"].get(gname, [])}
            btns = [B(i18n.slot_title(lang, sid, f"{gtitle} {sid}"), f"slot:{sid}") for sid, _, _ in g.slots if sid not in have]
            # "check": the AI read this table; say how many columns it found, and ask whether one is missing
            question = (T("group_check_q", sec=i18n.section_title(lang, sec), g=gtitle.lower(), n=len(have))
                        if flag == ["check"] and not s.get("manual") else
                        T("group_add_q", sec=i18n.section_title(lang, sec), g=gtitle.lower()))
            return self._say(mid, question, btns + [b("btn_no_more", "slot:none")], rid)
        key, path = head
        f = self._field(key, path)
        v = S.get_path(rec["data"]["sections"][key], path)
        sec = S.SECTION_BY_KEY[key]
        left = len([q for q in s["queue"] if q[0] != "__group__"])
        # In French the field's name usually IS the printed label, so it is not repeated.
        head_line = T("head_line" if i18n.shows_printed_label(lang, key, path, f) else "head_line_short",
                      sec=i18n.section_title(lang, sec), title=i18n.path_title(lang, key, path), label=f.label)
        shown = i18n.value(lang, f, v["value"])
        btns = []
        if v["status"] == S.NEEDS_REVIEW and v["value"] is not None:
            conf = f"{v['confidence']:.0%}" if v["confidence"] is not None else T("conf_unknown")
            if (v.get("note") or "").startswith("always confirmed"):
                body = T("always_confirm", v=shown, conf=conf)
            else:
                body = T("not_sure", v=shown, conf=conf)
                if v.get("note"):
                    body += T("why", note=i18n.note(lang, v["note"]))
            btns.append(b("btn_correct", "ans:ok"))
        elif v["status"] == S.ILLEGIBLE:
            body = T("illegible_body")
        elif v["status"] == S.NEEDS_REVIEW and v.get("source") == "ai":
            conf = T("conf_paren", conf=f"{v['confidence']:.0%}") if v["confidence"] is not None else ""
            body = T("ai_note", note=(i18n.note(lang, v.get("note")) or T("default_note")).capitalize(), conf=conf)
        elif s.get("edit") and v["status"] in S.VALUE_STATUSES and v["value"] is not None:
            body = T("current_value", v=shown)
        else:
            body = T("what_written")
        if f.kind == S.BOOL:
            btns += [b("btn_ticked", "opt:0"), b("btn_not_ticked", "opt:1")]
            hint = ""
        elif f.kind == S.CHOICE:
            btns += [B(o, f"opt:{i}") for i, o in enumerate(f.options)]     # printed options: as on the form
            hint = ""
        elif f.kind == S.MULTI:
            btns.append(b("btn_none_ticked", "opt:none"))
            hint = T("hint_multi", opts=", ".join(f.options))
        elif f.kind == S.DATE:
            hint = T("hint_date")
        elif f.kind == S.BP:
            hint = T("hint_bp")
        elif f.kind in (S.INT, S.NUM):
            hint = T("hint_number", unit=f" ({i18n.unit(lang, f.unit)})" if f.unit else "")
        else:
            hint = T("hint_text")
        btns += [b("btn_blank", "ans:blank"), b("btn_unreadable", "ans:illegible"),
                 b("btn_na", "ans:na"), b("btn_unknown", "ans:unknown")]
        if s.get("manual"):
            btns += [b("btn_rest_blank", "ans:rest_blank"), b("btn_later", "ans:later")]
        elif not s.get("edit"):
            btns.append(b("btn_skip", "ans:skip"))
            if left >= BULK_MIN:
                btns.append(B(T("btn_see_all", n=left), "ans:list"))
        self._say(mid, f"{head_line}\n{body}\n{hint}".strip() + (T("left", n=left) if left > 1 else ""), btns, rid)

    def _set(self, rid, key, path, value_fv):
        rec = self.store.record(rid)
        data = rec["data"]
        S.set_path(data["sections"][key], path, value_fv)
        S.apply_blank_rules(key, data["sections"][key])
        self.store.update_record(rid, data=data)
        self.store.event("field_set_by_midwife", record_id=rid, section=key, path=path, status=value_fv["status"])

    def _m_followup(self, mid, s, text):
        store, rid = self.store, s["rid"]
        if not s.get("queue"):
            return self._menu(mid, rid)
        head = s["queue"][0]
        if text == "back":
            return self._menu(mid, rid)
        # ---- adding a repeating-group column (manual entry / edit)
        if head[0] == "__group__":
            _, key, gname, *flag = head
            once = flag == ["once"]
            if not text.startswith("slot:") or text == "slot:none":
                if text == "slot:none" and not once:           # "No more": this table is complete, do not ask again
                    data = store.record(rid)["data"]
                    data["sections"][key].setdefault("checked", {})[gname] = True
                    store.update_record(rid, data=data)
                s["queue"].pop(0)
                return self._ask(mid, s)
            slot = text[5:]
            sec = S.SECTION_BY_KEY[key]
            g = next(x for x in sec.groups if x.name == gname)
            rec = store.record(rid)
            data = rec["data"]
            items = data["sections"][key]["groups"].setdefault(gname, [])
            if slot in [sid for sid, _, _ in g.slots] and slot not in [it["slot"] for it in items]:
                items.append({"slot": slot, "fields": {f.key: S.fv(None, S.NEEDS_REVIEW, None, "none") for f in g.fields}})
                order = [sid for sid, _, _ in g.slots]
                items.sort(key=lambda it: order.index(it["slot"]))
                store.update_record(rid, data=data)
                new = [[key, f"{gname}[{slot}].{f.key}"] for f in g.fields]
                s["queue"] = new + (s["queue"][1:] if once else s["queue"])
                s["manual"] = True
            return self._ask(mid, s)
        if text == "ans:list" and not s.get("manual") and not s.get("edit"):
            return self._bulk(mid, s)
        key, path = head
        f = self._field(key, path)
        cur = S.get_path(store.record(rid)["data"]["sections"][key], path)
        new = None
        if s.get("confirm") is not None:
            if text == "ans:keep":
                new = S.fv(s.pop("confirm"), S.KNOWN, 1.0, "midwife", "outside expected range, confirmed by midwife")
            else:
                s.pop("confirm")
                store.set_session(mid, s)
                if text == "ans:retype":
                    return self._say(mid, self._t(mid, "retype"))
        if new is not None:
            pass
        elif text == "ans:ok" and cur["value"] is not None:
            new = S.fv(cur["value"], S.KNOWN, 1.0, "midwife", "confirmed by midwife")
        elif text == "ans:blank":
            new = S.fv(None, S.NOT_PROVIDED, 1.0, "midwife")
        elif text == "ans:illegible":
            new = S.fv(None, S.ILLEGIBLE, 1.0, "midwife", "unreadable on the paper itself")
        elif text == "ans:na":
            new = S.fv(None, S.NOT_APPLICABLE, 1.0, "midwife")
        elif text == "ans:unknown":
            new = S.fv(None, S.UNKNOWN, 1.0, "midwife")
        elif text == "ans:skip":
            s["queue"].pop(0)
            return self._ask(mid, s)
        elif text == "ans:later":
            store.set_session(mid, {"mode": "idle", "rid": rid})
            return self._say(mid, self._t(mid, "saved_later", tag=rid[:SHORT]), (), rid)
        elif text == "ans:rest_blank":
            rec = store.record(rid)
            data = rec["data"]
            scope = path.split("].")[0] + "]." if "[" in path else None
            n = 0
            for q in list(s["queue"]):
                if q[0] != key or (scope and not q[1].startswith(scope)):
                    continue
                if S.get_path(data["sections"][key], q[1])["status"] == S.NEEDS_REVIEW:
                    S.set_path(data["sections"][key], q[1], S.fv(None, S.NOT_PROVIDED, 1.0, "midwife", "declared blank by midwife"))
                    n += 1
                s["queue"].remove(q)
            S.apply_blank_rules(key, data["sections"][key])
            store.update_record(rid, data=data)
            store.event("fields_declared_blank", record_id=rid, section=key, count=n)
            return self._ask(mid, s)
        elif text.startswith("opt:"):
            if f.kind == S.BOOL and text in ("opt:0", "opt:1"):
                new = S.fv(S.TICKED if text == "opt:0" else S.NOT_TICKED, S.KNOWN, 1.0, "midwife")
            elif f.kind == S.CHOICE and text[4:].isdigit() and int(text[4:]) < len(f.options):
                new = S.fv(f.options[int(text[4:])], S.KNOWN, 1.0, "midwife")
            elif f.kind == S.MULTI and text == "opt:none":
                new = S.fv([], S.KNOWN, 1.0, "midwife")
        elif not text.startswith(tuple(_PAYLOAD_PREFIXES)):
            clean, found = scrub(text, S.SCRUB_ALLOW.get(f.key, ()))
            if found:
                self._say(mid, self._t(mid, "identifier_removed"))
            value, ok = canon(f, clean)
            if not ok or value in (None, "", []) and f.kind != S.MULTI:
                return self._say(mid, self._t(mid, "not_understood", kind=self._t(mid, f"kind.{f.kind}")))
            if not plausible(f, value):
                s["confirm"] = value
                store.set_session(mid, s)
                return self._say(mid, self._t(mid, "out_of_range", v=i18n.value(self.lang(mid), f, value)),
                                 [self._b(mid, "btn_keep", "ans:keep"), self._b(mid, "btn_retype", "ans:retype")])
            new = S.fv(value, S.KNOWN, 1.0, "midwife")
        if new is None:
            return self._say(mid, self._t(mid, "use_button"))
        self._set(rid, key, path, new)
        s["queue"].pop(0)
        self._ask(mid, s)

    # ------------------------------------------- all uncertain fields at once
    def _bulk_entries(self, rec, queue):
        """The queued fields that are still uncertain, with their current value."""
        out = []
        for q in queue:
            if q[0] == "__group__":
                continue
            v = S.get_path(rec["data"]["sections"][q[0]], q[1])
            if v["status"] == S.NEEDS_REVIEW or (v["status"] == S.ILLEGIBLE and v["source"] == "ai"):
                out.append((q, self._field(q[0], q[1]), v))
        return out

    def _bulk(self, mid, s):
        """One message listing every field the agent is unsure about, instead of one
        question each.  Nothing is accepted by default: the midwife either confirms
        the values she sees listed, or picks a number to correct that field.  Fields
        without a readable value cannot be confirmed in one go; they are asked next."""
        rid, lang = s["rid"], self.lang(mid)
        entries = self._bulk_entries(self.store.record(rid), s["queue"])
        if not entries:
            return self._ask(mid, s)
        lines = [i18n.t(lang, "bulk_head", n=len(entries))]
        with_value = 0
        for i, (q, f, v) in enumerate(entries, 1):
            name = f"{i18n.section_title(lang, S.SECTION_BY_KEY[q[0]])} › {i18n.path_title(lang, q[0], q[1])}"
            if v["value"] is not None and v["status"] == S.NEEDS_REVIEW:
                with_value += 1
                conf = f"{v['confidence']:.0%}" if v["confidence"] is not None else i18n.t(lang, "conf_unknown")
                lines.append(i18n.t(lang, "bulk_value", i=i, name=name, v=i18n.value(lang, f, v["value"]), conf=conf))
            elif v["status"] == S.ILLEGIBLE:
                lines.append(i18n.t(lang, "bulk_illegible", i=i, name=name))
            else:
                lines.append(i18n.t(lang, "bulk_nothing", i=i, name=name))
        lines.append(i18n.t(lang, "bulk_foot"))
        btns = [B(i18n.t(lang, "btn_bulk_ok", n=with_value), "bulk:ok")] if with_value else []
        self.store.set_session(mid, {**s, "mode": "bulk", "paths": [q for q, _, _ in entries]})
        self._say(mid, "\n".join(lines), btns + [B(i18n.t(lang, "btn_bulk_step"), "bulk:step")], rid)

    def _m_bulk(self, mid, s, text):
        rid, paths = s["rid"], s.pop("paths", [])
        s["mode"] = "followup"
        if text == "bulk:ok":
            rec = self.store.record(rid)
            n = 0
            for q, f, v in self._bulk_entries(rec, paths):
                if v["value"] is not None and v["status"] == S.NEEDS_REVIEW:
                    self._set(rid, q[0], q[1], S.fv(v["value"], S.KNOWN, 1.0, "midwife", "confirmed by midwife"))
                    n += 1
            self.store.event("fields_confirmed_together", record_id=rid, actor=mid, count=n)
            self._say(mid, self._t(mid, "bulk_done", n=n), (), rid)
            return self._ask(mid, s)
        if text.isdigit() and 1 <= int(text) <= len(paths):
            chosen = paths[int(text) - 1]                      # correct this one now, then carry on
            s["queue"] = [chosen] + [q for q in s["queue"] if q != chosen]
            return self._ask(mid, s)
        if text in ("bulk:step", "back"):
            return self._ask(mid, s)
        s["mode"], s["paths"] = "bulk", paths
        self.store.set_session(mid, s)
        self._say(mid, self._t(mid, "bulk_hint", n=len(paths)))

    # ------------------------------------------------------- manual entry
    def _start_manual(self, mid, rid):
        store = self.store
        rec = store.record(rid)
        if rec is None or rec["midwife_id"] != mid:
            return self._say(mid, self._t(mid, "not_found"))
        if rec["state"] in (L.PENDING_AI, L.MANUAL_REVIEW_REQUIRED):
            store.transition(rid, L.NEEDS_REVIEW, actor=mid, reason="manual entry started")
        elif rec["state"] != L.NEEDS_REVIEW:
            return self._say(mid, self._t(mid, "manual_not_needed", tag=rid[:SHORT], state=rec["state"]))
        self._say(mid, self._t(mid, "manual_intro"), (), rid)
        self._manual_next_page(mid, rid)

    def _manual_next_page(self, mid, rid):
        store = self.store
        rec = store.record(rid)
        for p in store.pages(rid):
            if p["status"] == L.AI_PROCESSED:
                continue
            if not p["page_type"] or p["page_type"] not in rec["data"]["sections"]:
                if not p["page_type"]:
                    store.set_session(mid, {"mode": "manual_ptype", "rid": rid, "page_id": p["id"]})
                    return self._say(mid, self._t(mid, "which_registry_page", n=p["seq"]),
                                     [B(self._sec(mid, sec), f"ptype:{sec.key}") for sec in S.SECTIONS], rid)
                data = rec["data"]
                data["sections"][p["page_type"]] = S.empty_section(p["page_type"])
                store.update_record(rid, data=data)
                rec = store.record(rid)
            if p["status"] == L.PENDING_AI or p["status"] == L.PROCESSING_FAILED:
                store.set_page(p["id"], status=L.MANUAL_REVIEW_REQUIRED)
        q = []
        for sec in S.SECTIONS:
            data = rec["data"]["sections"].get(sec.key)
            if not data:
                continue
            q += [[sec.key, p] for p in pipeline.uncertain_paths(sec.key, data)]
            if any(v["source"] == "none" for v in data["fields"].values()):
                q += [["__group__", sec.key, g.name] for g in sec.groups]
        s = {"mode": "followup", "rid": rid, "queue": q, "manual": True}
        store.set_session(mid, s)
        self._ask(mid, s)

    def _m_manual_ptype(self, mid, s, text):
        if not text.startswith("ptype:") or text[6:] not in S.SECTION_BY_KEY:
            return self._say(mid, self._t(mid, "choose_page_button"))
        self.store.set_page(s["page_id"], page_type=text[6:])
        self._manual_next_page(mid, s["rid"])

    def _retry_ai(self, mid, rid):
        store = self.store
        rec = store.record(rid)
        if rec is None or rec["state"] != L.MANUAL_REVIEW_REQUIRED:
            return self._say(mid, self._t(mid, "nothing_to_retry"))
        for p in store.pages(rid):
            if p["status"] in (L.PROCESSING_FAILED, L.MANUAL_REVIEW_REQUIRED):
                store.set_page(p["id"], status=L.PENDING_AI, attempts=0)
        store.update_record(rid, next_attempt_at=None)
        store.transition(rid, L.PENDING_AI, actor=mid, reason="midwife asked for another AI attempt")
        if self.worker is not None:
            self.worker.retry_ai_now()
        self._say(mid, self._t(mid, "retry_now" if self._ai() else "retry_later"), (), rid)

    # ------------------------------------------------------ re-digitisation
    def _dup_existing(self, rec):
        code = linking.code_from_record(rec["data"], tentative=True) or rec["linking_code"]
        return self.worker.find_duplicate(rec["id"], code) if self.worker else None

    def _dup_start(self, mid, rec):
        rid = rec["id"]
        old = self._dup_existing(rec)
        if old is None:
            self.store.transition(rid, L.NEEDS_REVIEW, actor=mid, reason="duplicate no longer found")
            return self._begin_review(mid, rid)
        secs = ", ".join(self._sec(mid, S.SECTION_BY_KEY[k]) for k in old["data"]["sections"])
        self.store.set_session(mid, {"mode": "dup", "rid": rid, "old": old["id"]})
        self._say(mid, self._t(mid, "dup_q", code=old["linking_code"], date=old["created_at"][:10], secs=secs),
                  [self._b(mid, "btn_dup_update", "dup:update"), self._b(mid, "btn_dup_different", "dup:different")], rid)

    def _diff(self, key, old, new):
        out = []
        new_paths = {p: v for p, _, v in S.iter_fields(key, new)}
        for p, f, v in S.iter_fields(key, old):
            n = new_paths.get(p)
            if n is None or n["value"] != v["value"] or (v["value"] is None and n["status"] != v["status"]
                                                         and S.NEEDS_REVIEW not in (n["status"], v["status"])):
                out.append((p, f, v, n))
        old_paths = {p for p, _, _ in S.iter_fields(key, old)}
        out += [(p, f, None, v) for p, f, v in S.iter_fields(key, new) if p not in old_paths]
        return out

    def _m_dup(self, mid, s, text):
        store, rid = self.store, s["rid"]
        rec, old = store.record(rid), store.record(s["old"])
        if text == "dup:different":
            store.transition(rid, L.NEEDS_REVIEW, actor=mid, reason="midwife: not a duplicate")
            return self._begin_review(mid, rid)
        if text == "dup:update":
            both = [k for k in S.PAGE_TYPES if k in rec["data"]["sections"] and k in old["data"]["sections"]
                    and self._diff(k, old["data"]["sections"][k], rec["data"]["sections"][k])]
            s.update(mode="dup_section", todo=both)
            store.set_session(mid, s)
            return self._dup_next(mid, s)
        self._dup_start(mid, rec)

    def _fmt(self, mid, f, v):
        lang = self.lang(mid)
        if v is None:
            return i18n.t(lang, "absent")
        return f"“{i18n.value(lang, f, v['value'])}”" if v["value"] is not None else i18n.t(lang, f"status.{v['status']}")

    def _dup_next(self, mid, s):
        store, rid = self.store, s["rid"]
        rec, old = store.record(rid), store.record(s["old"])
        if not s["todo"]:
            data = rec["data"]
            for k, sec in old["data"]["sections"].items():      # pages not photographed again are kept
                data["sections"].setdefault(k, sec)
            store.update_record(rid, data=data, supersedes=old["id"])
            store.transition(rid, L.NEEDS_REVIEW, actor=mid, reason="update of an existing record chosen by midwife")
            self._say(mid, self._t(mid, "dup_kept"), (), rid)
            return self._begin_review(mid, rid)
        key = s["todo"][0]
        sec = S.SECTION_BY_KEY[key]
        diffs = self._diff(key, old["data"]["sections"][key], rec["data"]["sections"][key])
        lines = [self._t(mid, "dup_diff_head", sec=self._sec(mid, sec), n=len(diffs))]
        for p, f, o, n in diffs[:6]:
            lines.append(self._t(mid, "dup_diff_line", title=i18n.path_title(self.lang(mid), key, p),
                                 old=self._fmt(mid, f, o), new=self._fmt(mid, f, n)))
        if len(diffs) > 6:
            lines.append(self._t(mid, "dup_more", n=len(diffs) - 6))
        self._say(mid, "\n".join(lines) + self._t(mid, "dup_which"),
                  [self._b(mid, "btn_use_new", "dupsec:new"), self._b(mid, "btn_keep_saved", "dupsec:keep")], rid)

    def _m_dup_section(self, mid, s, text):
        if text not in ("dupsec:new", "dupsec:keep"):
            return self._dup_next(mid, s)
        key = s["todo"].pop(0)
        if text == "dupsec:keep":
            rec, old = self.store.record(s["rid"]), self.store.record(s["old"])
            data = rec["data"]
            data["sections"][key] = old["data"]["sections"][key]
            self.store.update_record(s["rid"], data=data)
        self.store.event("redigitisation_choice", record_id=s["rid"], section=key, choice=text[7:])
        self.store.set_session(mid, s)
        self._dup_next(mid, s)

    # ------------------------------------------------------------ linking
    def _describe(self, mid, p):
        sm = p["summary"]
        n = self.store.one("SELECT count(*) AS n FROM records WHERE patient_id=? AND superseded_by IS NULL", (p["id"],))["n"]
        bits = [self._t(mid, "d_code", code=p["linking_code"] or "-")]
        if sm.get("age") is not None:
            bits.append(self._t(mid, "d_age", age=sm["age"]))
        if sm.get("province"):
            bits.append(str(sm["province"]))
        if sm.get("lmp_date"):
            d = sm["lmp_date"]
            bits.append(self._t(mid, "d_lmp", date=f"{d[8:10]}/{d[5:7]}/{d[:4]}"))
        bits.append(self._t(mid, "d_records", n=n))
        return " · ".join(bits)

    def _start_linking(self, mid, rid, code_given=False):
        store = self.store
        rec = store.record(rid)
        code = linking.code_from_record(rec["data"]) or rec["linking_code"]
        if not code and not code_given:
            store.set_session(mid, {"mode": "link_code", "rid": rid})
            return self._say(mid, self._t(mid, "link_code_q"), [self._b(mid, "btn_no_code", "link:nocode")], rid)
        if code != rec["linking_code"]:
            store.update_record(rid, linking_code=code)
        cands = linking.candidates(store.patients(), code, linking.summary_from_record(rec["data"]))
        if rec["supersedes"]:
            prev = store.record(rec["supersedes"])
            if prev and prev["patient_id"] and prev["patient_id"] not in [c["patient"]["id"] for c in cands]:
                cands.insert(0, {"patient": store.patient(prev["patient_id"]), "reasons": ["the record you are updating"]})
        ids = [c["patient"]["id"] for c in cands]
        store.set_session(mid, {"mode": "link", "rid": rid, "cands": ids})
        if cands:
            lines = [self._t(mid, "link_which", code=code or "-")]
            for i, c in enumerate(cands, 1):
                lines.append(self._t(mid, "link_cand", i=i, desc=self._describe(mid, c["patient"]),
                                     reasons=i18n.reasons(self.lang(mid), c["reasons"])))
            btns = [self._b(mid, "btn_patient", f"link:{pid}", i=i) for i, pid in enumerate(ids, 1)]
            btns += [self._b(mid, "btn_none_new", "link:new"), self._b(mid, "btn_unsure", "link:unsure")]
        else:
            lines = [self._t(mid, "link_none", code=code or "-")]
            btns = [self._b(mid, "btn_create_new", "link:new"), self._b(mid, "btn_unsure", "link:unsure")]
        self._say(mid, "\n".join(lines), btns, rid)

    def _m_link_code(self, mid, s, text):
        rid = s["rid"]
        if text != "link:nocode":
            if text.startswith(tuple(_PAYLOAD_PREFIXES)):
                return self._say(mid, self._t(mid, "type_code"))
            clean, found = scrub(text)
            if found or not linking.normalize_code(clean):
                return self._say(mid, self._t(mid, "bad_code"))
            self.store.update_record(rid, linking_code=clean.strip())
            self.store.event("linking_code_entered", record_id=rid, actor=mid)
        self._start_linking(mid, rid, code_given=True)

    def _m_link(self, mid, s, text):
        store, rid = self.store, s["rid"]
        rec = store.record(rid)
        if rec["state"] != L.VALIDATED:
            return self._say(mid, self._t(mid, "registry_is", state=rec["state"]))
        summary = linking.summary_from_record(rec["data"])
        if text == "link:unsure":
            store.transition(rid, L.MANUAL_REVIEW_REQUIRED, actor=mid, reason="match undecided by midwife")
            store.set_session(mid, {"mode": "idle", "rid": rid})
            return self._say(mid, self._t(mid, "unsure_saved"), [self._b(mid, "btn_decide_now", f"review:{rid}")], rid)
        if text == "link:new":
            pid = store.create_patient(mid, rec["linking_code"], summary)
            how = "new patient created by midwife"
        elif text.startswith("link:") and text[5:] in s.get("cands", []):
            pid = text[5:]
            p = store.patient(pid)
            merged = {k: (p["summary"].get(k) if p["summary"].get(k) is not None else summary.get(k)) for k in summary}
            store.update_patient(pid, p["linking_code"] or rec["linking_code"], merged)
            how = "existing patient chosen by midwife"
        else:
            return self._start_linking(mid, rid, code_given=True)
        store.update_record(rid, patient_id=pid)
        store.transition(rid, L.PATIENT_MATCHED, actor=mid, reason=how)
        if rec["supersedes"]:
            old = store.record(rec["supersedes"])
            store.update_record(old["id"], superseded_by=rid)
            store.update_record(rid, version=old["version"] + 1)
            store.event("record_superseded", record_id=old["id"], by=rid)
        store.transition(rid, L.REGISTERED, actor=mid, reason="saved to the patient's profile")
        store.enqueue_sync(rid, store.record(rid)["version"])
        store.set_session(mid, {"mode": "idle", "rid": rid})
        n = store.one("SELECT count(*) AS n FROM records WHERE patient_id=? AND superseded_by IS NULL", (pid,))["n"]
        self._say(mid, self._t(mid, "registered_sync" if self._online() else "registered_offline", n=n), (), rid)

    # ------------------------------------------------------------- reports
    def _status(self, mid):
        recs = self.store.records("midwife_id=? AND superseded_by IS NULL", (mid,))
        if not recs:
            return self._say(mid, self._t(mid, "no_registry_yet") + self._t(mid, "help"))
        ai = self._t(mid, "ai_available") if self._ai() else f"{self._t(mid, 'ai_not_available')} ({self._why(mid)})"
        lines = [self._t(mid, "status_head", net=self._t(mid, "online" if self._online() else "offline"), ai=ai)]
        btns = []
        for r in recs[-12:]:
            lines.append(self._t(mid, "status_line", tag=r["id"][:SHORT], state=r["state"], n=len(self.store.pages(r["id"])))
                         + (" - " + self._t(mid, "d_code", code=r["linking_code"]) if r["linking_code"] else ""))
            if r["state"] not in (L.REGISTERED, L.SYNCED, L.SYNC_FAILED, L.PATIENT_MATCHED):
                btns.append(self._b(mid, "btn_open", f"review:{r['id']}", tag=r["id"][:SHORT]))
        pend = len(self.store.outbox("pending"))
        lines.append(self._t(mid, "status_sync", n=pend))
        self._say(mid, "\n".join(lines), btns[:8])

    def _history(self, mid):
        pats = self.store.patients()
        if not pats:
            return self._say(mid, self._t(mid, "no_patient"))
        lines = []
        lang = self.lang(mid)
        for i, p in enumerate(pats, 1):
            lines.append(self._t(mid, "history_patient", i=i, desc=self._describe(mid, p)))
            for r in self.store.records("patient_id=? AND superseded_by IS NULL", (p["id"],)):
                secs = ", ".join(self._sec(mid, S.SECTION_BY_KEY[k]) for k in S.PAGE_TYPES if k in r["data"]["sections"])
                lines.append(f"   - {r['created_at'][:10]} · {r['state']} · {secs}")
                for it in r["data"]["sections"].get("pregnancy", {}).get("groups", {}).get("visits", []):
                    d = it["fields"].get("attended_date", {})
                    if d.get("status") == S.KNOWN:
                        lines.append(i18n.t(lang, "history_visit", slot=i18n.slot_title(lang, it["slot"]),
                                            date=f"{d['value'][8:10]}/{d['value'][5:7]}/{d['value'][:4]}"))
        self._say(mid, "\n".join(lines))


_PAYLOAD_PREFIXES = ("cmd:", "review:", "ans:", "opt:", "menu:", "sec:", "ptype:", "page:", "dup:", "dupsec:",
                     "link:", "slot:", "retry_ai:", "retry_sync:", "q:", "ai:", "lang:", "bulk:")
