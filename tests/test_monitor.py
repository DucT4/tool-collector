from monitors.tiktok_monitor import diff_comments


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query, projection):
        source_url = query["source_url"]
        return [doc for doc in self.docs if doc.get("source_url") == source_url]


def test_diff_comments_seeds_empty_source_without_notifications():
    diff = diff_comments(
        [{"name": "Alice", "comment": "hello", "replies": []}],
        "https://example.test/video",
        FakeCollection([]),
    )

    assert diff.seeded is True
    assert diff.new_comments == []
    assert diff.new_replies == []


def test_diff_comments_detects_new_parent_comment():
    diff = diff_comments(
        [
            {"name": "Alice", "comment": "old", "replies": []},
            {"name": "Bob", "comment": "new", "replies": []},
        ],
        "https://example.test/video",
        FakeCollection([{"source_url": "https://example.test/video", "name": "Alice", "comment": "old", "replies": []}]),
    )

    assert diff.seeded is False
    assert diff.new_comments == [{"name": "Bob", "comment": "new", "replies": []}]
    assert diff.new_replies == []


def test_diff_comments_detects_new_reply_without_repeating_existing_reply():
    parent = {"name": "Alice", "comment": "old"}
    diff = diff_comments(
        [{**parent, "replies": [{"name": "Cat", "comment": "seen"}, {"name": "Dan", "comment": "new"}]}],
        "https://example.test/video",
        FakeCollection([{**parent, "source_url": "https://example.test/video", "replies": [{"name": "Cat", "comment": "seen"}]}]),
    )

    assert diff.seeded is False
    assert diff.new_comments == []
    assert diff.new_replies == [{"parent": {**parent, "replies": [{"name": "Cat", "comment": "seen"}, {"name": "Dan", "comment": "new"}]}, "reply": {"name": "Dan", "comment": "new"}}]
