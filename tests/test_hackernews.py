from groundtruth.sources import hackernews as hn

NOW = 1_800_000_000


def _item(**extra):
    item = {"id": 1, "type": "story", "title": "T", "url": "https://x.dev",
            "score": 120, "descendants": 30, "time": NOW - 3600}
    item.update(extra)
    return item


def test_candidate_mapping():
    assert hn.to_candidate(_item(), NOW) == {
        "hn_id": 1, "title": "T", "url": "https://x.dev", "score": 120, "comments": 30,
    }


def test_filters():
    assert hn.to_candidate(_item(url=None), NOW) == {}           # Ask HN / self-post
    assert hn.to_candidate(_item(score=10), NOW) == {}           # low signal
    assert hn.to_candidate(_item(time=NOW - 73 * 3600), NOW) == {}  # older than 72h
    assert hn.to_candidate(_item(type="job"), NOW) == {}
    assert hn.to_candidate(_item(dead=True), NOW) == {}
    assert hn.to_candidate({}, NOW) == {}                        # failed fetch


def test_ranking_counts_votes_and_discussion_equally():
    popular = {"score": 300, "comments": 10}
    argued = {"score": 150, "comments": 100}
    assert hn.rank_score(popular) > hn.rank_score(argued)
    assert hn.rank_score({"score": 100, "comments": 50}) > hn.rank_score({"score": 100, "comments": 0})


def test_merge_ids_dedups_and_keeps_order():
    assert hn.merge_ids([3, 1, 2], [2, 4, 3, 5]) == [3, 1, 2, 4, 5]
    assert hn.merge_ids([], []) == []
