"""
tests/test_fetch.py — Tests for the URL fetching and HTML parsing module.
"""

import os

import pytest
import requests

from src.fetch import FetchError, _clean_whitespace, fetch_obituary_text

NETWORK_AVAILABLE = os.getenv("SKIP_NETWORK_TESTS", "").lower() not in ("", "0", "false")


# ---------------------------------------------------------------------------
# Unit tests — no network call
# ---------------------------------------------------------------------------


class TestParseHtmlFixture:
    """Parse small HTML strings directly — no HTTP."""

    WORDPRESS_HTML = """
    <html>
      <body>
        <nav>Site navigation garbage</nav>
        <div class="entry-content">
          <p>John Henry Smith, age 72, of Jamesport, Missouri, passed away January 5, 2024.</p>
          <p>He was born March 3, 1952, in Chillicothe, Missouri.</p>
          <p>Survivors include his wife, Mary Smith.</p>
        </div>
        <footer>Footer garbage</footer>
      </body>
    </html>
    """

    ARTICLE_HTML = """
    <html>
      <body>
        <header>Header garbage</header>
        <article>
          <p>Jane Doe, beloved mother, passed away on February 10, 2023.</p>
          <p>She was born in 1940 in Kansas City.</p>
        </article>
      </body>
    </html>
    """

    FALLBACK_HTML = """
    <html>
      <body>
        <div class="sidebar">Short sidebar text.</div>
        <div class="main-content">
          Alice Walker, age 90, died peacefully at home on March 15, 2022.
          She was preceded in death by her husband, Robert Walker.
          She is survived by three children.
        </div>
      </body>
    </html>
    """

    def _parse(self, html: str) -> str:
        """Parse HTML directly without an HTTP call using BeautifulSoup."""
        from bs4 import BeautifulSoup
        from src.fetch import _clean_whitespace

        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(["nav", "header", "footer", "aside", "script", "style"]):
            tag.decompose()

        container = (
            soup.find("div", class_="entry-content")
            or soup.find("article")
        )
        if container is None:
            divs = soup.find_all("div")
            container = max(divs, key=lambda d: len(d.get_text())) if divs else None

        assert container is not None
        return _clean_whitespace(container.get_text(separator=" "))

    def test_entry_content_div_extracted(self):
        text = self._parse(self.WORDPRESS_HTML)
        assert "John Henry Smith" in text
        assert "Site navigation garbage" not in text
        assert "Footer garbage" not in text

    def test_entry_content_has_full_text(self):
        text = self._parse(self.WORDPRESS_HTML)
        assert "passed away January 5, 2024" in text
        assert "Mary Smith" in text

    def test_article_tag_fallback(self):
        text = self._parse(self.ARTICLE_HTML)
        assert "Jane Doe" in text
        assert "Header garbage" not in text

    def test_largest_div_fallback(self):
        text = self._parse(self.FALLBACK_HTML)
        assert "Alice Walker" in text
        assert "Robert Walker" in text

    def test_clean_whitespace_collapses_spaces(self):
        assert _clean_whitespace("  hello   world  ") == "hello world"

    def test_clean_whitespace_collapses_blank_lines(self):
        result = _clean_whitespace("line1\n\n\n\nline2")
        assert "\n\n\n" not in result

    def test_clean_whitespace_strips_edges(self):
        assert _clean_whitespace("  \n text \n  ") == "text"


# ---------------------------------------------------------------------------
# Integration test — real network call (skipped by default)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not bool(os.getenv("RUN_NETWORK_TESTS")),
    reason="Set RUN_NETWORK_TESTS=1 to run live network tests",
)
class TestFetchRealPage:
    """Fetch a real Tri-County Weekly obituary page."""

    TRI_COUNTY_URL = (
        "https://www.jamesporttricountyweekly.com/obituaries/donna-sue-neese/"
    )

    def test_returns_nonempty_text(self):
        text = fetch_obituary_text(self.TRI_COUNTY_URL)
        assert isinstance(text, str)
        assert len(text) > 100

    def test_contains_deceased_name(self):
        text = fetch_obituary_text(self.TRI_COUNTY_URL)
        assert "Neese" in text

    def test_no_html_tags_in_output(self):
        text = fetch_obituary_text(self.TRI_COUNTY_URL)
        assert "<" not in text and ">" not in text


class TestFetchErrorHandling:
    def test_raises_fetch_error_on_bad_url(self):
        with pytest.raises(FetchError):
            fetch_obituary_text("http://localhost:19999/nonexistent")
