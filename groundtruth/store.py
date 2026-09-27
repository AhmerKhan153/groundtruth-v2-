"""MongoDB store: the draft lifecycle and the source job's run marker.

The `status` field is the single source of truth, and every status change is ONE
atomic `find_one_and_update` conditioned on the current status:

    sourced ──pick──► drafting ──ok──► drafted ──approve──► approved   (terminal)
       ▲                 │               │
       └─(tap again)─ failed ◄──fail─────┤    ├──reject──► rejected
                                         └──rewrite─► drafting

That condition is what makes the webhook safe. Telegram resends an update when a
reply is slow, and a user can double-tap: the second claim matches nothing,
returns None, and the caller exits without repeating the work.

Retention: in-flight records expire via a TTL index on `expire_at`; `approved`
clears it and is the only thing kept forever. The unique `hn_id` index doubles as
dedup, so a story you've already been shown doesn't come back next run.

Nothing connects at import. The client is built on first use and reused across
requests on a warm instance; indexes are created by scripts/init_db.py.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

from groundtruth.config import MONGODB_DB_NAME, MONGODB_URI

SOURCED = "sourced"      # shown in a pick list, not drafted yet
DRAFTING = "drafting"    # a request is fetching / calling the model right now
DRAFTED = "drafted"      # draft sent to Telegram, awaiting your decision
APPROVED = "approved"    # you approved it; kept forever, nothing reads it back
REJECTED = "rejected"    # you rejected it; kept briefly for dedup
FAILED = "failed"        # no grounded draft was possible; tap again to retry

# Buttons stay valid for ~3 cycles, and hn_id keeps blocking repeats meanwhile.
IN_FLIGHT_TTL = timedelta(days=7)
REJECTED_TTL = timedelta(days=14)
# A `drafting` record older than this belongs to a request that died (instance
# killed mid-draft); a new tap may take it over. Longer than the worst-case pick.
STALE_CLAIM_AFTER = timedelta(minutes=5)
# The scheduler fires daily; this turns it into every other day, and a failed
# run is naturally retried the next day.
SOURCE_INTERVAL = timedelta(hours=44)

DRAFTS = "drafts"
META = "meta"
_SOURCE_JOB = "source_job"
_DUPLICATE_KEY = 11000

_client: Optional[MongoClient] = None


def _db():
    global _client
    if _client is None:
        # tz_aware: datetimes come back as aware UTC, so comparisons with now()
        # are correct regardless of the host timezone.
        _client = MongoClient(
            MONGODB_URI, serverSelectionTimeoutMS=5000, maxPoolSize=5, tz_aware=True
        )
    return _client[MONGODB_DB_NAME]


def _drafts() -> Collection:
    return _db()[DRAFTS]


def now() -> datetime:
    # Aware UTC. The old naive datetime.now() was local time, which Mongo's TTL
    # monitor reads as UTC -- records expired hours early or late.
    return datetime.now(timezone.utc)


def _expiry_for(status: str, at: datetime) -> Optional[datetime]:
    if status == APPROVED:
        return None
    if status == REJECTED:
        return at + REJECTED_TTL
    return at + IN_FLIGHT_TTL


def _with_id(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is not None:
        doc["id"] = str(doc["_id"])
    return doc


def _oid(draft_id: str) -> Optional[ObjectId]:
    try:
        return ObjectId(draft_id)
    except (InvalidId, TypeError):
        return None


def ensure_indexes() -> None:
    """Idempotent; run by scripts/init_db.py, never at import."""
    _drafts().create_index("hn_id", unique=True)
    # expireAfterSeconds=0: delete each doc once its absolute `expire_at` passes.
    # Docs with expire_at null/missing are never deleted.
    _drafts().create_index("expire_at", expireAfterSeconds=0)
    _drafts().create_index([("status", 1), ("announced", 1)])


# --- sourcing ----------------------------------------------------------------

def known_hn_ids(hn_ids: Iterable[int]) -> Set[int]:
    """The subset of `hn_ids` already stored in any status."""
    cursor = _drafts().find({"hn_id": {"$in": list(hn_ids)}}, {"hn_id": 1})
    return {doc["hn_id"] for doc in cursor}


def insert_sourced(stories: List[Dict[str, Any]]) -> int:
    """Store new stories as `sourced`, unannounced. Returns how many were new.

    A story already stored (by hn_id) is skipped rather than failing the batch,
    so re-running after a partial failure is safe.
    """
    if not stories:
        return 0
    at = now()
    docs = [
        {
            "hn_id": story["hn_id"],
            "title": story.get("title"),
            "url": story.get("url"),
            "score": story.get("score", 0),
            "comments": story.get("comments", 0),
            "status": SOURCED,
            "announced": False,
            "brief": None,
            "draft": None,
            "msg_id": None,
            "error": None,
            "created_at": at,
            "updated_at": at,
            "approved_at": None,
            "expire_at": _expiry_for(SOURCED, at),
        }
        for story in stories
    ]
    try:
        return len(_drafts().insert_many(docs, ordered=False).inserted_ids)
    except BulkWriteError as exc:
        errors = exc.details.get("writeErrors", [])
        if any(err.get("code") != _DUPLICATE_KEY for err in errors):
            raise
        return exc.details.get("nInserted", 0)


def unannounced() -> List[Dict[str, Any]]:
    """Sourced stories whose pick list never went out, in ranked (insert) order.

    Includes leftovers from a previous attempt whose Telegram send failed.
    """
    cursor = _drafts().find({"status": SOURCED, "announced": False}).sort("_id", 1)
    return [_with_id(doc) for doc in cursor]


def mark_announced(draft_ids: Iterable[str]) -> None:
    oids = [oid for oid in (_oid(d) for d in draft_ids) if oid is not None]
    if oids:
        _drafts().update_many({"_id": {"$in": oids}}, {"$set": {"announced": True}})


def source_due(force: bool = False) -> bool:
    """True if the last successful source run is older than SOURCE_INTERVAL."""
    if force:
        return True
    meta = _db()[META].find_one({"_id": _SOURCE_JOB})
    last = meta.get("last_run_at") if meta else None
    return last is None or now() - last >= SOURCE_INTERVAL


def record_source_run() -> None:
    """Call only after the pick list was delivered, so a failed run retries."""
    _db()[META].update_one(
        {"_id": _SOURCE_JOB}, {"$set": {"last_run_at": now()}}, upsert=True
    )


# --- lifecycle -----------------------------------------------------------------

def get(draft_id: str) -> Optional[Dict[str, Any]]:
    oid = _oid(draft_id)
    return _with_id(_drafts().find_one({"_id": oid})) if oid else None


def _transition(
    draft_id: str,
    from_statuses: Set[str],
    to_status: str,
    reclaim_stale: bool = False,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    """Atomically move a record between statuses; None if it wasn't eligible.

    None means someone else already did this (a resend, a double tap, a stale
    button) and the caller must stop.
    """
    oid = _oid(draft_id)
    if oid is None:
        return None
    at = now()
    eligible: List[Dict[str, Any]] = [{"status": {"$in": sorted(from_statuses)}}]
    if reclaim_stale:
        eligible.append({"status": DRAFTING, "updated_at": {"$lt": at - STALE_CLAIM_AFTER}})
    update = {
        "status": to_status,
        "updated_at": at,
        "expire_at": _expiry_for(to_status, at),
        **fields,
    }
    doc = _drafts().find_one_and_update(
        {"_id": oid, "$or": eligible},
        {"$set": update},
        return_document=ReturnDocument.AFTER,
    )
    return _with_id(doc)


def start_pick(draft_id: str) -> Optional[Dict[str, Any]]:
    """Claim a story for drafting. A failed story can be retried by tapping again."""
    return _transition(draft_id, {SOURCED, FAILED}, DRAFTING, reclaim_stale=True, error=None)


def start_rewrite(draft_id: str) -> Optional[Dict[str, Any]]:
    return _transition(draft_id, {DRAFTED}, DRAFTING, reclaim_stale=True)


def finish_draft(draft_id: str, brief: str, draft: str) -> Optional[Dict[str, Any]]:
    return _transition(draft_id, {DRAFTING}, DRAFTED, brief=brief, draft=draft, error=None)


def fail_draft(draft_id: str, error: str) -> Optional[Dict[str, Any]]:
    return _transition(draft_id, {DRAFTING}, FAILED, error=error)


def approve(draft_id: str) -> Optional[Dict[str, Any]]:
    """Terminal: the record is kept forever and nothing acts on it afterwards."""
    return _transition(draft_id, {DRAFTED}, APPROVED, approved_at=now())


def reject(draft_id: str) -> Optional[Dict[str, Any]]:
    return _transition(draft_id, {DRAFTED}, REJECTED)


def set_msg_id(draft_id: str, msg_id: int) -> None:
    """Remember which Telegram message shows this draft, to edit it in place."""
    oid = _oid(draft_id)
    if oid is not None:
        _drafts().update_one({"_id": oid}, {"$set": {"msg_id": msg_id}})
