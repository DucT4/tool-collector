import json
import re
import subprocess
import time

import requests

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


def _extract_remote_debugging_port_from_command_line(command_line: str | None) -> int | None:
    if not command_line:
        return None
    match = re.search(r"--remote-debugging-port=(\d+)", command_line)
    return int(match.group(1)) if match else None


def find_existing_gpm_cdp_url(profile_id: str) -> str | None:
    escaped_profile_id = profile_id.replace("'", "''")
    command = (
        "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
        "Where-Object { $_.ExecutablePath -like '*GPMLoginGlobal*' "
        f"-and $_.CommandLine -like '*{escaped_profile_id}*' "
        "-and $_.CommandLine -match '--remote-debugging-port=(\\d+)' } | "
        "ForEach-Object { $Matches[1] } | Select-Object -Unique | ConvertTo-Json"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    try:
        ports = json.loads(result.stdout)
    except json.JSONDecodeError:
        ports = result.stdout.strip()

    if isinstance(ports, str):
        ports = [ports]

    for port in ports:
        cdp_url = f"http://127.0.0.1:{port}"
        try:
            wait_for_cdp(cdp_url, timeout_seconds=3)
            return cdp_url
        except GPMLoginError:
            continue

    return None


def start_gpm_profile(profile_id: str, api_base: str | None = None) -> str:
    base = (api_base or settings.gpm_api_base).rstrip("/")
    paths = [
        f"/api/v1/profiles/start/{profile_id}",
        f"/api/v3/profiles/start/{profile_id}",
    ]
    last_payload = None

    for path in paths:
        url = f"{base}{path}"
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            last_payload = f"HTTP {exc.response.status_code} for {path}"
            continue
        except requests.ConnectionError as exc:
            raise GPMLoginError(f"Cannot connect to GPMLogin API at {base}: {exc}") from exc
        except requests.RequestException as exc:
            raise GPMLoginError(f"GPMLogin API connection failed at {base}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise GPMLoginError(f"GPMLogin API returned invalid JSON for {path}") from exc

        port = _find_remote_debugging_port(payload)
        if port:
            return f"http://127.0.0.1:{port}"
        last_payload = payload

    raise GPMLoginError(
        "GPMLogin API response missing remote_debugging_port/remote_debugging_address. "
        f"Last response: {last_payload}"
    )


def wait_for_cdp(cdp_url: str, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    version_url = f"{cdp_url.rstrip('/')}/json/version"

    while time.monotonic() < deadline:
        try:
            response = requests.get(version_url, timeout=2)
            if response.ok:
                return
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(0.5)

    raise GPMLoginError(f"CDP endpoint is not ready at {cdp_url}: {last_error}")


def connect_to_browser(cdp_url: str | None = None):
    playwright = sync_playwright().start()
    target_cdp_url = cdp_url or settings.cdp_url
    wait_for_cdp(target_cdp_url)
    browser = playwright.chromium.connect_over_cdp(target_cdp_url)

    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()

    return playwright, browser, context


def connect_to_gpm_profile(profile_id: str, api_base: str | None = None):
    cdp_url = start_gpm_profile(profile_id, api_base=api_base)
    try:
        wait_for_cdp(cdp_url, timeout_seconds=10)
    except GPMLoginError:
        existing_cdp_url = find_existing_gpm_cdp_url(profile_id)
        if existing_cdp_url:
            cdp_url = existing_cdp_url
        else:
            raise
    return connect_to_browser(cdp_url), cdp_url
