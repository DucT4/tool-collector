from main import is_browser_connection_error, parse_args


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
