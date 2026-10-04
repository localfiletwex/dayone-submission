import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The tests must give the same result whatever is exported in the shell that runs them (a key, a
# model name, WhatsApp settings...): none of it may reach the code under test, and no test may
# call a real service or open the real device database.
for _name in list(os.environ):
    if _name.startswith(("WHATSAPP_", "GEMINI_", "DAYONE_")) or _name == "GOOGLE_API_KEY":
        del os.environ[_name]
os.environ["DAYONE_EXTRACTOR"] = "fake"

from app.conversation import Agent                      # noqa: E402
from app.extractors.fake import FakeExtractor           # noqa: E402
from app.storage import Store                           # noqa: E402
from app.sync import InMemorySyncTarget                 # noqa: E402
from app.worker import Connectivity, Worker             # noqa: E402

PIN = "4321"


def img(n: int) -> bytes:
    """A distinct fake 'photo' (the fake extractor never decodes it)."""
    return b"\xff\xd8FAKEJPEG" + bytes([n]) * 64


class Rig:
    def __init__(self, directory, extractor=None, online=True, threshold=0.85):
        self.dir = directory
        self.store = Store(directory, PIN)
        self.extractor = extractor or FakeExtractor()
        self.server = InMemorySyncTarget()
        self.net = Connectivity(online)
        self.worker = Worker(self.store, self.extractor, self.server, self.net, threshold=threshold)
        self.agent = Agent(self.store, self.worker, {"value": threshold, "calibrated": False})
        self.worker.notifier = self.agent
        self.mw = self.store.user("midwife1")

    def photo(self, n=1):
        return self.agent.handle_image(self.mw, img(n))

    def say(self, text):
        return self.agent.handle_text(self.mw, text)

    def press(self, label_part):
        """Press the button of the last bot message whose label contains label_part."""
        last = self.prompt()
        for b in last["buttons"]:
            if label_part.lower() in b["label"].lower():
                return self.say(b["payload"])
        raise AssertionError(f"no button {label_part!r} in {[b['label'] for b in last['buttons']]}: {last['text']}")

    def prompt(self):
        """The message the midwife can act on: the latest bot message with buttons
        (a later notification without buttons does not hide it), as in the UI."""
        bots = [m for m in self.store.messages(self.mw["id"]) if m["sender"] == "bot"]
        return next((m for m in reversed(bots) if m["buttons"]), bots[-1])

    def last(self):
        return [m for m in self.store.messages(self.mw["id"]) if m["sender"] == "bot"][-1]

    def tick(self):
        # ignore retry back-off in tests
        self.store.db.execute("UPDATE records SET next_attempt_at=NULL")
        return self.worker.tick()

    def state(self, rid):
        return self.store.record(rid)["state"]

    def rid(self):
        return self.store.session(self.mw["id"])["rid"]

    def answer_all(self, limit=400):
        """Answer every follow-up: confirm when possible, else declare blank."""
        for _ in range(limit):
            m = self.prompt()
            labels = [b["label"] for b in m["buttons"]]
            if any("Confirm" in x or "Review remaining" in x for x in labels) or not labels:
                return
            if any("correct" in x for x in labels):
                self.press("correct")
            elif any(x == "No more" for x in labels):
                self.press("No more")
            else:
                self.press("Blank on paper")
        raise AssertionError("follow-ups did not finish")

    def register(self, choice="new"):
        """Confirm, then take the match decision (default: create a new patient)."""
        self.answer_all()
        self.press("Confirm")
        if any("no code" in b["label"] for b in self.prompt()["buttons"]):
            self.press("no code")
        return self.press(choice)

    def capture(self, *ns, ptype=None):
        for n in ns:
            self.photo(n)
        rid = self.rid()
        self.say("cmd:done")
        return rid

    def reopen(self):
        """Simulate an app restart: new Store/Worker/Agent on the same files."""
        self.store.close()
        self.store = Store(self.dir, PIN)
        self.worker = Worker(self.store, self.extractor, self.server, self.net, threshold=self.worker.threshold)
        self.agent = Agent(self.store, self.worker)
        self.worker.notifier = self.agent
        self.mw = self.store.user("midwife1")


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path / "device")
    yield r
    r.store.close()


def photo_bytes(kind: str = "sharp", size=(900, 1270)) -> bytes:
    """A synthetic 'registry page' as real JPEG bytes: ruled lines and blocks of
    'writing' on light paper.  kind: sharp | blurred | dark | tiny."""
    import io
    import random
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(7)
    img = Image.new("L", size, 207)
    d = ImageDraw.Draw(img)
    for y in range(60, size[1] - 40, 38):
        d.line([(50, y), (size[0] - 50, y)], fill=120, width=1)
        x = 60
        while x < size[0] - 120:
            w = rnd.randint(8, 46)
            if rnd.random() < 0.7:
                d.rectangle([x, y - 16, x + w, y - 14 + rnd.randint(1, 3)], fill=25)
                d.line([(x, y - 22), (x + w // 2, y - 6)], fill=25, width=2)
            x += w + rnd.randint(6, 18)
    if kind == "blurred":
        img = img.filter(ImageFilter.GaussianBlur(3))
    elif kind == "dark":
        img = img.point(lambda p: int(p * 0.35))
    elif kind == "tiny":
        img = img.resize((360, 508))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()
