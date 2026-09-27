from datetime import timedelta

from groundtruth import store


def _story(hn_id, **extra):
    return {"hn_id": hn_id, "title": f"Story {hn_id}", "url": f"https://example.com/{hn_id}",
            "score": 100, "comments": 10, **extra}


def _sourced_id(hn_id=1):
    store.insert_sourced([_story(hn_id)])
    return next(d["id"] for d in store.unannounced() if d["hn_id"] == hn_id)


def _age(db, draft_id, **delta):
    db[store.DRAFTS].update_one(
        {"_id": store._oid(draft_id)},
        {"$set": {"updated_at": store.now() - timedelta(**delta)}},
    )


# --- indexes & sourcing --------------------------------------------------------

def test_indexes(db):
    info = db[store.DRAFTS].index_information()
    assert any(ix["key"] == [("hn_id", 1)] and ix.get("unique") for ix in info.values())
    assert any(ix["key"] == [("expire_at", 1)] and ix.get("expireAfterSeconds") == 0
               for ix in info.values())


def test_insert_dedups_by_hn_id(db):
    assert store.insert_sourced([_story(1), _story(2)]) == 2
    # Re-running (e.g. a retried source job) skips known ids instead of failing.
    assert store.insert_sourced([_story(2), _story(3)]) == 1
    assert store.known_hn_ids([1, 2, 3, 4]) == {1, 2, 3}


def test_unannounced_in_rank_order_until_marked(db):
    store.insert_sourced([_story(30), _story(10), _story(20)])
    pending = store.unannounced()
    assert [d["hn_id"] for d in pending] == [30, 10, 20]
    store.mark_announced([d["id"] for d in pending[:2]])
    assert [d["hn_id"] for d in store.unannounced()] == [20]


def test_source_due(db):
    assert store.source_due()
    store.record_source_run()
    assert not store.source_due()
    assert store.source_due(force=True)
    db[store.META].update_one(
        {"_id": "source_job"},
        {"$set": {"last_run_at": store.now() - store.SOURCE_INTERVAL - timedelta(minutes=1)}},
    )
    assert store.source_due()


def test_timestamps_are_utc_aware(db):
    doc = store.get(_sourced_id())
    assert doc["created_at"].utcoffset() == timedelta(0)
    assert doc["expire_at"] - doc["updated_at"] == store.IN_FLIGHT_TTL


# --- lifecycle -------------------------------------------------------------------

def test_pick_claims_once(db):
    draft_id = _sourced_id()
    assert store.start_pick(draft_id)["status"] == store.DRAFTING
    # A Telegram resend or double tap while drafting: no second claim.
    assert store.start_pick(draft_id) is None


def test_stale_drafting_can_be_reclaimed(db):
    draft_id = _sourced_id()
    store.start_pick(draft_id)
    _age(db, draft_id, minutes=4)
    assert store.start_pick(draft_id) is None
    _age(db, draft_id, minutes=6)
    assert store.start_pick(draft_id)["status"] == store.DRAFTING


def test_failed_pick_can_be_retried(db):
    draft_id = _sourced_id()
    store.start_pick(draft_id)
    failed = store.fail_draft(draft_id, "Could not read the article")
    assert failed["status"] == store.FAILED and failed["error"]
    retried = store.start_pick(draft_id)
    assert retried["status"] == store.DRAFTING and retried["error"] is None


def test_draft_then_approve_is_permanent_and_terminal(db):
    draft_id = _sourced_id()
    store.start_pick(draft_id)
    drafted = store.finish_draft(draft_id, "brief", "post")
    assert (drafted["status"], drafted["brief"], drafted["draft"]) == (store.DRAFTED, "brief", "post")

    approved = store.approve(draft_id)
    assert approved["status"] == store.APPROVED
    assert approved["expire_at"] is None and approved["approved_at"] is not None
    # Nothing moves an approved record: repeat approve, reject, rewrite all miss.
    assert store.approve(draft_id) is None
    assert store.reject(draft_id) is None
    assert store.start_rewrite(draft_id) is None


def test_reject_keeps_record_for_dedup_window(db):
    draft_id = _sourced_id()
    store.start_pick(draft_id)
    store.finish_draft(draft_id, "brief", "post")
    rejected = store.reject(draft_id)
    assert rejected["status"] == store.REJECTED
    assert rejected["expire_at"] - rejected["updated_at"] == store.REJECTED_TTL
    assert store.approve(draft_id) is None


def test_rewrite_keeps_brief(db):
    draft_id = _sourced_id()
    store.start_pick(draft_id)
    store.finish_draft(draft_id, "brief", "post v1")
    claimed = store.start_rewrite(draft_id)
    assert claimed["status"] == store.DRAFTING and claimed["brief"] == "brief"
    assert store.start_rewrite(draft_id) is None
    assert store.finish_draft(draft_id, "brief", "post v2")["draft"] == "post v2"


def test_cannot_approve_before_drafted(db):
    draft_id = _sourced_id()
    assert store.approve(draft_id) is None
    store.start_pick(draft_id)
    assert store.approve(draft_id) is None


def test_unknown_or_malformed_ids(db):
    assert store.get("not-an-object-id") is None
    assert store.start_pick("not-an-object-id") is None
    assert store.approve("0" * 24) is None


def test_msg_id(db):
    draft_id = _sourced_id()
    store.set_msg_id(draft_id, 42)
    assert store.get(draft_id)["msg_id"] == 42
