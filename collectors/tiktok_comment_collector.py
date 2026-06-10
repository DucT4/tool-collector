import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

from playwright.sync_api import Page, Request, Response, TimeoutError as PlaywrightTimeoutError

from config import tiktok_selectors as selectors
from config.settings import settings


class TikTokAccessBlocked(RuntimeError):
    pass


COMMENT_NETWORK_IDLE_MS = 1800
COMMENT_NETWORK_IDLE_TIMEOUT_MS = 9000
UNLIMITED_REPLY_CLICK_HARD_CAP = 10000
COMMENT_NETWORK_URL_MARKERS = (
    "mcs-sg.tiktokv.com/v1/list",
    "mcs-va.tiktokv.com/v1/list",
    "comment/list",
    "comment/list/reply",
    "/api/comment/list/",
    "/api/comment/",
    "aweme/v1/web/comment",
)

COMMENT_DATA_URL_MARKERS = (
    "comment/list",
    "comment/list/reply",
    "/api/comment/list/",
    "/api/comment/",
    "aweme/v1/web/comment",
)


UI_TEXT_VALUES = {
    "reply",
    "like",
    "view replies",
    "view more replies",
    "view previous replies",
    "see translation",
    "more",
    "tr\u1ea3 l\u1eddi",
    "\u1ea9n",
    "trả lời",
    "ẩn",
}


def is_comment_network_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in COMMENT_NETWORK_URL_MARKERS)


def is_comment_data_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in COMMENT_DATA_URL_MARKERS)


def _comment_id_from_url(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    for key in ("comment_id", "root_comment_id", "cid"):
        value = query.get(key)
        if value:
            return value[0]
    return ""


def _extract_tiktok_user_name(user: dict | None) -> str:
    if not isinstance(user, dict):
        return ""
    for key in ("nickname", "unique_id", "uniqueId", "sec_uid", "uid"):
        value = user.get(key)
        if value:
            return str(value)
    return ""


def _extract_tiktok_comment_text(comment: dict) -> str:
    for key in ("text", "comment_text", "commentText", "reply_comment_total_text"):
        value = comment.get(key)
        if value:
            return str(value)
    return ""


def _extract_tiktok_comment_id(comment: dict) -> str:
    for key in ("cid", "comment_id", "commentId", "id"):
        value = comment.get(key)
        if value:
            return str(value)
    return ""


def _extract_tiktok_comment_list(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    comments = payload.get("comments")
    if isinstance(comments, list):
        return [comment for comment in comments if isinstance(comment, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        return _extract_tiktok_comment_list(data)
    return []


def _comment_from_api_payload(comment: dict) -> dict:
    replies = []
    for key in ("reply_comment", "reply_comments", "replies"):
        values = comment.get(key)
        if not isinstance(values, list):
            continue
        for reply in values:
            if not isinstance(reply, dict):
                continue
            reply_text = _extract_tiktok_comment_text(reply)
            if not reply_text:
                continue
            replies.append(
                {
                    "cid": _extract_tiktok_comment_id(reply),
                    "name": _extract_tiktok_user_name(reply.get("user")),
                    "comment": reply_text,
                }
            )

    return {
        "cid": _extract_tiktok_comment_id(comment),
        "name": _extract_tiktok_user_name(comment.get("user")),
        "comment": _extract_tiktok_comment_text(comment),
        "replies": replies,
    }


class CommentNetworkMonitor:
    def __init__(self) -> None:
        self.pending: dict[int, str] = {}
        self.api_items_by_cid: dict[str, dict] = {}
        self.pending_replies_by_parent_cid: dict[str, list[dict]] = {}
        self.request_count = 0
        self.data_response_count = 0
        self.finished_count = 0
        self.last_activity = time.monotonic()
        self._handlers = []

    def start(self, page: Page) -> None:
        def on_request(request: Request) -> None:
            if not is_comment_network_url(request.url):
                return
            self.pending[id(request)] = request.url
            self.request_count += 1
            self.last_activity = time.monotonic()

        def on_request_done(request: Request) -> None:
            if id(request) not in self.pending and not is_comment_network_url(request.url):
                return
            self.pending.pop(id(request), None)
            self.finished_count += 1
            self.last_activity = time.monotonic()

        def on_response(response: Response) -> None:
            if not is_comment_data_url(response.url):
                return
            try:
                payload = response.json()
            except Exception:
                return

            comments = _extract_tiktok_comment_list(payload)
            if not comments:
                return

            parent_cid = _comment_id_from_url(response.url)
            self.data_response_count += 1
            for comment in comments:
                item = _comment_from_api_payload(comment)
                if not item.get("comment"):
                    continue
                cid = item.get("cid") or ""
                if parent_cid and cid != parent_cid:
                    self.pending_replies_by_parent_cid.setdefault(parent_cid, []).append(item)
                else:
                    self._merge_api_item(item)

            self._flush_pending_api_replies()

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfinished", on_request_done)
        page.on("requestfailed", on_request_done)
        self._handlers = [
            ("request", on_request),
            ("response", on_response),
            ("requestfinished", on_request_done),
            ("requestfailed", on_request_done),
        ]

    def _merge_api_item(self, item: dict) -> None:
        cid = item.get("cid") or f"{item.get('name', '')}:{item.get('comment', '')}"
        existing = self.api_items_by_cid.setdefault(
            cid,
            {
                "cid": cid,
                "name": item.get("name", ""),
                "comment": item.get("comment", ""),
                "replies": [],
            },
        )
        if item.get("name") and not existing.get("name"):
            existing["name"] = item["name"]
        if item.get("comment") and (
            not existing.get("comment") or str(existing.get("comment", "")).startswith("[parent_comment_id:")
        ):
            existing["comment"] = item["comment"]

        seen = {(reply.get("name", ""), reply.get("comment", "")) for reply in existing.get("replies", [])}
        for reply in item.get("replies", []):
            reply_key = (reply.get("name", ""), reply.get("comment", ""))
            if reply_key in seen:
                continue
            seen.add(reply_key)
            existing["replies"].append(reply)

    def _flush_pending_api_replies(self) -> None:
        for parent_cid in list(self.pending_replies_by_parent_cid):
            if parent_cid not in self.api_items_by_cid:
                self.api_items_by_cid[parent_cid] = {
                    "cid": parent_cid,
                    "name": "",
                    "comment": f"[parent_comment_id:{parent_cid}]",
                    "replies": [],
                }
            self._merge_api_item(
                {
                    "cid": parent_cid,
                    "replies": self.pending_replies_by_parent_cid[parent_cid],
                }
            )
            del self.pending_replies_by_parent_cid[parent_cid]

    def collect_api_comments(self, source_url: str | None = None) -> list[dict]:
        self._flush_pending_api_replies()
        return clean_comments(list(self.api_items_by_cid.values()), source_url=source_url)

    def stop(self, page: Page) -> None:
        for event_name, handler in self._handlers:
            try:
                page.remove_listener(event_name, handler)
            except Exception:
                pass
        self._handlers = []

    def is_idle(self, idle_ms: int = COMMENT_NETWORK_IDLE_MS) -> bool:
        idle_for_ms = (time.monotonic() - self.last_activity) * 1000
        return not self.pending and idle_for_ms >= idle_ms

    def wait_for_idle(
        self,
        page: Page,
        idle_ms: int = COMMENT_NETWORK_IDLE_MS,
        timeout_ms: int = COMMENT_NETWORK_IDLE_TIMEOUT_MS,
    ) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self.is_idle(idle_ms=idle_ms):
                return True
            page.wait_for_timeout(250)
        return self.is_idle(idle_ms=idle_ms)


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

        cleaned_item = {"name": name, "comment": comment, "replies": replies}
        if item.get("cid"):
            cleaned_item["cid"] = clean_text(str(item.get("cid")))
        cleaned.append(cleaned_item)

    return cleaned


def merge_comment_items(existing: list[dict], new_items: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    cid_index: dict[str, tuple[str, str]] = {}

    for item in existing:
        key = (item.get("name", ""), item.get("comment", ""))
        merged[key] = {
            "name": item.get("name", ""),
            "comment": item.get("comment", ""),
            "replies": list(item.get("replies", [])),
        }
        if item.get("cid"):
            merged[key]["cid"] = item["cid"]
            cid_index[str(item["cid"])] = key

    for item in new_items:
        item_cid = str(item.get("cid", ""))
        key = cid_index.get(item_cid) if item_cid else None
        key = key or (item.get("name", ""), item.get("comment", ""))
        if key not in merged:
            merged[key] = {
                "name": item.get("name", ""),
                "comment": item.get("comment", ""),
                "replies": [],
            }
        if item_cid:
            merged[key]["cid"] = item_cid
            cid_index[item_cid] = key

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
        page.locator('[data-e2e^="comment-level-1"]').first.wait_for(state="visible", timeout=timeout)
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
                    if (/B\\u00ecnh lu\\u1eadn\\s*\\(\\d+\\)|Comments\\s*\\(\\d+\\)/i.test(bodyText)) return true;
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
                /(?:B\\u00ecnh lu\\u1eadn|Comments)\\s*\\((\\d+)\\)/i,
                /(?:B\\u00ecnh lu\\u1eadn|Comments)\\s+(\\d+)/i,
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


def reset_comment_scroll(page: Page) -> None:
    page.evaluate(
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
                    const likelyComments = text.includes("comments")
                        || text.includes("reply")
                        || text.includes("log in to comment")
                        || text.includes("b\\u00ecnh lu\\u1eadn")
                        || text.includes("tr\\u1ea3 l\\u1eddi");
                    return { el, rect, canScroll, visible, rightSide, likelyComments, area: rect.width * rect.height };
                })
                .filter((item) => item.canScroll && item.visible && item.rightSide)
                .sort((a, b) => {
                    if (a.likelyComments !== b.likelyComments) return a.likelyComments ? -1 : 1;
                    return b.area - a.area;
                });

            const target = candidates[0]?.el;
            if (target) target.scrollTop = 0;
        }
        """
    )


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
                    const likelyComments = text.includes("comments")
                        || text.includes("reply")
                        || text.includes("log in to comment")
                        || text.includes("b\\u00ecnh lu\\u1eadn")
                        || text.includes("tr\\u1ea3 l\\u1eddi");
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
                    if (/^tr\\u1ea3 l\\u1eddi$/i.test(text) || /^reply$/i.test(text)) return false;
                    if (/^\\u1ea9n$/i.test(text) || /^hide$/i.test(text)) return false;
                    if (/xem\\s+\\d+\\s+c\\u00e2u tr\\u1ea3 l\\u1eddi/i.test(text)) return true;
                    if (/xem th\\u00eam\\s+\\d*/i.test(text)) return true;
                    return normalizedPatterns.some((pattern) => text.includes(pattern));
                }).length;
        }
        """,
        selectors.REPLY_BUTTON_TEXT_PATTERNS,
    )


def open_replies(page: Page, max_clicks: int = 30, network_monitor: CommentNetworkMonitor | None = None) -> int:
    clicked = 0
    patterns = selectors.REPLY_BUTTON_TEXT_PATTERNS

    while clicked < max_clicks:
        if count_reply_buttons(page) <= 0:
            break

        batch_clicked = page.evaluate(
            """
            (maxClicks) => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
                };
                const isReplyExpander = (text) => {
                    const value = text.replace(/\\s+/g, " ").trim();
                    if (!value) return false;
                    if (/^tr\\u1ea3 l\\u1eddi$/i.test(value) || /^reply$/i.test(value)) return false;
                    if (/^\\u1ea9n$/i.test(value) || /^hide$/i.test(value)) return false;
                    return /view.*repl/i.test(value)
                        || /more replies/i.test(value)
                        || /view more/i.test(value)
                        || /see more/i.test(value)
                        || /xem\\s+\\d+\\s+c\\u00e2u tr\\u1ea3 l\\u1eddi/i.test(value)
                        || /xem th\\u00eam\\s*$/i.test(value)
                        || /xem th\\u00eam\\s+\\d*/i.test(value)
                        || /c\\u00e2u tr\\u1ea3 l\\u1eddi/i.test(value);
                };

                let clicked = 0;
                const clickedTargets = new Set();
                const candidates = [...document.querySelectorAll("button, [role='button'], div, span, p")]
                    .filter(isVisible)
                    .filter((el) => isReplyExpander(el.innerText || el.textContent || ""));

                for (const candidate of candidates) {
                    if (clicked >= maxClicks) break;
                    const target = candidate.closest("button, [role='button']") || candidate;
                    if (clickedTargets.has(target)) continue;
                    clickedTargets.add(target);
                    target.click();
                    clicked += 1;
                }
                return clicked;
            }
            """,
            max_clicks - clicked,
        )
        if not batch_clicked:
            break
        clicked += batch_clicked
        if network_monitor:
            network_monitor.wait_for_idle(page)
        else:
            page.wait_for_timeout(1200)
        page.wait_for_timeout(500)

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
                if network_monitor:
                    network_monitor.wait_for_idle(page)
                else:
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
    network_monitor: CommentNetworkMonitor | None = None,
) -> tuple[int, int, list[dict], dict]:
    if max_reply_clicks <= 0:
        max_reply_clicks = UNLIMITED_REPLY_CLICK_HARD_CAP

    total_reply_clicks = 0
    stable_rounds = 0
    previous_total = count_loaded_comment_and_reply_blocks(page)
    accumulated = collect_comment_data(page, source_url=source_url)
    if network_monitor:
        accumulated = merge_comment_items(accumulated, network_monitor.collect_api_comments(source_url=source_url))
    rounds = 0
    hard_round_limit = scroll_times
    if target_comment_count:
        hard_round_limit = max(scroll_times, min(target_comment_count * 2, 1500))

    while rounds < hard_round_limit:
        rounds += 1
        scroll_comment_area_once(page)
        if network_monitor:
            network_monitor.wait_for_idle(page)
        else:
            page.wait_for_timeout(1200)
        accumulated = merge_comment_items(accumulated, collect_comment_data(page, source_url=source_url))
        if network_monitor:
            accumulated = merge_comment_items(accumulated, network_monitor.collect_api_comments(source_url=source_url))

        remaining_click_budget = max_reply_clicks - total_reply_clicks
        if remaining_click_budget > 0:
            total_reply_clicks += open_replies(
                page,
                max_clicks=remaining_click_budget,
                network_monitor=network_monitor,
            )
            if network_monitor:
                network_monitor.wait_for_idle(page)
            else:
                page.wait_for_timeout(900)
            accumulated = merge_comment_items(accumulated, collect_comment_data(page, source_url=source_url))
            if network_monitor:
                accumulated = merge_comment_items(accumulated, network_monitor.collect_api_comments(source_url=source_url))

        current_total = len(accumulated) + sum(len(item.get("replies", [])) for item in accumulated)
        current_comments = count_loaded_comment_blocks(page)
        visible_reply_buttons = count_reply_buttons(page) > 0
        can_click_reply_buttons = visible_reply_buttons and total_reply_clicks < max_reply_clicks
        network_is_idle = network_monitor is None or network_monitor.is_idle()

        reached_comment_target = target_comment_count is None or current_total >= target_comment_count
        if current_total <= previous_total:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_total = current_total

        no_more_visible_load = not visible_reply_buttons and network_is_idle and stable_rounds >= 3
        soft_limit_reached = rounds >= scroll_times
        if no_more_visible_load and (reached_comment_target or soft_limit_reached):
            return current_comments, total_reply_clicks, accumulated, {
                "rounds": rounds,
                "hard_round_limit": hard_round_limit,
                "stable_rounds": stable_rounds,
                "stop_reason": "network_idle_no_dom_growth",
            }
        if visible_reply_buttons and not can_click_reply_buttons and network_is_idle and stable_rounds >= 3:
            return current_comments, total_reply_clicks, accumulated, {
                "rounds": rounds,
                "hard_round_limit": hard_round_limit,
                "stable_rounds": stable_rounds,
                "stop_reason": "max_reply_clicks_reached",
            }

    return count_loaded_comment_blocks(page), total_reply_clicks, accumulated, {
        "rounds": rounds,
        "hard_round_limit": hard_round_limit,
        "stable_rounds": stable_rounds,
        "stop_reason": "hard_round_limit",
    }


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
    network_monitor = CommentNetworkMonitor()
    network_monitor.start(page)
    try:
        page.goto(source_url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
        network_monitor.wait_for_idle(page)
        page.wait_for_timeout(1000)
        click_open_comment_panel(page)
        network_monitor.wait_for_idle(page)
        reset_comment_scroll(page)
        page.wait_for_timeout(1000)
        blocker = detect_access_blocker(page)
        if blocker:
            if debug:
                dump_debug_artifacts(page)
            raise TikTokAccessBlocked(blocker)
        wait_for_comments(page)

        target_count = get_target_comment_count(page)
        _, reply_clicks, accumulated_comments, expand_stats = expand_comments_and_replies(
            page,
            scroll_times=scroll_times or settings.scroll_times,
            max_reply_clicks=max_reply_clicks or settings.max_reply_clicks,
            target_comment_count=target_count,
            source_url=source_url,
            network_monitor=network_monitor,
        )

        blocker = detect_access_blocker(page)
        if blocker:
            if debug:
                dump_debug_artifacts(page)
            raise TikTokAccessBlocked(blocker)

        comments = merge_comment_items(accumulated_comments, collect_comment_data(page, source_url=source_url))
        comments = merge_comment_items(comments, network_monitor.collect_api_comments(source_url=source_url))
        if debug:
            total_replies = sum(len(item.get("replies", [])) for item in comments)
            print(
                "[DEBUG] target_comments="
                f"{target_count} collected_comments={len(comments)} collected_replies={total_replies} "
                f"reply_clicks={reply_clicks} scroll_rounds={expand_stats['rounds']} "
                f"stop_reason={expand_stats['stop_reason']} "
                f"comment_network_requests={network_monitor.request_count} "
                f"comment_data_responses={network_monitor.data_response_count} "
                f"api_comments={len(network_monitor.api_items_by_cid)} "
                f"api_pending_reply_parents={len(network_monitor.pending_replies_by_parent_cid)} "
                f"pending_comment_network={len(network_monitor.pending)}"
            )
            if not comments:
                dump_debug_artifacts(page)

        return comments
    finally:
        network_monitor.stop(page)
