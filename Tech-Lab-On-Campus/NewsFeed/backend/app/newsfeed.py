"""Module for retrieving newsfeed information."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import redis

# Initialize Redis client
_redis = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# ─────────────────────────────────────────────
# Data Model
# ─────────────────────────────────────────────

@dataclass
class Article:
    """Dataclass for an article."""
    author: str
    title: str
    body: str
    publish_date: datetime
    image_url: str
    url: str
    article_id: str
    sector: str
    view_count: int = 0


# ─────────────────────────────────────────────
# Internal Helpers
# ─────────────────────────────────────────────

def _parse_article(data: dict, article_id: str) -> Article:
    """Parse a raw Redis hash into an Article dataclass."""
    return Article(
        author=data["author"],
        title=data["title"],
        body=data["body"],
        publish_date=datetime.fromisoformat(data["publish_date"]),
        image_url=data["image_url"],
        url=data["url"],
        article_id=article_id,
        sector=data["sector"],
        view_count=int(data.get("view_count", 0)),
    )


def _get_article_by_id(article_id: str) -> Optional[Article]:
    """Fetch and parse a single article by its ID."""
    data = _redis.hgetall(f"article:{article_id}")
    return _parse_article(data, article_id) if data else None


# ─────────────────────────────────────────────
# Core News Retrieval
# ─────────────────────────────────────────────

def get_all_news() -> list[Article]:
    """Get all news articles sorted by most recent publish date."""
    keys = _redis.keys("article:*")
    if not keys:
        return []

    articles = []
    for key in keys:
        data = _redis.hgetall(key)
        if data:
            article_id = key.split(":", 1)[1]
            articles.append(_parse_article(data, article_id))

    return sorted(articles, key=lambda a: a.publish_date, reverse=True)


def get_featured_news() -> Article | None:
    """Get the most recently published article."""
    articles = get_all_news()
    return articles[0] if articles else None


# ─────────────────────────────────────────────
# Follow-Up 1: Bookmarks
#
# Data structure: Redis List — `bookmarks:{user_id}`
#
# A List preserves insertion order naturally, which directly satisfies
# the requirement to keep bookmarks in the order they were added.
# RPUSH appends to the tail (O(1)), LREM removes by value (O(N) on the
# list length, acceptable since bookmark lists are small per user), and
# LRANGE retrieves the full list in order (O(N)). A Set would give O(1)
# removal but loses ordering. A Sorted Set could preserve order via a
# timestamp score, but adds complexity without benefit here since the
# List already gives us chronological order for free.
# ─────────────────────────────────────────────

def bookmark_article(user_id: str, article_id: str) -> None:
    """Bookmark an article for a user (no-op if already bookmarked)."""
    key = f"bookmarks:{user_id}"

    # Guard against duplicates — LPOS returns the index if found, else None
    if _redis.lpos(key, article_id) is None:
        _redis.rpush(key, article_id)


def remove_bookmark(user_id: str, article_id: str) -> None:
    """Remove a bookmark for a user."""
    # LREM key count value — count=0 removes all occurrences
    _redis.lrem(f"bookmarks:{user_id}", 0, article_id)


def get_bookmarks(user_id: str) -> list[Article]:
    """Retrieve bookmarked articles in the order they were bookmarked."""
    article_ids = _redis.lrange(f"bookmarks:{user_id}", 0, -1)

    articles = []
    for article_id in article_ids:
        article = _get_article_by_id(article_id)
        if article:
            articles.append(article)

    return articles


# ─────────────────────────────────────────────
# Follow-Up 2: View Counts & Top Viewed
#
# Data structure: Redis Sorted Set — `article:views`
#
# A Sorted Set maps each article_id (member) to its view count (score).
# ZINCRBY increments the score atomically in O(log N), which is critical
# for correctness under concurrent reads. ZREVRANGE retrieves the top-N
# members by score in O(log N + N), which is optimal — no full scan or
# sort needed. Storing view_count only in the article hash would require
# fetching and sorting all articles to find the top N (O(M log M) for M
# articles). The Sorted Set makes get_top_viewed_articles O(log M + N)
# regardless of total article count.
# ─────────────────────────────────────────────

VIEWS_KEY = "article:views"


def record_article_view(article_id: str) -> None:
    """Increment the view count for an article."""
    # Atomic increment in the Sorted Set + mirror to the article hash
    _redis.zincrby(VIEWS_KEY, 1, article_id)
    _redis.hincrby(f"article:{article_id}", "view_count", 1)


def get_top_viewed_articles(n: int) -> list[Article]:
    """Retrieve the top n most viewed articles, descending by view count."""
    # ZREVRANGE returns members with the highest scores first
    top_ids = _redis.zrevrange(VIEWS_KEY, 0, n - 1, withscores=False)

    articles = []
    for article_id in top_ids:
        article = _get_article_by_id(article_id)
        if article:
            articles.append(article)

    return articles


# ─────────────────────────────────────────────
# Follow-Up 3: Most Popular Sector
#
# Ambiguity note: "popular" is intentionally vague. Two reasonable
# interpretations are:
#   (a) Most articles published — the sector with the highest article count.
#   (b) Most total views — the sector whose articles have the most combined views.
#
# In an interview, you'd surface this ambiguity and align with the
# interviewer before coding. Here both are implemented. The default
# uses total views (b), since view count better reflects what users
# actually find engaging rather than what editors publish most.
#
# Data structure: Redis Hash — `sector:article_count` and `sector:view_count`
#
# Two hashes let us track both metrics independently and update them
# atomically with HINCRBY (O(1)) on each article publish or view event.
# At query time, HGETALL fetches all sector scores in one round-trip,
# and we do a single linear scan to find the max — O(S) where S is the
# number of sectors, which is a small constant in practice.
# ─────────────────────────────────────────────

SECTOR_COUNT_KEY = "sector:article_count"
SECTOR_VIEWS_KEY = "sector:view_count"


def register_article_sector(article_id: str, sector: str) -> None:
    """Called when an article is published — updates sector article count."""
    _redis.hincrby(SECTOR_COUNT_KEY, sector, 1)


def record_sector_view(sector: str) -> None:
    """Called when an article is viewed — updates sector view total."""
    _redis.hincrby(SECTOR_VIEWS_KEY, sector, 1)


def get_most_popular_sector(by: str = "views") -> str | None:
    """
    Retrieve the most popular sector.

    Args:
        by: "views"  — sector with the highest total view count (default)
            "count"  — sector with the most published articles

    Returns:
        The sector name, or None if no data exists.
    """
    key = SECTOR_VIEWS_KEY if by == "views" else SECTOR_COUNT_KEY
    sector_scores = _redis.hgetall(key)

    if not sector_scores:
        return None

    return max(sector_scores, key=lambda s: int(sector_scores[s]))
