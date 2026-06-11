from notifications.telegram import TelegramSubscriptionService, format_comment_notifications, split_telegram_message, truncate_text


class FakeResult:
    upserted_id = "subscriber"
    modified_count = 1


class FakeCollection:
    def __init__(self):
        self.calls = []

    def update_one(self, query, update, upsert):
        self.calls.append((query, update, upsert))
        return FakeResult()


class FakeNotifier:
    def __init__(self, updates):
        self.updates = updates
        self.sent = []
        self.offsets = []

    def get_updates(self, offset=None):
        self.offsets.append(offset)
        return self.updates

    def send_message(self, text, chat_id=None):
        self.sent.append((str(chat_id), text))


def test_format_comment_notifications_includes_comments_and_replies():
    messages = format_comment_notifications(
        "https://example.test/video",
        [{"name": "Alice", "comment": "hello"}],
        [{"parent": {"name": "Bob", "comment": "parent"}, "reply": {"name": "Cat", "comment": "reply"}}],
    )

    text = "\n".join(messages)
    assert "TikTok có tương tác mới" in text
    assert "Bình luận mới:" in text
    assert "Phản hồi mới:" in text
    assert "Alice: hello" in text
    assert "Cat: reply" in text
    assert "trả lời Bob: parent" in text
    assert "New comments" not in text
    assert "New replies" not in text


def test_split_telegram_message_respects_limit():
    messages = split_telegram_message(["header", "x" * 20, "y" * 20], limit=25)

    assert len(messages) == 3
    assert all(len(message) <= 25 for message in messages)


def test_truncate_text_shortens_long_values():
    assert truncate_text("a" * 20, limit=10) == "aaaaaaa..."


def test_subscription_service_registers_start_and_advances_offset():
    notifier = FakeNotifier(
        [
            {
                "update_id": 42,
                "message": {
                    "text": "/start ACCOUNT-1001",
                    "from": {"id": 123, "username": "alice"},
                    "chat": {"id": 123, "type": "private"},
                },
            }
        ]
    )
    collection = FakeCollection()
    service = TelegramSubscriptionService(notifier, collection)

    assert service.poll_once() == 1
    assert service.next_offset == 43
    assert collection.calls[0][0] == {"telegram_chat_id": "123"}
    assert collection.calls[0][1]["$set"]["link_token"] == "ACCOUNT-1001"
    assert notifier.sent == []


def test_subscription_service_deactivates_stop():
    notifier = FakeNotifier(
        [{"update_id": 7, "message": {"text": "/stop", "from": {"id": 123}, "chat": {"id": 123}}}]
    )
    collection = FakeCollection()
    service = TelegramSubscriptionService(notifier, collection)

    assert service.poll_once() == 1
    assert collection.calls[0][1]["$set"]["active"] is False
    assert collection.calls[0][2] is False
    assert notifier.sent == []
