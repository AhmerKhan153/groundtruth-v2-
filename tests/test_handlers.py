"""Handler flows against real MongoDB, with Telegram and the pipeline faked."""

import pytest

from groundtruth import handlers, pipeline, store, telegram
from groundtruth.config import TELEGRAM_CHAT_ID


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers = [], [], []
        self._next_id = 100

    def send_message(self, text, buttons=None):
        self._next_id += 1
        self.sent.append({"id": self._next_id, "text": text, "buttons": buttons})
        return self._next_id

    def edit_message(self, message_id, text, buttons=None):
        self.edits.append({"id": message_id, "text": text, "buttons": buttons})

    def answer_callback(self, callback_id, text=None):
        self.answers.append(text)


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    for name in ("send_message", "edit_message", "answer_callback"):
        monkeypatch.setattr(telegram, name, getattr(fake, name))
    return fake


@pytest.fixture
def stories(monkeypatch):
    items = [{"hn_id": i, "title": f"Story {i}", "url": f"https://example.com/{i}",
              "score": 100 - i, "comments": 5} for i in range(1, 9)]
    monkeypatch.setattr(handlers.hackernews, "fetch_candidates", lambda: list(items))
    return items


@pytest.fixture
def model(monkeypatch):
    """Fake pipeline: records calls; drafts are numbered."""
    calls = []

    def draft_from_url(title, url):
        calls.append(("draft", title))
        return "BRIEF", "Draft 1"

    def write_post(title, brief, previous_draft=None):
        calls.append(("rewrite", previous_draft))
        return f"Draft {len(calls)}"

    monkeypatch.setattr(pipeline, "draft_from_url", draft_from_url)
    monkeypatch.setattr(pipeline, "write_post", write_post)
    return calls


def tap(data, chat_id=None):
    handlers.handle_update({"update_id": 1, "callback_query": {
        "id": "cb", "data": data,
        "message": {"chat": {"id": int(chat_id or TELEGRAM_CHAT_ID)}}}})


def first_story_id():
    return str(store._drafts().find_one({"hn_id": 1})["_id"])


# --- source job --------------------------------------------------------------------------

def test_source_sends_top_stories_once_per_interval(db, tg, stories):
    assert handlers.source_job() == {"status": "sent", "count": 6}
    msg = tg.sent[0]
    assert msg["text"].startswith("Pick a story") and "Story 1" in msg["text"]
    assert [b[1] for row in msg["buttons"] for b in row][0].startswith("p:")
    assert handlers.source_job() == {"status": "skipped"}


def test_forced_run_skips_stories_already_shown(db, tg, stories):
    handlers.source_job()
    assert handlers.source_job(force=True) == {"status": "sent", "count": 2}  # stories 7, 8
    assert "Story 7" in tg.sent[1]["text"] and "Story 1" not in tg.sent[1]["text"]


def test_failed_send_is_retried_without_duplicates(db, tg, stories, monkeypatch):
    def down(*a, **k):
        raise telegram.TelegramError("sendMessage failed: timeout")

    monkeypatch.setattr(telegram, "send_message", down)
    with pytest.raises(telegram.TelegramError):
        handlers.source_job()
    assert store.source_due()  # the run was not recorded

    monkeypatch.setattr(telegram, "send_message", tg.send_message)
    assert handlers.source_job() == {"status": "sent", "count": 6}  # same six, resent
    assert store._drafts().count_documents({}) == 6


# --- taps ----------------------------------------------------------------------------------

def test_pick_drafts_into_one_message(db, tg, stories, model):
    handlers.source_job()
    tg.sent.clear()
    story = first_story_id()
    tap(f"p:{story}")

    assert tg.answers == ["Drafting…"]
    assert tg.sent[0]["text"] == "✍️ Drafting: Story 1"
    final = tg.edits[-1]
    assert final["id"] == tg.sent[0]["id"] and final["text"].startswith("Draft 1")
    assert "https://example.com/1" in final["text"]
    assert [b[1] for row in final["buttons"] for b in row] == [f"a:{story}", f"r:{story}", f"w:{story}"]
    assert store.get(story)["status"] == store.DRAFTED


def test_repeated_pick_is_a_toast(db, tg, stories, model):
    handlers.source_job()
    story = first_story_id()
    tap(f"p:{story}")
    tap(f"p:{story}")  # resend / double tap
    assert model == [("draft", "Story 1")]
    assert tg.answers[-1] == "Already drafted."


def test_pick_failure_can_be_retried(db, tg, stories, model, monkeypatch):
    handlers.source_job()
    story = first_story_id()

    def unreadable(title, url):
        raise pipeline.DraftError("The page has no readable article.")

    monkeypatch.setattr(pipeline, "draft_from_url", unreadable)
    tap(f"p:{story}")
    assert tg.edits[-1]["text"].startswith("⚠️ Skipped: Story 1")
    assert store.get(story)["status"] == store.FAILED

    monkeypatch.setattr(pipeline, "draft_from_url", lambda t, u: ("BRIEF", "Draft ok"))
    tap(f"p:{story}")
    assert store.get(story)["status"] == store.DRAFTED
    assert tg.edits[-1]["text"].startswith("Draft ok")


def test_approve_sends_copy_ready_post_once(db, tg, stories, model):
    handlers.source_job()
    story = first_story_id()
    tap(f"p:{story}")
    tap(f"a:{story}")

    assert tg.edits[-1]["buttons"] is None and tg.edits[-1]["text"].endswith("✅ Approved")
    assert tg.sent[-1] == {"id": tg.sent[-1]["id"], "text": "Draft 1", "buttons": None}
    record = store.get(story)
    assert record["status"] == store.APPROVED and record["expire_at"] is None

    sent_before = len(tg.sent)
    tap(f"a:{story}")
    assert len(tg.sent) == sent_before and tg.answers[-1] == "Already approved."


def test_reject(db, tg, stories, model):
    handlers.source_job()
    story = first_story_id()
    tap(f"p:{story}")
    tap(f"r:{story}")
    assert tg.edits[-1]["text"].endswith("❌ Rejected") and tg.edits[-1]["buttons"] is None
    assert store.get(story)["status"] == store.REJECTED


def test_rewrite_uses_previous_draft(db, tg, stories, model):
    handlers.source_job()
    story = first_story_id()
    tap(f"p:{story}")
    tap(f"w:{story}")
    assert model[-1] == ("rewrite", "Draft 1")
    assert tg.edits[-1]["text"].startswith("Draft 2") and tg.edits[-1]["buttons"]
    assert store.get(story)["draft"] == "Draft 2"


def test_failed_rewrite_restores_previous_draft(db, tg, stories, model, monkeypatch):
    handlers.source_job()
    story = first_story_id()
    tap(f"p:{story}")

    def down(*a, **k):
        raise pipeline.DraftError("The model call failed: timeout")

    monkeypatch.setattr(pipeline, "write_post", down)
    tap(f"w:{story}")
    last = tg.edits[-1]
    assert last["text"].startswith("Draft 1") and "Rewrite failed" in last["text"] and last["buttons"]
    assert store.get(story)["status"] == store.DRAFTED


def test_other_chats_are_ignored(db, tg, stories, model):
    handlers.source_job()
    tap(f"p:{first_story_id()}", chat_id=999)
    assert model == [] and tg.answers == []


def test_unexpected_errors_are_reported_not_raised(db, tg, stories, monkeypatch):
    handlers.source_job()

    def boom(draft_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(store, "start_pick", boom)
    tap(f"p:{first_story_id()}")  # must not raise
    assert "database down" in tg.sent[-1]["text"]


def test_unknown_or_expired_ids(db, tg):
    tap("p:" + "0" * 24)
    tap("zz:whatever")
    assert tg.answers == ["This story has expired.", None]
