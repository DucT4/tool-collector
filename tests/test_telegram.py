from notifications.telegram import format_comment_notifications, split_telegram_message, truncate_text


def test_format_comment_notifications_includes_comments_and_replies():
    messages = format_comment_notifications(
        "https://example.test/video",
        [{"name": "Alice", "comment": "hello"}],
        [{"parent": {"name": "Bob", "comment": "parent"}, "reply": {"name": "Cat", "comment": "reply"}}],
    )

    text = "\n".join(messages)
    assert "New comments: 1" in text
    assert "New replies: 1" in text
    assert "Alice: hello" in text
    assert "Cat: reply" in text


def test_split_telegram_message_respects_limit():
    messages = split_telegram_message(["header", "x" * 20, "y" * 20], limit=25)

    assert len(messages) == 3
    assert all(len(message) <= 25 for message in messages)


def test_truncate_text_shortens_long_values():
    assert truncate_text("a" * 20, limit=10) == "aaaaaaa..."
