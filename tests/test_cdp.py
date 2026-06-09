from browser.cdp import _extract_remote_debugging_port_from_command_line, _find_remote_debugging_port


def test_find_remote_debugging_port_at_root():
    assert _find_remote_debugging_port({"remote_debugging_port": 35001}) == 35001


def test_find_remote_debugging_port_nested_data():
    payload = {"success": True, "data": {"remote_debugging_port": "35002"}}

    assert _find_remote_debugging_port(payload) == 35002


def test_find_remote_debugging_port_camel_case():
    payload = {"data": {"remoteDebuggingPort": 35003}}

    assert _find_remote_debugging_port(payload) == 35003


def test_find_remote_debugging_port_from_address():
    payload = {"data": {"remote_debugging_address": "127.0.0.1:53378"}}

    assert _find_remote_debugging_port(payload) == 53378


def test_extract_remote_debugging_port_from_command_line():
    command_line = 'chrome.exe --user-data-dir="D:\\profile" --remote-debugging-port=59804 --lang=vi'

    assert _extract_remote_debugging_port_from_command_line(command_line) == 59804
