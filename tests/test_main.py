from monitors.tiktok_monitor import CommentDiff
from main import is_browser_connection_error, notification_messages_for_diff, parse_args


def test_parse_args_accepts_monitor_interval_and_cdp_override():
    args = parse_args(["https://example.test/video", "--monitor", "--interval-minutes", "10", "--cdp-url", "http://127.0.0.1:9222"])

    assert args.monitor is True
    assert args.interval_minutes == 10
    assert args.cdp_url_provided is True


def test_parse_args_defaults_monitor_interval_to_five_minutes():
    args = parse_args(["https://example.test/video", "--monitor"])

    assert args.interval_minutes == 5


def test_browser_connection_error_detects_closed_context():
    error = RuntimeError("BrowserContext.new_page: Target page, context or browser has been closed")

    assert is_browser_connection_error(error) is True


def test_seed_baseline_does_not_create_telegram_message():
    diff = CommentDiff(seeded=True, new_comments=[], new_replies=[])

    assert notification_messages_for_diff("https://example.test/video", diff) == []


def test_no_changes_does_not_create_telegram_message():
    diff = CommentDiff(seeded=False, new_comments=[], new_replies=[])

    assert notification_messages_for_diff("https://example.test/video", diff) == []


def test_new_comment_creates_customer_notification():
    diff = CommentDiff(
        seeded=False,
        new_comments=[{"name": "Alice", "comment": "Quán có chỗ đậu xe không?"}],
        new_replies=[],
    )

    messages = notification_messages_for_diff("https://example.test/video", diff)

    assert len(messages) == 1
    assert "Alice: Quán có chỗ đậu xe không?" in messages[0]
