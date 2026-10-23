"""Collect all comments for a Polymarket event through the Gamma API.

fetch_comments(link) returns the comments as a list.
getComments(link) does the same and also saves them to data.json,
which the training scripts read.
"""

import json
import logging
import os
import time
from urllib.parse import urlparse

import requests

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
PAGE_SIZE = 100
MAX_PAGES = 200            # safety stop: at most 20,000 comments per event
REQUEST_TIMEOUT = 10       # seconds for each HTTP request
MAX_RETRIES = 3
BACKOFF_SECONDS = 0.5      # wait 0.5s, then 1s, then 2s between retries
PAGE_DELAY = 0.2           # small pause between pages to be polite to the API

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_PATH = os.path.join(BASE_DIR, "data.json")

log = logging.getLogger(__name__)


class CommentFetchError(Exception):
    """Raised when comments for an event cannot be collected."""


def extract_event_slug(link):
    """Get the event slug from a Polymarket link.

    Works with:
      https://polymarket.com/event/<slug>
      https://polymarket.com/event/<slug>/            (trailing slash)
      https://polymarket.com/event/<slug>?tid=123     (query string)
      https://polymarket.com/event/<slug>/<market>    (link to one market)
      <slug>                                          (just the slug)
    """
    if not link or not link.strip():
        raise CommentFetchError("Empty event link.")

    link = link.strip()
    parsed = urlparse(link if "://" in link else "https://x/" + link)
    parts = [p for p in parsed.path.split("/") if p]

    if "event" in parts:
        i = parts.index("event")
        if i + 1 < len(parts):
            return parts[i + 1]
        raise CommentFetchError(f"No event slug found in link: {link}")

    if "://" not in link and len(parts) == 1:
        return parts[0]

    raise CommentFetchError(f"Not a Polymarket event link: {link}")


def _get_json(session, url, params=None):
    """GET a URL and return JSON. Retries on network errors, 429 and 5xx."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_error = e
        else:
            if resp.status_code == 404:
                raise CommentFetchError(f"Not found: {url}")
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = CommentFetchError(f"HTTP {resp.status_code} from {url}")
            elif resp.status_code >= 400:
                raise CommentFetchError(f"HTTP {resp.status_code} from {url}")
            else:
                try:
                    return resp.json()
                except ValueError as e:
                    raise CommentFetchError(f"Bad JSON from {url}") from e

        if attempt < MAX_RETRIES:
            wait = BACKOFF_SECONDS * (2 ** attempt)
            log.warning("Request failed (%s), retrying in %.1fs", last_error, wait)
            time.sleep(wait)

    raise CommentFetchError(f"Gave up after {MAX_RETRIES + 1} tries: {last_error}")


def fetch_comments(link, session=None):
    """Return a list of {"body": text} for every comment on the event."""
    session = session or requests.Session()
    slug = extract_event_slug(link)

    event = _get_json(session, f"{GAMMA_BASE_URL}/events/slug/{slug}")
    event_id = event.get("id") if isinstance(event, dict) else None
    if not event_id:
        raise CommentFetchError(f"Event '{slug}' has no id in the API response.")

    # Polymarket limits offset paging (offset > ~200 returns HTTP 422 "offset
    # too large"), so we use the keyset endpoint and follow next_cursor.
    comments = []
    seen_ids = set()
    cursor = None
    for _ in range(MAX_PAGES):
        params = {
            "parent_entity_type": "Event",
            "parent_entity_id": event_id,
            "limit": PAGE_SIZE,
        }
        if cursor:
            params["after_cursor"] = cursor
        data = _get_json(session, f"{GAMMA_BASE_URL}/comments/keyset", params)
        page = data.get("comments") if isinstance(data, dict) else None
        if not page:
            break

        for c in page:
            # Skip duplicates (a comment can show up on two pages)
            cid = c.get("id")
            if cid is not None:
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
            body = (c.get("body") or "").strip()
            if body:
                comments.append({"body": body})

        next_cursor = data.get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(PAGE_DELAY)
    else:
        log.warning("Stopped at %d pages for '%s'", MAX_PAGES, slug)

    return comments


def save_comments(comments, path=DEFAULT_OUTPUT_PATH):
    """Write comments to a JSON file (used by the training scripts)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(comments, f, indent=4, ensure_ascii=False)


def getComments(link, output_path=DEFAULT_OUTPUT_PATH):
    """Fetch comments, save them to data.json, and return them.

    Kept for the training scripts (main.py, training_data_expansion.py).
    The API uses fetch_comments() directly so requests don't share a file.
    """
    comments = fetch_comments(link)
    save_comments(comments, output_path)
    print(f"Saved {len(comments)} comments -> {output_path}")
    return comments
