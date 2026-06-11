from database.mongo import get_active_telegram_chat_ids, save_comments, save_telegram_subscriber


class FakeResult:
    upserted_id = "new-id"
    modified_count = 0


class FakeCollection:
    def __init__(self):
        self.calls = []
        self.deleted = []

    def find_one(self, query, projection):
        return None

    def update_one(self, query, update, upsert):
        self.calls.append((query, update, upsert))
        return FakeResult()

    def delete_many(self, query):
        self.deleted.append(query)

    def find(self, query, projection):
        return [
            {"telegram_chat_id": "123"},
            {"telegram_chat_id": "456"},
        ]


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


def test_save_and_list_telegram_subscriber():
    collection = FakeCollection()

    save_telegram_subscriber(
        {
            "text": "/start LINK123",
            "from": {"id": 123, "username": "alice", "first_name": "Alice"},
            "chat": {"id": 123, "type": "private"},
        },
        collection,
    )

    query, update, upsert = collection.calls[0]
    assert query == {"telegram_chat_id": "123"}
    assert update["$set"]["telegram_user_id"] == "123"
    assert update["$set"]["link_token"] == "LINK123"
    assert update["$set"]["active"] is True
    assert upsert is True
    assert get_active_telegram_chat_ids(collection) == ["123", "456"]
