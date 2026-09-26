# MongoDB runbook

Groundtruth stores every draft and its lifecycle state in MongoDB: database
`groundtruth`, collection `articles`. The `status` field on each document is the
single source of truth for where a draft sits in the pipeline — there is no
in-memory state to lose across a restart.

> **Renamed from `KnowledgeExtractor`.** The default database name changed with the
> rebrand. `MONGODB_DB_NAME` in your `.env` still overrides the default, so an
> existing instance keeps reading its old data until you choose to migrate. To move
> records across:
>
> ```bash
> mongodump --db KnowledgeExtractor --collection articles --out /tmp/ke-dump
> mongorestore --db groundtruth --collection articles \
>     /tmp/ke-dump/KnowledgeExtractor/articles.bson
> ```
>
> Then set `MONGODB_DB_NAME=groundtruth` in `.env` and confirm the document counts
> match before dropping anything. Only `approved` and `posted` records are worth
> moving; the rest is in-flight state the TTL index would have swept anyway.

## 1. Start MongoDB

If MongoDB is not already running, start it with:

```bash
mongod --dbpath /data/db
```

If you use systemd, this also works:

```bash
sudo systemctl start mongod
```

Verify that it is running:

```bash
ps -ef | grep '[m]ongod'
```

And confirm the server responds:

```bash
mongosh --eval 'db.runCommand({ ping: 1 })'
```

## 2. Open the MongoDB shell

```bash
mongosh
```

Then switch to the project database:

```javascript
use groundtruth
```

## 3. Check the articles collection

List collections:

```javascript
show collections
```

Count saved records:

```javascript
db.articles.countDocuments()
```

View the latest records:

```javascript
db.articles.find().sort({ _id: -1 }).limit(5).pretty()
```

## 4. Inspect records from Python

Go through the repository layer so you read the same database and collection the
app does, rather than a hardcoded guess that can drift:

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "src")
from repository import draft_repository as repo
print("approved, awaiting publish:", len(repo.find_approved()))
for doc in repo._collection.find().sort([("_id", -1)]).limit(5):
    print(doc.get("status"), "|", doc.get("title"))
EOF
```

Counts by lifecycle status, from `mongosh`:

```javascript
db.articles.aggregate([{ $group: { _id: "$status", n: { $sum: 1 } } }])
```

## 5. Optional cleanup

Remove all test records if needed:

```javascript
db.articles.deleteMany({})
```

## 6. The TTL sweep

Sourced, drafted and rejected records carry an `expire_at` datetime and are deleted
automatically by a TTL index (`expireAfterSeconds: 0`) once that timestamp passes —
see `SOURCED_TTL_HOURS` in `src/config.py`. Reaching `approved` clears `expire_at`,
and that is exactly what makes a record persistent.

See what is currently queued for deletion:

```javascript
db.articles.find({ expire_at: { $ne: null } }, { title: 1, status: 1, expire_at: 1 })
```

If records disappear unexpectedly, check the index before suspecting the app:

```javascript
db.articles.getIndexes()
```
