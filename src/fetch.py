"""
fetch.py — Fetch and parse obituary text from a URL.
"""

import re

import requests
from bs4 import BeautifulSoup


class FetchError(Exception):
    """Raised on HTTP errors or HTML parsing failures."""


def fetch_obituary_text(url: str) -> str:
    """
    Fetch an obituary page and return clean plain text of the article body.

    Strategy (in order):
      1. <div class="entry-content"> — WordPress / Tri-County Weekly
      2. <article> tag — common semantic layout
      3. Largest <div> by text length — generic fallback

    Args:
        url: Full URL of the obituary page.

    Returns:
        Clean plain text of the article body (no HTML tags, minimal whitespace).

    Raises:
        FetchError: On HTTP error, connection failure, or empty parse result.
    """
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "far-west-legacy/0.1"})
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise FetchError(f"HTTP error fetching {url}: {exc}") from exc
    except requests.RequestException as exc:
        raise FetchError(f"Network error fetching {url}: {exc}") from exc

    soup = BeautifulSoup(response.text, "lxml")

    # Remove navigation, header, footer, sidebar noise
    for tag in soup.find_all(["nav", "header", "footer", "aside", "script", "style"]):
        tag.decompose()

    container = (
        soup.find("div", class_="entry-content")
        or soup.find("article")
        or _largest_div(soup)
    )

    if container is None:
        raise FetchError(f"Could not locate article content in page: {url}")

    raw = container.get_text(separator=" ")
    text = _clean_whitespace(raw)

    if not text:
        raise FetchError(f"Extracted text is empty for URL: {url}")

    return text


def _largest_div(soup: BeautifulSoup):
    """Return the <div> with the most text content, or None if no divs exist."""
    divs = soup.find_all("div")
    if not divs:
        return None
    return max(divs, key=lambda d: len(d.get_text()))


def _clean_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip edges."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
