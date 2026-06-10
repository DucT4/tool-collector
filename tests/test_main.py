from main import parse_args


def test_parse_args_accepts_monitor_interval_and_cdp_override():
    args = parse_args(["https://example.test/video", "--monitor", "--interval-minutes", "10", "--cdp-url", "http://127.0.0.1:9222"])

    assert args.monitor is True
    assert args.interval_minutes == 10
    assert args.cdp_url_provided is True
