from collectors.tiktok_comment_collector import (
    clean_comments,
    is_comment_network_url,
    merge_comment_items,
    normalize_source_url,
)


def test_normalize_source_url_strips_query_hash_and_trailing_slash():
    url = "https://www.tiktok.com/@user/video/123456/?is_from_webapp=1#comments"

    assert normalize_source_url(url) == "https://www.tiktok.com/@user/video/123456"


def test_is_comment_network_url_matches_comment_and_reply_apis():
    assert is_comment_network_url("https://www.tiktok.com/api/comment/list/?aweme_id=1")
    assert is_comment_network_url("https://www.tiktok.com/api/comment/list/reply/?comment_id=2")
    assert not is_comment_network_url("https://www.tiktok.com/api/post/item_list/")


def test_clean_comments_trims_and_dedupes_comments_and_replies():
    items = [
        {
            "name": " Alice ",
            "comment": " hello   world ",
            "replies": [
                {"name": " Bob ", "comment": " reply one "},
                {"name": "Bob", "comment": "reply one"},
                {"name": "Nobody", "comment": ""},
            ],
        },
        {"name": "Alice", "comment": "hello world", "replies": []},
        {"name": "Empty", "comment": "   "},
    ]

    assert clean_comments(items, source_url="https://example.test/video") == [
        {
            "name": "Alice",
            "comment": "hello world",
            "replies": [{"name": "Bob", "comment": "reply one"}],
        }
    ]


def test_clean_comments_filters_tiktok_ui_text_from_replies():
    items = [
        {
            "name": "Alice",
            "comment": "real comment",
            "replies": [
                {"name": "", "comment": "Reply"},
                {"name": "", "comment": "View more replies"},
                {"name": "Bob", "comment": "real reply"},
            ],
        }
    ]

    assert clean_comments(items, source_url="https://example.test/video") == [
        {
            "name": "Alice",
            "comment": "real comment",
            "replies": [{"name": "Bob", "comment": "real reply"}],
        }
    ]


def test_merge_comment_items_keeps_existing_comments_and_merges_replies():
    existing = [{"name": "Alice", "comment": "parent", "replies": [{"name": "Bob", "comment": "one"}]}]
    new_items = [
        {"name": "Alice", "comment": "parent", "replies": [{"name": "Bob", "comment": "one"}, {"name": "Cat", "comment": "two"}]},
        {"name": "Dan", "comment": "other", "replies": []},
    ]

    assert merge_comment_items(existing, new_items) == [
        {
            "name": "Alice",
            "comment": "parent",
            "replies": [{"name": "Bob", "comment": "one"}, {"name": "Cat", "comment": "two"}],
        },
        {"name": "Dan", "comment": "other", "replies": []},
    ]
