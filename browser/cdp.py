from playwright.sync_api import sync_playwright

from config.settings import settings


def connect_to_browser(cdp_url: str | None = None):
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(cdp_url or settings.cdp_url)

    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()

    return playwright, browser, context
