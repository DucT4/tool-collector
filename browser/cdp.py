import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from config.settings import settings


class GPMLoginError(RuntimeError):
    pass


def _find_remote_debugging_port(payload) -> int | None:
    if isinstance(payload, dict):
        for key in ("remote_debugging_port", "remoteDebuggingPort", "debugging_port", "debuggingPort"):
            value = payload.get(key)
            if value:
                return int(value)
        for key in ("remote_debugging_address", "remoteDebuggingAddress", "debugging_address", "debuggingAddress"):
            value = payload.get(key)
            if value:
                match = re.search(r":(\d+)$", str(value))
                if match:
                    return int(match.group(1))
        for value in payload.values():
            found = _find_remote_debugging_port(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_remote_debugging_port(value)
            if found:
                return found
    return None


def start_gpm_profile(profile_id: str, api_base: str | None = None) -> str:
    base = (api_base or settings.gpm_api_base).rstrip("/")
    url = f"{base}/api/v3/profiles/start/{profile_id}"

    try:
        with urlopen(url, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GPMLoginError(f"GPMLogin start profile failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise GPMLoginError(f"Cannot connect to GPMLogin API at {base}: {exc.reason}") from exc
    except OSError as exc:
        raise GPMLoginError(f"GPMLogin API connection failed at {base}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GPMLoginError("GPMLogin API returned invalid JSON") from exc

    port = _find_remote_debugging_port(payload)
    if not port:
        raise GPMLoginError(
            "GPMLogin API response missing remote_debugging_port/remote_debugging_address. "
            f"Response: {payload}"
        )

    return f"http://127.0.0.1:{port}"


def connect_to_browser(cdp_url: str | None = None):
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(cdp_url or settings.cdp_url)

    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()

    return playwright, browser, context


def connect_to_gpm_profile(profile_id: str, api_base: str | None = None):
    cdp_url = start_gpm_profile(profile_id, api_base=api_base)
    return connect_to_browser(cdp_url), cdp_url
