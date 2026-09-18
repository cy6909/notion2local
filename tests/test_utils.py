from notion2local.utils import content_hash


def test_content_hash_ignores_notion_transport_request_id() -> None:
    payload = {"id": "page-1", "request_id": "request-a", "properties": {"title": "Stable"}}
    replay = {**payload, "request_id": "request-b"}

    assert content_hash(payload) == content_hash(replay)


def test_content_hash_still_changes_for_real_content() -> None:
    first = {"id": "page-1", "request_id": "request-a", "properties": {"title": "Stable"}}
    second = {"id": "page-1", "request_id": "request-b", "properties": {"title": "Changed"}}

    assert content_hash(first) != content_hash(second)
