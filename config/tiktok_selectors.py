COMMENT_CONTAINER_CANDIDATES = [
    '[data-e2e="search-comment-container"]',
    '[data-e2e="comment-list"]',
    '[data-e2e*="comment-list"]',
    '[data-e2e*="comment-container"]',
    '[class*="DivCommentListContainer"]',
    '[class*="DivCommentContainer"]',
    'section[aria-label*="comment" i]',
]

COMMENT_BLOCK_CANDIDATES = [
    '[data-e2e="comment-item"]',
    '[data-e2e*="comment-item"]',
    'div:has([data-e2e*="comment-level-1"])',
    '[class*="DivCommentItemContainer"]',
    '[class*="CommentItem"]',
]

AUTHOR_CANDIDATES = [
    '[data-e2e="comment-username"]',
    '[data-e2e*="comment-username"]',
    'a[href^="/@"] span',
    'a[href^="/@"]',
]

TEXT_CANDIDATES = [
    '[data-e2e="comment-level-1"]',
    '[data-e2e*="comment-level-1"]',
    '[data-e2e*="comment-level-2"]',
    '[data-e2e="comment-text"]',
    '[data-e2e*="comment-text"]',
    'p',
    'span[dir="auto"]',
]

REPLY_BLOCK_CANDIDATES = [
    '[data-e2e="comment-reply"]',
    '[data-e2e*="comment-reply"]',
    'div:has([data-e2e*="comment-level-2"])',
    '[class*="ReplyComment"]',
    '[class*="DivReplyContainer"] [class*="Comment"]',
]

REPLY_BUTTON_TEXT_PATTERNS = [
    "View replies",
    "View more replies",
    "View previous replies",
    "more replies",
    "replies",
    "câu trả lời",
    "Xem thêm",
]
