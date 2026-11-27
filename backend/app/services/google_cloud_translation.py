"""Optional Google Cloud Translation Basic comparison of one explicitly shared passage."""
import html
import httpx

from app.core.review import ReviewError, checked_revision


async def compare(settings, store, document, number, region_id, revision, text, source, target, *, transport=None):
    if settings.runtime_mode != "local" or not settings.google_translate_api_key:
        raise ReviewError("google_not_configured", "Google Cloud Translation is not configured on this computer.", 503)
    if source == target:
        raise ReviewError("same_language", "Choose a different target language.")
    page = store.page(document, number)
    checked_revision(page["revision"], revision)
    region = next((r for r in page["regions"] if r["id"] == region_id), None)
    if not region or region["source"] != text or not text.strip():
        raise ReviewError("preview_changed", "The saved passage changed. Preview it again before sending.", 409)
    try:
        async with httpx.AsyncClient(timeout=25, transport=transport, follow_redirects=False) as client:
            response = await client.post("https://translation.googleapis.com/language/translate/v2",
                headers={"X-Goog-Api-Key": settings.google_translate_api_key.get_secret_value()},
                json={"q": [text], "format": "text", "source": source, "target": target})
        if response.status_code == 429:
            raise ReviewError("google_quota", "Google's translation quota was reached. Try again later.", 503)
        if response.status_code in (401, 403):
            raise ReviewError("google_access", "Google refused the request. Check the key, API access and project quota.", 503)
        response.raise_for_status()
        rows = response.json()["data"]["translations"]
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0]["translatedText"], str):
            raise ValueError("invalid response")
        output = html.unescape(rows[0]["translatedText"]).strip()
        if not output:
            raise ValueError("empty response")
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        raise ReviewError("google_unavailable", "Google did not return a usable translation. Try again when connected.", 503) from None
    return {"translation": output, "provider": "Google Cloud Translation", "source": source, "target": target}
