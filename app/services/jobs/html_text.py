"""HTML -> plain text for job descriptions returned by ATS APIs (Greenhouse
etc. return the description as an HTML blob). Not a general-purpose HTML
scraper - just strips markup so the text is embeddable, instead of
embedding entire noisy HTML pages.
"""

from __future__ import annotations

import html as html_entities

from bs4 import BeautifulSoup

_BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


def html_to_text(html: str | None) -> str:
    if not html:
        return ""

    # Some sources hand over HTML that was entity-escaped once more
    # ("&lt;p&gt;..." - Greenhouse's API, and JSON-LD descriptions on
    # Greenhouse-backed pages such as Wolt's). Left alone, the markup
    # survives as literal text. Only when the text has escaped tags and
    # no real ones, so genuine "&lt;" inside proper HTML stays intact.
    if "&lt;" in html and "<" not in html:
        html = html_entities.unescape(html)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Insert a newline after each block-level element so paragraphs don't
    # collapse into one run-on line once tags are stripped.
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")

    text = soup.get_text()
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)
