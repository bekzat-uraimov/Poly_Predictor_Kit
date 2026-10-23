"""Tests for commentsReceiver. Run with: pytest Emotional_Damage_Predictor

No real network calls: a fake session returns prepared responses.
"""

import pytest
import requests

import commentsReceiver as cr
from commentsReceiver import CommentFetchError, extract_event_slug, fetch_comments


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # Retries and page delays should not slow the tests down
    monkeypatch.setattr(cr.time, "sleep", lambda s: None)


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    """Returns the next prepared item for each GET. An exception item is raised."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def event(event_id="123"):
    return FakeResponse(200, {"id": event_id})


def page(start, count, next_cursor=None):
    comments = [{"id": i, "body": f"comment {i}"} for i in range(start, start + count)]
    return FakeResponse(200, {"comments": comments, "next_cursor": next_cursor})


# ---------- extract_event_slug ----------

@pytest.mark.parametrize("link", [
    "https://polymarket.com/event/fed-rate-cut",
    "https://polymarket.com/event/fed-rate-cut/",
    "https://polymarket.com/event/fed-rate-cut?tid=1763315191827",
    "https://polymarket.com/event/fed-rate-cut/will-the-fed-cut-in-december",
    "  https://polymarket.com/event/fed-rate-cut  ",
    "fed-rate-cut",
])
def test_slug_from_different_link_shapes(link):
    assert extract_event_slug(link) == "fed-rate-cut"


@pytest.mark.parametrize("link", ["", "   ", "https://polymarket.com/", "https://polymarket.com/event/"])
def test_bad_links_raise(link):
    with pytest.raises(CommentFetchError):
        extract_event_slug(link)


# ---------- fetch_comments ----------

def test_follows_cursor_until_no_next_cursor():
    session = FakeSession([event(), page(0, 100, "c1"), page(100, 100, "c2"), page(200, 30)])
    comments = fetch_comments("https://polymarket.com/event/x", session=session)
    assert len(comments) == 230
    assert comments[0] == {"body": "comment 0"}
    # 1 event lookup + 3 pages
    assert len(session.calls) == 4
    assert session.calls[1][0].endswith("/comments/keyset")
    assert [c[1].get("after_cursor") for c in session.calls[1:]] == [None, "c1", "c2"]
    # every request has a timeout
    assert all(c[2] == cr.REQUEST_TIMEOUT for c in session.calls)


def test_stops_if_cursor_repeats():
    session = FakeSession([event(), page(0, 5, "same"), page(5, 5, "same")])
    assert len(fetch_comments("x", session=session)) == 10
    assert len(session.calls) == 3


def test_empty_event_returns_empty_list():
    session = FakeSession([event(), FakeResponse(200, {"comments": [], "next_cursor": None})])
    assert fetch_comments("x", session=session) == []


def test_skips_duplicates_and_empty_bodies():
    data = [{"id": 1, "body": "a"}, {"id": 1, "body": "a"}, {"id": 2, "body": "  "}, {"id": 3, "body": None}]
    session = FakeSession([event(), FakeResponse(200, {"comments": data})])
    assert fetch_comments("x", session=session) == [{"body": "a"}]


def test_retries_on_server_error_then_succeeds():
    session = FakeSession([FakeResponse(503), FakeResponse(429), event(), page(0, 5)])
    assert len(fetch_comments("x", session=session)) == 5


def test_retries_on_timeout_then_succeeds():
    session = FakeSession([requests.Timeout(), event(), page(0, 5)])
    assert len(fetch_comments("x", session=session)) == 5


def test_gives_up_after_max_retries():
    session = FakeSession([FakeResponse(500)] * (cr.MAX_RETRIES + 1))
    with pytest.raises(CommentFetchError, match="Gave up"):
        fetch_comments("x", session=session)


def test_unknown_event_fails_fast_without_retry():
    session = FakeSession([FakeResponse(404)])
    with pytest.raises(CommentFetchError, match="Not found"):
        fetch_comments("x", session=session)
    assert len(session.calls) == 1


def test_event_without_id_raises():
    session = FakeSession([FakeResponse(200, {"title": "no id"})])
    with pytest.raises(CommentFetchError, match="no id"):
        fetch_comments("x", session=session)


def test_bad_json_raises():
    session = FakeSession([FakeResponse(200, ValueError("not json"))])
    with pytest.raises(CommentFetchError, match="Bad JSON"):
        fetch_comments("x", session=session)


def test_stops_at_max_pages(monkeypatch):
    monkeypatch.setattr(cr, "MAX_PAGES", 2)
    session = FakeSession([event(), page(0, 100, "c1"), page(100, 100, "c2")])
    assert len(fetch_comments("x", session=session)) == 200


def test_getComments_saves_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "fetch_comments", lambda link: [{"body": "hi"}])
    out = tmp_path / "data.json"
    assert cr.getComments("x", output_path=str(out)) == [{"body": "hi"}]
    assert '"hi"' in out.read_text(encoding="utf-8")
