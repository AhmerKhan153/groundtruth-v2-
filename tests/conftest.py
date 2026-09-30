import os

# Point the store at a throwaway database before groundtruth.config is imported.
# load_dotenv never overrides variables that are already set.
os.environ["MONGODB_DB_NAME"] = "groundtruth_test"
# Tests never follow .env to a real cluster: they create and drop a database.
# Local MongoDB by default (CI runs one as a service); override deliberately.
os.environ["MONGODB_URI"] = os.environ.get("TEST_MONGODB_URI", "mongodb://localhost:27017/")
# Fixed test identities, so tests never depend on (or touch) the real bot/secrets.
os.environ["TELEGRAM_CHAT_ID"] = "1000"
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["JOB_SECRET"] = "test-job-secret"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-webhook-secret"

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from groundtruth import store
from groundtruth.config import MONGODB_URI


@pytest.fixture
def db():
    """A clean test database with the real indexes; skips if Mongo isn't running.

    Real MongoDB on purpose: the unique index, TTL index and conditional
    find_one_and_update are exactly what these tests exercise.
    """
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        pytest.skip("MongoDB is not reachable at MONGODB_URI")
    client.drop_database("groundtruth_test")
    store.ensure_indexes()
    yield store._db()
    client.drop_database("groundtruth_test")
