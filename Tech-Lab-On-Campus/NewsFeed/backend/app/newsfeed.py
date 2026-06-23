"""Module for retrieving newsfeed information."""
import json
from dataclasses import dataclass
from datetime import datetime

import redis

# Initialize Redis client
_redis = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

@dataclass
class Article:
    """Dataclass for an article."""
    author: str
    title: str
    body: str
    publish_date: datetime
    image_url: str
    url: str


def _parse_article(data: dict) -> Article:
    """Parse a raw Redis hash into an Article dataclass."""
    return Article(
        author=data["author"],
        title=data["title"],
        body=data["body"],
        publish_date=datetime.fromisoformat(data["publish_date"]),
        image_url=data["image_url"],
        url=data["url"],
    )


def get_all_news() -> list[Article]:
    """Get all news articles from the datastore."""
    # 1. Use Redis client to fetch all article keys
    keys = _redis.keys("article:*")
    if not keys:
        return []

    # 2. Fetch each article hash and parse it
    articles = []
    for key in keys:
        data = _redis.hgetall(key)
        if data:
            articles.append(_parse_article(data))

    # 3. Return articles sorted by most recent publish date
    return sorted(articles, key=lambda a: a.publish_date, reverse=True)


def get_featured_news() -> Article | None:
    """Get the featured news article from the datastore."""
    # 1. Get all articles (already sorted by most recent date)
    articles = get_all_news()

    # 2. Return the most recently published article, or None if empty
    return articles[0] if articles else None
