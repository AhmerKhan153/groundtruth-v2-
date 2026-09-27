"""Create the MongoDB indexes. Idempotent; run once per database (local or Atlas).

    python -m scripts.init_db

Kept out of the request path on purpose: creating indexes at import time made
every cold start pay a round trip to the database.
"""

from groundtruth import store
from groundtruth.config import MONGODB_DB_NAME


def main() -> None:
    store.ensure_indexes()
    print(f"Indexes ready on {MONGODB_DB_NAME}.{store.DRAFTS}")


if __name__ == "__main__":
    main()
