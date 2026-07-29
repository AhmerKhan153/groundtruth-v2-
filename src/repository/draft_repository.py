"""MongoDB-backed draft repository.

The Mongo `status` field is the single source of truth for a draft's lifecycle:

    sourced  -> a story was picked from a provider (no draft yet)
    drafted  -> an LLM draft exists, awaiting your Telegram approval
    approved -> you approved it; the publish job will post it
    posted   -> published to LinkedIn
    rejected -> you rejected it

Only approved/posted docs are retained. Sourced/drafted/rejected records are
in-flight state carried purely so the Telegram flow has a stable id to reference;
a TTL index (via the `expire_at` field) auto-deletes them after SOURCED_TTL_HOURS
unless they reach a persistent status, which clears their expiry.

Telegram callbacks carry the string _id, so state survives restarts and multiple.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo import MongoClient

from config import (
    ARTICLES_COLLECTION,
    MONGODB_DB_NAME,
    MONGODB_URI,
    SOURCED_TTL_HOURS,
    STATUS_APPROVED,
    STATUS_DRAFTED,
    STATUS_POSTED,
    STATUS_SOURCED,
)

_client = MongoClient(MONGODB_URI)
_collection = _client[MONGODB_DB_NAME][ARTICLES_COLLECTION]

# TTL index on `expire_at`: with expireAfterSeconds=0, Mongo deletes each doc once
# `expire_at` (an absolute datetime) passes. Docs whose `expire_at` is null/missing
# are never deleted, so clearing the field (on approval) makes a record permanent.
_collection.create_index("expire_at", expireAfterSeconds=0)

# Statuses that make a record permanent (TTL cleared). Everything else is in-flight
# and expires unless promoted.
_PERSISTENT_STATUSES = {STATUS_APPROVED, STATUS_POSTED}


def _now() -> datetime:
    return datetime.now()


def _ttl_expiry() -> datetime:
    """Absolute time at which an in-flight record should auto-delete."""
    return _now() + timedelta(hours=SOURCED_TTL_HOURS)


def insert_sourced(story: Dict[str, Any]) -> str:
    """Persist a freshly sourced story and return its id as a string."""
    doc = {
        "title": story.get("title"),
        "url": story.get("url"),
        "score": story.get("score"),
        "draft": None,
        "status": STATUS_SOURCED,
        "created_at": _now(),
        "updated_at": _now(),
        "posted_at": None,
        "linkedin_url": None,
        "expire_at": _ttl_expiry(),
    }
    result = _collection.insert_one(doc)
    return str(result.inserted_id)


def attach_draft(draft_id: str, draft_text: str) -> None:
    """Store a generated draft and move the record to `drafted`.

    Still in-flight (awaiting approval), so the TTL clock is refreshed to give a
    full window to approve rather than counting from when it was sourced.
    """
    _collection.update_one(
        {"_id": ObjectId(draft_id)},
        {
            "$set": {
                "draft": draft_text,
                "status": STATUS_DRAFTED,
                "updated_at": _now(),
                "expire_at": _ttl_expiry(),
            }
        },
    )


def set_status(draft_id: str, status: str, **extra: Any) -> None:
    """Update a record's status (plus any extra fields, e.g. linkedin_url).

    Reaching a persistent status (approved/posted) clears `expire_at` so the record
    survives; any other status leaves the existing TTL so it still auto-expires.
    """
    fields = {"status": status, "updated_at": _now(), **extra}
    if status in _PERSISTENT_STATUSES:
        fields["expire_at"] = None
    _collection.update_one({"_id": ObjectId(draft_id)}, {"$set": fields})


def get(draft_id: str) -> Optional[Dict[str, Any]]:
    doc = _collection.find_one({"_id": ObjectId(draft_id)})
    if doc is not None:
        doc["id"] = str(doc["_id"])
    return doc


def find_by_status(status: str) -> List[Dict[str, Any]]:
    docs = list(_collection.find({"status": status}).sort("created_at", 1))
    for doc in docs:
        doc["id"] = str(doc["_id"])
    return docs


def find_approved() -> List[Dict[str, Any]]:
    return find_by_status(STATUS_APPROVED)
