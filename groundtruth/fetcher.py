import re
from html.parser import HTMLParser
from typing import Optional, Tuple

from requests import RequestException, get

# Void elements never get an end tag, so they must never open a skip region --
# nothing would ever close it and the rest of the document would be discarded.
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# Tags whose text is never article content.
_DISCARD_TAGS = {"script", "style", "svg", "noscript", "iframe", "object", "embed"}

# Matched against whole words in class/id, never as substrings: "ad" must not
# fire on Tailwind names like "shadow-lg", "leading-relaxed" or "bg-gradient".
_NOISE_TOKENS = {
    "nav", "navbar", "navigation", "menu", "sidebar", "ad", "ads", "advert",
    "advertisement", "cookie", "cookies", "comment", "comments", "footer",
    "social", "toolbar", "paywall",
}

# The content containers themselves are never noise, whatever their class says:
# Substack ships <article class="newsletter-post">, and matching "newsletter"
# there threw away the entire post.
_CONTENT_TAGS = {"article", "main", "body"}

# Sites reject the default python-requests agent outright (403), so present a
# normal browser UA.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Below this many characters of body text the "article" is a nav stub, a consent
# wall or a JS shell -- not something worth handing to the model.
_MIN_BODY_CHARS = 400


class ArticleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.body_parts = []
        self._in_title = False
        self._context_stack = []
        # Depth of currently open non-void elements, and the depth of the
        # element that opened the region we're skipping (None = not skipping).
        # Comparing depths -- rather than tag names -- keeps same-named nesting
        # (a <div> inside a skipped <div>) from ending the skip early.
        self._depth = 0
        self._skip_from_depth = None
        self._block_tags = {
            "p",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "blockquote",
            "pre",
            "figcaption",
            "td",
            "th",
            "div",
            "section",
        }

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)

        # Void tags carry no text and never close. Opening a skip region on one
        # would strand it open for the rest of the document.
        if tag in _VOID_TAGS:
            return

        self._depth += 1

        if self._skip_from_depth is not None:
            return

        if tag in _DISCARD_TAGS or self._is_noise_tag(tag, attrs_dict):
            self._skip_from_depth = self._depth
            return

        if tag == "title":
            self._in_title = True
            return

        if tag in {"article", "main", "body", "section"}:
            self._context_stack.append(tag)
            return

        if tag in self._block_tags and self._context_stack:
            self.body_parts.append("\n\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return

        if self._skip_from_depth is not None:
            if self._depth <= self._skip_from_depth:
                self._skip_from_depth = None
            self._depth = max(0, self._depth - 1)
            return

        self._depth = max(0, self._depth - 1)

        if tag == "title":
            self._in_title = False
            return

        if tag in {"article", "main", "body", "section"} and self._context_stack:
            self._context_stack.pop()

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
            return

        if self._skip_from_depth is not None:
            return

        if not self._context_stack:
            return

        text = re.sub(r"\s+", " ", data).strip()
        if text:
            # Leading space so text split across inline tags (<a>, <em>, <code>)
            # doesn't concatenate into "likeFrontier-BenchandGDPval-AA".
            # _normalize_paragraphs collapses the runs afterwards.
            self.body_parts.append(" " + text)

    def _is_noise_tag(self, tag, attrs):
        if tag in _CONTENT_TAGS:
            return False

        if tag in {"nav", "footer", "aside", "form", "button", "menu"}:
            return True

        combined = f"{attrs.get('class', '')} {attrs.get('id', '')}".lower()
        words = set(re.split(r"[^a-z]+", combined))
        return bool(words & _NOISE_TOKENS)


# Chrome that survives tag-level filtering because it sits inside the content
# container: skip links, menu labels, subscribe prompts. Matched against a whole
# paragraph, never a substring, so an article discussing "advertisement" keeps it.
_BOILERPLATE_LINE = re.compile(
    r"^(skip to (?:main )?content|menu|search|subscribe|share this|share|sign ?up"
    r"|log ?in|sign ?in|newsletter|advertisement|cookie[s]? policy|accept cookies"
    r"|privacy policy|terms of service)$",
    re.IGNORECASE,
)

# Skip links repeat inline ("Skip to content Skip to content Home ...") rather
# than on their own paragraph, so the whole-line filter never sees them.
_INLINE_SKIP_LINK = re.compile(r"(?i)(?:skip to (?:main )?content\s*)+")


def _strip_boilerplate(text: str) -> str:
    """Drop nav chrome that the tag-level filters could not reach.

    Every character removed here buys a character of real article text inside the
    content cap, and keeps the model from opening on "Skip to content".
    """
    kept = []
    for paragraph in (text or "").split("\n\n"):
        cleaned = _INLINE_SKIP_LINK.sub("", paragraph).strip()
        if cleaned and not _BOILERPLATE_LINE.match(cleaned):
            kept.append(cleaned)
    return "\n\n".join(kept)


def _normalize_paragraphs(text: str) -> str:
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text or ""):
        cleaned = re.sub(r"\s+", " ", paragraph).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)


def clean_html_to_parts(html: str) -> Tuple[str, str]:
    """Return (title, body) as normalized plain text."""
    parser = ArticleTextParser()
    parser.feed(html or "")
    parser.close()

    title = _normalize_paragraphs(" ".join(parser.title_parts))
    body = _normalize_paragraphs("".join(parser.body_parts))
    return title, body


def clean_html_to_text(html: str) -> str:
    title, body = clean_html_to_parts(html)
    if title and body:
        return f"{title}\n\n{body}"
    return title or body


def fetch_article(url: str) -> Optional[str]:
    """Return the article as plain text, or None if it couldn't be read.

    Returning None matters: a title with no body is worse than nothing, because
    the writing prompt will happily invent the substance the body should have
    supplied. Callers must treat None as "skip this story".
    """
    if not url:
        return None

    try:
        response = get(url, timeout=15, headers=_HEADERS, allow_redirects=True)
    except RequestException as e:
        print(f"Error fetching article from {url}: {e}")
        return None

    if response.status_code != 200:
        print(f"Error fetching article from {url}: Status code {response.status_code}")
        return None

    if "html" not in response.headers.get("Content-Type", "text/html").lower():
        print(f"Skipping {url}: not an HTML document")
        return None

    title, body = clean_html_to_parts(response.text)
    # Strip before the length check, so a page that is *only* nav chrome fails
    # here rather than passing on boilerplate bulk.
    body = _strip_boilerplate(body)
    if len(body) < _MIN_BODY_CHARS:
        print(
            f"Skipping {url}: only extracted {len(body)} chars of body text "
            f"(need {_MIN_BODY_CHARS}) — likely JS-rendered or gated."
        )
        return None

    return f"{title}\n\n{body}" if title else body
