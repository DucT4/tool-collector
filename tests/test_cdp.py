from browser.cdp import _find_remote_debugging_port


def test_find_remote_debugging_port_at_root():
    assert _find_remote_debugging_port({"remote_debugging_port": 35001}) == 35001


def test_find_remote_debugging_port_nested_data():
    payload = {"success": True, "data": {"remote_debugging_port": "35002"}}

    assert _find_remote_debugging_port(payload) == 35002


def test_find_remote_debugging_port_camel_case():
    payload = {"data": {"remoteDebuggingPort": 35003}}

    assert _find_remote_debugging_port(payload) == 35003
