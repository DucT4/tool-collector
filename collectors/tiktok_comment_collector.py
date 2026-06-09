import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from config import tiktok_selectors as selectors
from config.settings import settings


class TikTokAccessBlocked(RuntimeError):
    pass


UI_TEXT_VALUES = {
    "reply",
    "like",
    "view replies",
    "view more replies",
    "view previous replies",
    "see translation",
    "more",
}


def normalize_source_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def is_ui_text(value: str | None) -> bool:
    text = clean_text(value).lower()
    return not text or text in UI_TEXT_VALUES or bool(re.fullmatch(r"\d+[smhdw]?", text))


def clean_comments(items: list[dict], source_url: str | None = None) -> list[dict]:
    cleaned = []
    seen = set()

    for item in items:
        name = clean_text(item.get("name"))
        comment = clean_text(item.get("comment"))
        if is_ui_text(comment):
            continue

        replies = []
        reply_seen = set()
        for reply in item.get("replies") or []:
            reply_name = clean_text(reply.get("name"))
            reply_comment = clean_text(reply.get("comment"))
            if is_ui_text(reply_comment):
                continue
            reply_key = (reply_name, reply_comment)
            if reply_key in reply_seen:
                continue
            reply_seen.add(reply_key)
            replies.append({"name": reply_name, "comment": reply_comment})

        key = (source_url or "", name, comment)
        if key in seen:
            continue
        seen.add(key)

        cleaned.append({"name": name, "comment": comment, "replies": replies})

    return cleaned


def merge_comment_items(existing: list[dict], new_items: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {
        (item.get("name", ""), item.get("comment", "")): {
            "name": item.get("name", ""),
            "comment": item.get("comment", ""),
            "replies": list(item.get("replies", [])),
        }
        for item in existing
    }

    for item in new_items:
        key = (item.get("name", ""), item.get("comment", ""))
        if key not in merged:
            merged[key] = {
                "name": item.get("name", ""),
                "comment": item.get("comment", ""),
                "replies": [],
            }

        reply_seen = {
            (reply.get("name", ""), reply.get("comment", ""))
            for reply in merged[key].get("replies", [])
        }
        for reply in item.get("replies", []):
            reply_key = (reply.get("name", ""), reply.get("comment", ""))
            if reply_key in reply_seen:
                continue
            reply_seen.add(reply_key)
            merged[key]["replies"].append(reply)

    return list(merged.values())


def _selector_payload() -> dict:
    return {
        "container": selectors.COMMENT_CONTAINER_CANDIDATES,
        "commentBlocks": selectors.COMMENT_BLOCK_CANDIDATES,
        "authors": selectors.AUTHOR_CANDIDATES,
        "texts": selectors.TEXT_CANDIDATES,
        "replyBlocks": selectors.REPLY_BLOCK_CANDIDATES,
    }


def wait_for_comments(page: Page, timeout_ms: int | None = None) -> None:
    timeout = timeout_ms or settings.page_timeout_ms
    selector = ", ".join(selectors.COMMENT_CONTAINER_CANDIDATES + selectors.COMMENT_BLOCK_CANDIDATES)
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        # TikTok may lazy-load comments after scroll; caller can continue with debug output.
        pass


def is_comment_panel_open(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const bodyText = document.body.innerText || "";
                    if (/Bình luận\\s*\\(\\d+\\)|Comments\\s*\\(\\d+\\)/i.test(bodyText)) return true;
                    return Boolean(document.querySelector('[data-e2e^="comment-username-"], [data-e2e^="comment-level-"]'));
                }
                """
            )
        )
    except Exception:
        return False


def click_open_comment_panel(page: Page) -> bool:
    """
    TikTok often lands on a feed-style video page where comments are hidden
    until the comment button is clicked. Open that panel before scrolling.
    """
    click_selectors = [
        '[data-e2e="comment-icon"]',
        '[data-e2e="comment-count"]',
        'button[aria-label*="comment" i]',
        '[role="button"][aria-label*="comment" i]',
    ]

    if is_comment_panel_open(page):
        return True

    clicked = page.evaluate(
        """
        () => {
            const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
            };
            const candidates = [
                ...document.querySelectorAll('[data-e2e="comment-icon"], [data-e2e="comment-count"]')
            ].filter(isVisible);

            for (const candidate of candidates) {
                let target = candidate.closest('button, [role="button"]');
                let cursorTarget = candidate;
                for (let i = 0; i < 6 && cursorTarget && !target; i += 1) {
                    const style = window.getComputedStyle(cursorTarget);
                    if (style.cursor === "pointer") target = cursorTarget;
                    cursorTarget = cursorTarget.parentElement;
                }
                target = target || candidate.parentElement;
                if (target) {
                    target.click();
                    return true;
                }
            }
            return false;
        }
        """
    )
    if clicked:
        page.wait_for_timeout(2500)
        if is_comment_panel_open(page):
            return True

    for selector in click_selectors:
        try:
            locator = page.locator(selector).first
            if not locator.count() or not locator.is_visible(timeout=1000):
                continue
            box = locator.bounding_box(timeout=1000)
            if not box:
                continue
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.wait_for_timeout(2500)
            if is_comment_panel_open(page):
                return True
        except Exception:
            continue

    return False


def detect_access_blocker(page: Page) -> str | None:
    text = page.locator("body").inner_text(timeout=3000)
    lowered = text.lower()
    if "drag the slider to fit the puzzle" in lowered:
        return "TikTok captcha is blocking comment loading. Solve it manually in the CDP Chrome profile, then run again."
    if "verify to continue" in lowered or "verification" in lowered and "comment" not in lowered:
        return "TikTok verification is blocking comment loading. Complete it manually in the browser, then run again."
    return None


def find_comment_container(page: Page):
    for selector in selectors.COMMENT_CONTAINER_CANDIDATES:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                return locator
        except Exception:
            continue
    return None


def get_target_comment_count(page: Page) -> int | None:
    count = page.evaluate(
        """
        () => {
            const visibleText = [...document.querySelectorAll("button, div, span, strong")]
                .map((el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const visible = rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
                    return visible ? (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim() : "";
                })
                .filter(Boolean);

            const tabPatterns = [
                /(?:Bình luận|Comments)\\s*\\((\\d+)\\)/i,
                /(?:Bình luận|Comments)\\s+(\\d+)/i,
            ];
            for (const text of visibleText) {
                for (const pattern of tabPatterns) {
                    const match = text.match(pattern);
                    if (match) return Number(match[1]);
                }
            }

            const icon = document.querySelector('[data-e2e="comment-count"]');
            const iconText = (icon?.innerText || icon?.textContent || "").replace(/\\D/g, "");
            return iconText ? Number(iconText) : null;
        }
        """
    )
    return int(count) if count else None


def count_loaded_comment_blocks(page: Page) -> int:
    payload = _selector_payload()
    return page.evaluate(
        """
        (cfg) => {
            const seen = new Set();
            for (const selector of cfg.commentBlocks) {
                try {
                    for (const el of document.querySelectorAll(selector)) {
                        const text = (el.innerText || el.textContent || "").trim();
                        if (text) seen.add(el);
                    }
                } catch (_) {}
            }
            return seen.size;
        }
        """,
        payload,
    )


def count_loaded_comment_and_reply_blocks(page: Page) -> int:
    payload = _selector_payload()
    return page.evaluate(
        """
        (cfg) => {
            const countBySelectors = (selectorList) => {
                const seen = new Set();
                for (const selector of selectorList) {
                    try {
                        for (const el of document.querySelectorAll(selector)) {
                            const text = (el.innerText || el.textContent || "").trim();
                            if (text) seen.add(el);
                        }
                    } catch (_) {}
                }
                return seen.size;
            };

            return countBySelectors(cfg.commentBlocks) + countBySelectors(cfg.replyBlocks);
        }
        """,
        payload,
    )


def scroll_comment_area_once(page: Page) -> bool:
    target_point = page.evaluate(
        """
        () => {
            const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
            const candidates = [...document.querySelectorAll("div, section, aside")]
                .map((el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const canScroll = el.scrollHeight > el.clientHeight + 80;
                    const visible = rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
                    const rightSide = rect.left > viewportWidth * 0.45;
                    const text = (el.innerText || el.textContent || "").toLowerCase();
                    const likelyComments = text.includes("comments") || text.includes("reply") || text.includes("log in to comment");
                    return { el, rect, canScroll, visible, rightSide, likelyComments, area: rect.width * rect.height };
                })
                .filter((item) => item.canScroll && item.visible && item.rightSide)
                .sort((a, b) => {
                    if (a.likelyComments !== b.likelyComments) return a.likelyComments ? -1 : 1;
                    return b.area - a.area;
                });

            const target = candidates[0]?.el;
            if (!target) return null;
            const before = target.scrollTop;
            target.scrollTop = target.scrollTop + Math.max(target.clientHeight * 0.85, 700);
            const rect = target.getBoundingClientRect();
            return {
                scrolled: target.scrollTop !== before,
                x: Math.min(rect.left + rect.width / 2, window.innerWidth - 20),
                y: Math.min(rect.top + rect.height / 2, window.innerHeight - 20)
            };
        }
        """
    )
    if target_point:
        try:
            page.mouse.move(target_point["x"], target_point["y"])
            page.mouse.wheel(0, 1600)
        except Exception:
            pass
        return True

    container = find_comment_container(page)
    if container:
        try:
            point = container.evaluate(
                """
                (el) => {
                    const before = el.scrollTop;
                    el.scrollTop = el.scrollTop + Math.max(el.clientHeight * 0.85, 700);
                    const rect = el.getBoundingClientRect();
                    return {
                        scrolled: el.scrollTop !== before,
                        x: Math.min(rect.left + rect.width / 2, window.innerWidth - 20),
                        y: Math.min(rect.top + rect.height / 2, window.innerHeight - 20)
                    };
                }
                """
            )
            if point:
                page.mouse.move(point["x"], point["y"])
                page.mouse.wheel(0, 1600)
                return bool(point["scrolled"])
        except Exception:
            pass

    try:
        viewport = page.viewport_size or {"width": 1366, "height": 768}
        page.mouse.move(viewport["width"] * 0.78, viewport["height"] * 0.55)
    except Exception:
        pass
    page.mouse.wheel(0, 1600)
    return True


def scroll_comments(page: Page, times: int = 5, target_count: int | None = None) -> int:
    stable_rounds = 0
    previous_count = count_loaded_comment_blocks(page)

    for _ in range(times):
        scroll_comment_area_once(page)
        page.wait_for_timeout(1800)

        current_count = count_loaded_comment_blocks(page)
        if target_count is not None and current_count >= target_count:
            return current_count

        if current_count <= previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_count = current_count

        if target_count is None and stable_rounds >= 4:
            return current_count

    return count_loaded_comment_blocks(page)


def count_reply_buttons(page: Page) -> int:
    return page.evaluate(
        """
        (patterns) => {
            const normalizedPatterns = patterns.map((value) => value.toLowerCase());
            const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
            };

            return [...document.querySelectorAll("button, [role='button'], div, span")]
                .filter(isVisible)
                .filter((el) => {
                    const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                    if (!text) return false;
                    return normalizedPatterns.some((pattern) => text.includes(pattern));
                }).length;
        }
        """,
        selectors.REPLY_BUTTON_TEXT_PATTERNS,
    )


def open_replies(page: Page, max_clicks: int = 30) -> int:
    clicked = 0
    patterns = selectors.REPLY_BUTTON_TEXT_PATTERNS

    for pattern in patterns:
        if clicked >= max_clicks:
            break
        locator = page.get_by_text(re.compile(re.escape(pattern), re.I))
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            if clicked >= max_clicks:
                break
            try:
                target = locator.nth(index)
                if not target.is_visible(timeout=500):
                    continue
                target.click(timeout=1500)
                clicked += 1
                page.wait_for_timeout(800)
            except Exception:
                continue

    return clicked


def expand_comments_and_replies(
    page: Page,
    scroll_times: int,
    max_reply_clicks: int,
    target_comment_count: int | None = None,
    source_url: str | None = None,
) -> tuple[int, int, list[dict]]:
    total_reply_clicks = 0
    stable_rounds = 0
    previous_total = count_loaded_comment_and_reply_blocks(page)
    accumulated = collect_comment_data(page, source_url=source_url)

    for _ in range(scroll_times):
        scroll_comment_area_once(page)
        page.wait_for_timeout(1200)
        accumulated = merge_comment_items(accumulated, collect_comment_data(page, source_url=source_url))

        remaining_click_budget = max_reply_clicks - total_reply_clicks
        if remaining_click_budget > 0:
            total_reply_clicks += open_replies(page, max_clicks=remaining_click_budget)
            page.wait_for_timeout(900)
            accumulated = merge_comment_items(accumulated, collect_comment_data(page, source_url=source_url))

        current_total = len(accumulated) + sum(len(item.get("replies", [])) for item in accumulated)
        current_comments = count_loaded_comment_blocks(page)
        has_reply_buttons = count_reply_buttons(page) > 0 and total_reply_clicks < max_reply_clicks

        reached_comment_target = target_comment_count is None or current_total >= target_comment_count
        if current_total <= previous_total:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_total = current_total

        if reached_comment_target and not has_reply_buttons and stable_rounds >= 3:
            return current_comments, total_reply_clicks, accumulated

    return count_loaded_comment_blocks(page), total_reply_clicks, accumulated


def collect_comment_data(page: Page, source_url: str | None = None) -> list[dict]:
    payload = _selector_payload()
    data = page.evaluate(
        """
        (cfg) => {
            const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const commentNodes = [...document.querySelectorAll('[data-e2e^="comment-username-"], [data-e2e^="comment-level-"]')]
                .map((el) => ({
                    key: el.getAttribute("data-e2e") || "",
                    text: clean(el.innerText || el.textContent || "")
                }))
                .filter((item) => item.text);

            const sequentialResults = [];
            let pendingName = null;
            let pendingLevel = null;
            let currentParent = null;

            for (const node of commentNodes) {
                const usernameMatch = node.key.match(/^comment-username-(\\d+)$/);
                if (usernameMatch) {
                    pendingName = node.text;
                    pendingLevel = Number(usernameMatch[1]);
                    continue;
                }

                const textMatch = node.key.match(/^comment-level-(\\d+)$/);
                if (!textMatch) continue;

                const level = Number(textMatch[1]);
                const name = pendingLevel === level ? pendingName || "" : "";
                const item = { name, comment: node.text };

                if (level === 1) {
                    currentParent = { ...item, replies: [] };
                    sequentialResults.push(currentParent);
                } else if (level === 2 && currentParent) {
                    currentParent.replies.push(item);
                }

                pendingName = null;
                pendingLevel = null;
            }

            if (sequentialResults.length) return sequentialResults;

            const textOf = (el) => (el?.innerText || el?.textContent || "")
                .replace(/\\s+/g, " ")
                .trim();
            const linesOf = (el) => (el?.innerText || el?.textContent || "")
                .split("\\n")
                .map((value) => value.replace(/\\s+/g, " ").trim())
                .filter(Boolean);

            const firstText = (root, selectorList) => {
                for (const selector of selectorList) {
                    try {
                        const el = root.querySelector(selector);
                        const text = textOf(el);
                        if (text) return text;
                    } catch (_) {}
                }
                return "";
            };

            const allBySelectors = (selectorList) => {
                const found = [];
                const seen = new Set();
                for (const selector of selectorList) {
                    try {
                        for (const el of document.querySelectorAll(selector)) {
                            if (!seen.has(el)) {
                                seen.add(el);
                                found.push(el);
                            }
                        }
                    } catch (_) {}
                }
                return found;
            };

            const blocks = allBySelectors(cfg.commentBlocks);
            const results = [];

            for (const block of blocks) {
                const replyBlocks = allBySelectors.call(null, cfg.replyBlocks)
                    .filter((reply) => block.contains(reply));

                const replySet = new Set(replyBlocks);
                const mainTextRoot = block.cloneNode(true);
                for (const reply of replyBlocks) {
                    const matchText = textOf(reply);
                    for (const node of [...mainTextRoot.querySelectorAll("*")]) {
                        if (textOf(node) === matchText) node.remove();
                    }
                }

                let name = firstText(block, cfg.authors);
                let comment = firstText(mainTextRoot, cfg.texts);

                if (!comment) {
                    const lines = linesOf(mainTextRoot);
                    name = name || lines[0] || "";
                    comment = lines.find((line) => line && line !== name) || "";
                }

                const replies = [];
                for (const reply of replySet) {
                    const replyName = firstText(reply, cfg.authors);
                    let replyComment = firstText(reply, cfg.texts);
                    if (!replyComment) {
                        const lines = linesOf(reply);
                        replyComment = lines.find((line) => line && line !== replyName) || "";
                    }
                    if (replyComment) replies.push({ name: replyName, comment: replyComment });
                }

                if (comment) results.push({ name, comment, replies });
            }

            return results;
        }
        """,
        payload,
    )
    return clean_comments(data, source_url=source_url)


def dump_debug_artifacts(page: Page, output_dir: str = "logs") -> Path:
    log_dir = Path(output_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    data = page.evaluate(
        """
        () => [...document.querySelectorAll("[data-e2e]")]
            .slice(0, 250)
            .map((el) => ({
                key: el.getAttribute("data-e2e"),
                text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 300)
            }))
            .filter((item) => item.key || item.text)
        """
    )

    json_path = log_dir / f"tiktok-dom-candidates-{stamp}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    screenshot_path = log_dir / f"tiktok-page-{stamp}.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        pass

    return json_path


def collect_from_video(
    page: Page,
    video_url: str,
    scroll_times: int | None = None,
    max_reply_clicks: int | None = None,
    debug: bool = False,
) -> list[dict]:
    source_url = normalize_source_url(video_url)
    page.goto(video_url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
    page.wait_for_timeout(3000)
    click_open_comment_panel(page)
    blocker = detect_access_blocker(page)
    if blocker:
        if debug:
            dump_debug_artifacts(page)
        raise TikTokAccessBlocked(blocker)
    wait_for_comments(page)

    target_count = get_target_comment_count(page)
    _, _, accumulated_comments = expand_comments_and_replies(
        page,
        scroll_times=scroll_times or settings.scroll_times,
        max_reply_clicks=max_reply_clicks or settings.max_reply_clicks,
        target_comment_count=target_count,
        source_url=source_url,
    )

    blocker = detect_access_blocker(page)
    if blocker:
        if debug:
            dump_debug_artifacts(page)
        raise TikTokAccessBlocked(blocker)

    comments = merge_comment_items(accumulated_comments, collect_comment_data(page, source_url=source_url))
    if debug and not comments:
        dump_debug_artifacts(page)

    return comments
