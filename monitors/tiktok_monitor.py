from dataclasses import dataclass

from pymongo.collection import Collection

from database.mongo import save_comments


@dataclass
class CommentDiff:
    seeded: bool
    new_comments: list[dict]
    new_replies: list[dict]


def _reply_key(reply: dict) -> tuple[str, str]:
    return (reply.get("name", ""), reply.get("comment", ""))


def diff_comments(items: list[dict], source_url: str, collection: Collection) -> CommentDiff:
    existing_docs = list(collection.find({"source_url": source_url}, {"name": 1, "comment": 1, "replies": 1}))
    if not existing_docs:
        return CommentDiff(seeded=True, new_comments=[], new_replies=[])

    existing_by_parent = {
        (doc.get("name", ""), doc.get("comment", "")): {
            _reply_key(reply) for reply in doc.get("replies", [])
        }
        for doc in existing_docs
    }

    new_comments: list[dict] = []
    new_replies: list[dict] = []

    for item in items:
        parent_key = (item.get("name", ""), item.get("comment", ""))
        existing_replies = existing_by_parent.get(parent_key)
        if existing_replies is None:
            new_comments.append(item)
            continue

        for reply in item.get("replies", []):
            if _reply_key(reply) in existing_replies:
                continue
            new_replies.append({"parent": item, "reply": reply})

    return CommentDiff(seeded=False, new_comments=new_comments, new_replies=new_replies)


def save_and_diff_comments(items: list[dict], source_url: str, collection: Collection) -> CommentDiff:
    diff = diff_comments(items, source_url, collection)
    save_comments(items, source_url, collection=collection, prune_stale=False)
    return diff
