from database.mongo import save_comments


class FakeResult:
    upserted_id = "new-id"
    modified_count = 0


class FakeCollection:
    def __init__(self):
        self.calls = []
        self.deleted = []

    def update_one(self, query, update, upsert):
        self.calls.append((query, update, upsert))
        return FakeResult()

    def delete_many(self, query):
        self.deleted.append(query)


def test_save_comments_upserts_by_source_name_comment():
    collection = FakeCollection()

    saved = save_comments(
        [{"name": "Alice", "comment": "hello", "replies": [{"name": "Bob", "comment": "hi"}]}],
        "https://www.tiktok.com/@user/video/123",
        collection=collection,
    )

    assert saved == 1
    query, update, upsert = collection.calls[0]
    assert query == {
        "source_url": "https://www.tiktok.com/@user/video/123",
        "name": "Alice",
        "comment": "hello",
    }
    assert update["$set"]["platform"] == "tiktok"
    assert update["$set"]["replies"] == [{"name": "Bob", "comment": "hi"}]
    assert upsert is True
    assert collection.deleted == [
        {
            "source_url": "https://www.tiktok.com/@user/video/123",
            "$nor": [
                {
                    "source_url": "https://www.tiktok.com/@user/video/123",
                    "name": "Alice",
                    "comment": "hello",
                }
            ],
        }
    ]
