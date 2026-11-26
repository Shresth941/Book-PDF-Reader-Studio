"""Exact matches from the two Legislative Department editions of 9 November 2015."""
from functools import lru_cache
import json
from pathlib import Path

from app.services.legal_translation import normalized

HINDI_SHA = "020206e0904a285dca191065be61f88d5e9bfdd8df2d4c1f693be821cad27192"
DATA = Path(__file__).with_name("reference")
SUFFIX = str.maketrans("कखगघङचछजझञटठडढणतथदधनपफबभमय", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@lru_cache(maxsize=2)
def index(name):
    return json.loads((DATA / f"constitution-2015-{name}.json").read_text(encoding="utf-8"))


def contents_title(label, source):
    article = label.rstrip(".").translate(SUFFIX)
    item = index("titles").get(article)
    if item and normalized(source) == item["hi"]:
        return article, item
    return None


def published_passage(source, page):
    item = index("passages").get(normalized(source))
    return item if item and item["hi_page"] == page else None


def reading_location(page):
    if page <= 8:
        return {"page": page, "label": "Front matter"}
    if page == 33:
        return {"page": 29, "label": "Preamble"}
    items = list(index("titles").items())
    if 9 <= page <= 32:
        matches = [item for _, item in items if item["hi_contents"] == page]
        return {"page": matches[0]["en_contents"] if matches else 9, "label": "Contents"}
    if 34 <= page <= 334:
        earlier = [(article, item) for article, item in items if item["hi_page"] <= page]
        if earlier:
            anchor = max(item["hi_page"] for _, item in earlier)
            article, item = next((key, val) for key, val in earlier if val["hi_page"] == anchor)
            return {"page": item["en_page"], "label": f"Article {article} · start of provision"}
    return {"page": 9, "label": "English contents · select a schedule or appendix"}
