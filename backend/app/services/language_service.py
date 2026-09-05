import asyncio
import re
import urllib.parse
import unicodedata

import httpx
from fastapi import HTTPException

from app.core.cache import TimedLruCache


def _detect_language(text: str, requested: str) -> str:
    """Resolve auto to the two languages supported by the reader."""
    if requested in ("en", "hi"):
        return requested
    devanagari = sum("\u0900" <= character <= "\u097f" for character in text)
    latin = sum(character.isascii() and character.isalpha() for character in text)
    if devanagari and devanagari >= latin / 2:
        return "hi"
    return "en"


# These are deliberately small, high-confidence legal equivalents. They make
# the terms used repeatedly in a legal PDF consistent without forcing legal
# wording onto an unrelated document. The Easements Act title uses "सुखाचार"
# in India Code; "सुखाधिकार" is retained as a familiar search alias.
LEGAL_GLOSSARY: dict[str, tuple[str, str, str]] = {
    "indian easements act": (
        "भारतीय सुखाचार अधिनियम, 1882",
        "The Indian statute governing easements and licences.",
        "सुखाचार और अनुज्ञप्तियों से संबंधित भारतीय अधिनियम।",
    ),
    "easement": (
        "सुखाचार",
        "A legal right over another person's land for the beneficial enjoyment of one's own land.",
        "अपनी भूमि के लाभकारी उपभोग के लिए किसी अन्य व्यक्ति की भूमि पर प्राप्त विधिक अधिकार।",
    ),
    "dominant heritage": (
        "प्रधान सम्पदा",
        "The land for whose beneficial enjoyment an easement exists.",
        "वह प्रधान सम्पदा जिसके लाभकारी उपभोग के लिए सुखाचार विद्यमान है।",
    ),
    "servient heritage": (
        "सेवक सम्पदा",
        "The land on which the liability of an easement is imposed.",
        "वह सेवक सम्पदा जिस पर सुखाचार का भार लगाया जाता है।",
    ),
    "dominant owner": (
        "प्रधान स्वामी",
        "The owner or occupier of the dominant heritage.",
        "प्रधान सम्पदा का स्वामी या अधिभोगी।",
    ),
    "servient owner": (
        "सेवक स्वामी",
        "The owner or occupier of the servient heritage.",
        "सेवक सम्पदा का स्वामी या अधिभोगी।",
    ),
    "right of way": (
        "मार्गाधिकार",
        "An easement allowing passage over another person's land.",
        "दूसरे व्यक्ति की भूमि से होकर आने-जाने की अनुमति देने वाला सुखाचार।",
    ),
    "licence": (
        "अनुज्ञप्ति",
        "Permission to do an act that would otherwise be unlawful; it does not itself create an easement.",
        "किसी ऐसे कार्य की अनुमति जो अन्यथा विधि-विरुद्ध होता; इससे अपने-आप सुखाचार उत्पन्न नहीं होता।",
    ),
    "section": (
        "धारा",
        "A numbered provision within an Act or other legislation.",
        "किसी अधिनियम या अन्य विधि का क्रमांकित प्रावधान।",
    ),
    "act": (
        "अधिनियम",
        "A statute enacted by a legislature.",
        "विधायिका द्वारा बनाया गया अधिनियम।",
    ),
    "court": (
        "न्यायालय",
        "A judicial body that interprets and applies the law.",
        "विधि की व्याख्या और प्रयोग करने वाला न्यायिक निकाय।",
    ),
    "repeal": (
        "निरसन",
        "The formal revocation of a law or legal provision.",
        "किसी विधि या विधिक प्रावधान का औपचारिक निरसन।",
    ),
    "jurisdiction": (
        "अधिकारिता",
        "The legal authority of a court or other body to decide a matter.",
        "किसी विषय का निर्णय करने के लिए न्यायालय या अन्य निकाय की विधिक शक्ति।",
    ),
}

LEGAL_ALIASES = {
    "easements": "easement",
    "license": "licence",
    "सुखाधिकार": "easement",
    "सुखाचार": "easement",
    "प्रधान संपदा": "dominant heritage",
    "प्रधान सम्पदा": "dominant heritage",
    "सेवक संपदा": "servient heritage",
    "सेवक सम्पदा": "servient heritage",
    "मार्ग अधिकार": "right of way",
    "मार्गाधिकार": "right of way",
    "अनुज्ञप्ति": "licence",
    "धारा": "section",
    "अधिनियम": "act",
    "न्यायालय": "court",
    "निरसन": "repeal",
    "अधिकारिता": "jurisdiction",
}


def _normalized_term_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).casefold()).strip(" .,:;!?\"'“”‘’")


def _legal_entry(value: str) -> tuple[str, tuple[str, str, str]] | None:
    key = _normalized_term_key(value)
    canonical = LEGAL_ALIASES.get(key, key)
    entry = LEGAL_GLOSSARY.get(canonical)
    return (canonical, entry) if entry else None


def _document_domain(*parts: str) -> str:
    """Classify a document locally from its title and nearby text."""
    text = " ".join(part for part in parts if part).casefold()
    if not text:
        return "general"
    legal_markers = (
        "easement", "dominant heritage", "servient heritage", "statute", "jurisdiction",
        "court", "section", "act", "license", "licence", "plaintiff", "defendant",
        "contract", "property", "appeal", "witness", "अधिनियम", "धारा", "न्यायालय",
        "सुखाचार", "सुखाधिकार", "अधिकारिता",
    )
    hits = sum(marker in text for marker in legal_markers)
    title_signal = any(marker in text[:300] for marker in ("act", "law", "legal", "court", "अधिनियम"))
    return "legal" if hits >= 2 or title_signal else "general"


def _translation_terms(source: str, target: str) -> list[tuple[str, str]]:
    if source == "en" and target == "hi":
        pairs = [(term, details[0]) for term, details in LEGAL_GLOSSARY.items()]
        pairs.append(("license", LEGAL_GLOSSARY["licence"][0]))
        pairs.append(("easements", LEGAL_GLOSSARY["easement"][0]))
        return pairs
    if source == "hi" and target == "en":
        pairs = [(details[0], term) for term, details in LEGAL_GLOSSARY.items()]
        pairs.extend((alias, canonical) for alias, canonical in LEGAL_ALIASES.items() if re.search(r"[\u0900-\u097F]", alias))
        return pairs
    return []


def _protect_legal_terms(text: str, source: str, target: str, domain: str) -> tuple[str, dict[str, str]]:
    if domain != "legal":
        return text, {}
    protected = text
    replacements: dict[str, str] = {}
    for index, (term, translated_term) in enumerate(sorted(_translation_terms(source, target), key=lambda item: -len(item[0]))):
        token = f"PDFLEGALTERM{index}X"
        if source == "en":
            pattern = rf"\b{re.escape(term)}\b"
        else:
            pattern = re.escape(term)
        if re.search(pattern, protected, flags=re.IGNORECASE):
            protected = re.sub(pattern, token, protected, flags=re.IGNORECASE)
            replacements[token] = translated_term
    return protected, replacements


def _restore_legal_terms(text: str, replacements: dict[str, str]) -> str:
    for token, term in replacements.items():
        # Public engines normally preserve an all-caps token. This tolerant
        # pattern also restores variants where a provider inserts spaces.
        flexible_token = r"\s*".join(map(re.escape, token))
        text = re.sub(flexible_token, term, text, flags=re.IGNORECASE)
    return text


class LibreTranslateService:
    """Translation with language detection, chunking, and a short-lived cache."""

    def __init__(self, url: str | None, key: str | None):
        # A configured self-hosted LibreTranslate service is preferred. Without
        # one, Google's endpoint is usually quicker than a public Libre instance.
        self.url = url.rstrip("/") if url else None
        self.key = key
        self._cache: TimedLruCache[tuple[str, str, str, str], str] = TimedLruCache(256, ttl_seconds=60 * 60)

    @staticmethod
    def _chunks(text: str, maximum_length: int = 900) -> list[str]:
        """Split large drafts without changing their original whitespace.

        The free translation endpoints are GET based and reject long encoded
        Unicode query strings. Keeping an individual request below 900 source
        characters lets Hindi-to-English translations complete reliably.
        """
        if len(text) <= maximum_length:
            return [text]
        chunks: list[str] = []
        remaining = text
        while len(remaining) > maximum_length:
            cut = max(remaining.rfind("\n", 0, maximum_length), remaining.rfind(" ", 0, maximum_length))
            if cut <= 0:
                cut = maximum_length
            else:
                cut += 1
            chunks.append(remaining[:cut])
            remaining = remaining[cut:]
        if remaining:
            chunks.append(remaining)
        return chunks

    async def _translate_libre(self, client: httpx.AsyncClient, text: str, source: str, target: str) -> str | None:
        if not self.url:
            return None
        payload = {"q": text, "source": source, "target": target, "format": "text"}
        if self.key:
            payload["api_key"] = self.key
        try:
            response = await client.post(f"{self.url}/translate", json=payload, timeout=4.0)
            if response.status_code == 200:
                translated = response.json().get("translatedText")
                if translated:
                    return str(translated)
        except Exception:
            pass
        return None

    @staticmethod
    async def _translate_google(client: httpx.AsyncClient, text: str, source: str, target: str) -> str | None:
        try:
            response = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text},
                timeout=4.0,
            )
            if response.status_code == 200:
                data = response.json()
                translated_chunks = [
                    segment[0]
                    for segment in data[0]
                    if isinstance(segment, list) and segment and segment[0]
                ]
                if translated_chunks:
                    translated = "".join(translated_chunks)
                    # The endpoint occasionally prepends a separator even when
                    # the source starts with a word. Preserve intentional draft
                    # whitespace while removing that provider-only artifact.
                    if text and not text[0].isspace():
                        translated = translated.lstrip()
                    if text and not text[-1].isspace():
                        translated = translated.rstrip()
                    return translated
        except Exception:
            pass
        return None

    @staticmethod
    async def _translate_mymemory(client: httpx.AsyncClient, text: str, source: str, target: str) -> str | None:
        # MyMemory provides a useful no-key fallback, but only accepts small
        # requests. Split here rather than abandoning the fallback for a whole
        # draft when Google is temporarily unavailable.
        if len(text) > 450:
            translated_chunks: list[str] = []
            for chunk in LibreTranslateService._chunks(text, 450):
                translated = await LibreTranslateService._translate_mymemory(client, chunk, source, target)
                if not translated:
                    return None
                translated_chunks.append(translated)
            return "".join(translated_chunks)
        try:
            response = await client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": text, "langpair": f"{source}|{target}"},
                timeout=4.0,
            )
            if response.status_code == 200:
                translated = response.json().get("responseData", {}).get("translatedText")
                if translated and not str(translated).startswith("MYMEMORY WARNING"):
                    return str(translated)
        except Exception:
            pass
        return None

    async def _translate_chunk(self, client: httpx.AsyncClient, text: str, source: str, target: str) -> str:
        for provider in (self._translate_libre, self._translate_google, self._translate_mymemory):
            result = await provider(client, text, source, target)
            if result:
                return result
        raise HTTPException(503, "Translation is temporarily unavailable. Please try again in a moment.")

    async def translate(self, text: str, source: str, target: str, document_title: str = "") -> str:
        if not text or not text.strip():
            return ""
        resolved_source = _detect_language(text, source)
        if resolved_source == target:
            return text
        domain = _document_domain(document_title, text[:4000])
        cache_key = (resolved_source, target, domain, text)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        protected_text, legal_replacements = _protect_legal_terms(text, resolved_source, target, domain)
        chunks = self._chunks(protected_text)
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "PDFReaderApp/1.0"}) as client:
            translated: list[str] = []
            # A few small concurrent requests are both quicker and more
            # reliable than a single oversized encoded URL.
            for index in range(0, len(chunks), 3):
                translated.extend(await asyncio.gather(*(
                    self._translate_chunk(client, chunk, resolved_source, target)
                    for chunk in chunks[index:index + 3]
                )))
        result = _restore_legal_terms("".join(translated), legal_replacements)
        self._cache.set(cache_key, result)
        # A common reader workflow is English -> Hindi -> English. The second
        # operation can return the exact original draft immediately, including
        # when a public provider is temporarily unavailable.
        self._cache.set((target, resolved_source, domain, result), text)
        return result


class DictionaryService:
    def __init__(self, translator: LibreTranslateService):
        self.translator = translator
        # The document id scopes learned results to the active PDF. This gives
        # repeated searches an instant, consistent answer without carrying
        # terminology from a law PDF into a different subject later.
        self._cache: TimedLruCache[tuple[str, str, str, str], list[str]] = TimedLruCache(
            1024, ttl_seconds=24 * 60 * 60,
        )

    @staticmethod
    def _sanitize_term(raw_term: str) -> str:
        # Keep Unicode letters and multi-word Hindi queries intact. PDFs and
        # copied browser text can add invisible marks or non-breaking spaces;
        # normalize them before both lookup and cache-key construction.
        normalized = unicodedata.normalize("NFC", raw_term)
        normalized = re.sub(r"[\u200B-\u200D\uFEFF]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip(" \t\r\n.,;:!?()[]{}\"'“”‘’")

    @staticmethod
    def _passage_helpers(term: str, language: str) -> list[str]:
        """Give immediate, useful help for a selected sentence or paragraph.

        Dictionary APIs only define a word or short phrase. A long selection
        often contains legal vocabulary, so surface those terms locally before
        attempting remote dictionary lookups. This works without an API key and
        remains useful when the network is unavailable.
        """
        if language != "en" or len(re.findall(r"[A-Za-z]+", term)) < 5:
            return []
        explanations = (
            ("repeal", "to formally revoke or annul a law or legal provision"),
            ("reproduc", "to repeat, restate, or enact the wording again"),
            ("administ", "to apply, manage, or enforce a law or system"),
            ("court", "a judicial body that interprets and applies the law"),
            ("section", "a numbered part of an Act or other legal document"),
            ("act", "a statute: a law passed by a legislature"),
            ("law", "rules that a government or court recognizes and enforces"),
        )
        found: list[str] = []
        seen: set[str] = set()
        for raw_word in re.findall(r"[A-Za-z]+", term):
            word = raw_word.casefold()
            if word in seen:
                continue
            for stem, explanation in explanations:
                if word.startswith(stem):
                    found.append(f"{raw_word} — {explanation.capitalize()}.")
                    seen.add(word)
                    break
            if len(found) == 4:
                break
        return found

    @staticmethod
    def _candidate_terms(term: str, language: str) -> list[str]:
        """Choose a few meaningful words from a long selection for lookup."""
        if language == "hi":
            tokens = re.findall(r"[\u0900-\u097F]+", term)
        else:
            tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", term)
        if len(tokens) <= 4:
            return [term]

        ignored = {
            "the", "and", "for", "that", "with", "from", "which", "this", "these", "those", "have",
            "has", "been", "were", "are", "was", "will", "would", "shall", "should", "into", "over",
            "under", "when", "where", "they", "their", "them", "than", "then", "namel",
        }
        scored: list[tuple[int, int, str]] = []
        for index, raw_word in enumerate(tokens):
            word = raw_word.casefold()
            if len(word) < 4 or word in ignored:
                continue
            score = len(word)
            if word.endswith(("ing", "ed", "tion", "ment", "ity", "ence", "ance")):
                score += 8
            if word.startswith(("repeal", "administ", "reproduc", "jurisdict", "statut")):
                score += 12
            scored.append((score, index, raw_word))

        candidates: list[str] = []
        seen: set[str] = set()
        for _, _, word in sorted(scored, key=lambda item: (-item[0], item[1])):
            if word.casefold() not in seen:
                candidates.append(word)
                seen.add(word.casefold())
            if len(candidates) == 3:
                break
        return candidates or [term]

    @staticmethod
    def _definitions_from_wiktionary(data: dict, language: str, allow_language_fallback: bool = True) -> list[str]:
        entries = data.get(language, [])
        if not entries and allow_language_fallback:
            entries = data.get("en", []) or data.get("hi", [])
        definitions = []
        for entry in entries:
            for definition in entry.get("definitions", []):
                html = definition.get("definition", "")
                cleaned = re.sub(r"<[^>]+>", "", html).strip()
                if cleaned:
                    definitions.append(cleaned)
        return definitions[:6]

    async def _english_definitions(self, client: httpx.AsyncClient, term: str) -> list[str]:
        try:
            response = await client.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(term)}", timeout=4.0
            )
            if response.status_code == 200:
                definitions = [
                    definition["definition"].strip()
                    for entry in response.json()
                    if isinstance(entry, dict)
                    for meaning in entry.get("meanings", [])
                    for definition in meaning.get("definitions", [])
                    if definition.get("definition", "").strip()
                ]
                if definitions:
                    return definitions[:6]
        except Exception:
            pass

        try:
            response = await client.get(
                "https://api.datamuse.com/words", params={"sp": term, "md": "d", "max": "5"}, timeout=4.0
            )
            if response.status_code == 200:
                for item in response.json():
                    if item.get("word", "").casefold() == term.casefold() and item.get("defs"):
                        return [value.split("\t", 1)[-1].capitalize() for value in item["defs"][:6]]
        except Exception:
            pass
        return []

    async def _wiktionary_definitions(
        self, client: httpx.AsyncClient, term: str, language: str, allow_language_fallback: bool = True,
    ) -> list[str]:
        hosts = ("hi.wiktionary.org", "en.wiktionary.org") if language == "hi" else ("en.wiktionary.org",)
        for host in hosts:
            try:
                response = await client.get(
                    f"https://{host}/api/rest_v1/page/definition/{urllib.parse.quote(term)}", timeout=4.0
                )
                if response.status_code == 200:
                    payload = response.json()
                    definitions = self._definitions_from_wiktionary(payload, language, allow_language_fallback)
                    if definitions:
                        return definitions
            except Exception:
                pass
        return []

    async def _translate_or_empty(self, text: str, source: str, target: str, document_title: str = "") -> str:
        """Keep a useful lookup result visible if the optional translation fails."""
        try:
            return await self.translator.translate(text, source, target, document_title)
        except TypeError:
            # Keeps lightweight test/dummy translators compatible with the
            # optional document-title argument used by the real service.
            return await self.translator.translate(text, source, target)
        except HTTPException:
            return ""

    async def _bilingual_definitions(
        self, definitions: list[str], source_language: str = "en", document_title: str = "",
    ) -> list[str]:
        """Return each available meaning in English and Hindi for the reader UI."""
        definitions = [definition.strip() for definition in definitions if definition.strip()][:6]
        if not definitions:
            return []

        # Translate the batch in one request. Newlines are retained by the
        # supported providers, letting the results stay in the same order while
        # keeping lookup fast for passages with several useful legal terms.
        target_language = "hi" if source_language == "en" else "en"
        translated = await self._translate_or_empty(
            "\n".join(definitions), source_language, target_language, document_title,
        )
        translated_definitions = [line.strip() for line in translated.splitlines() if line.strip()]

        if source_language == "en":
            english_definitions, hindi_definitions = definitions, translated_definitions
        else:
            english_definitions, hindi_definitions = translated_definitions, definitions

        results = [f"English: {definition}" for definition in english_definitions]
        if source_language == "hi" and not results:
            results.append("English: Translation is temporarily unavailable.")
        if len(hindi_definitions) == len(definitions):
            results.extend(f"Hindi: {definition}" for definition in hindi_definitions)
        elif translated.strip():
            label = "Hindi" if source_language == "en" else "English"
            results.append(f"{label}: {translated.strip()}")
        else:
            missing_label = "Hindi" if source_language == "en" else "English"
            results.append(f"{missing_label}: Translation is temporarily unavailable.")
        return results

    @staticmethod
    def _legal_results(term: str, domain: str) -> list[str]:
        exact_match = _legal_entry(term)
        if exact_match:
            key, (hindi_term, english_meaning, hindi_meaning) = exact_match
            return [
                f"English: {key} — {english_meaning}",
                f"Hindi: {hindi_term} — {hindi_meaning}",
            ]
        if domain != "legal":
            return []

        matches: list[tuple[int, str, tuple[str, str, str]]] = []
        lowered = term.casefold()
        for key, entry in LEGAL_GLOSSARY.items():
            position = lowered.find(key)
            if position >= 0:
                matches.append((position, key, entry))
        matches.sort(key=lambda item: item[0])
        unique_matches: list[tuple[str, tuple[str, str, str]]] = []
        seen: set[str] = set()
        for _, key, entry in matches:
            if key not in seen:
                unique_matches.append((key, entry))
                seen.add(key)
            if len(unique_matches) == 3:
                break
        english_results = [f"English: {key} — {entry[1]}" for key, entry in unique_matches]
        hindi_results = [f"Hindi: {entry[0]} — {entry[2]}" for _, entry in unique_matches]
        return english_results + hindi_results

    async def define(
        self, raw_term: str, language: str, context: str = "", document_title: str = "", document_id: str = "",
    ) -> list[str]:
        term = self._sanitize_term(raw_term)
        if not term:
            return []
        resolved_language = _detect_language(term, language)
        domain = _document_domain(document_title, context, term)
        scope = document_id.strip() or domain
        cache_key = (scope, domain, resolved_language, term.casefold())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        legal_results = self._legal_results(term, domain)
        if legal_results:
            self._cache.set(cache_key, legal_results)
            return list(legal_results)

        if resolved_language == "hi":
            async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "PDFReaderApp/1.0"}) as client:
                hindi_definitions = await self._wiktionary_definitions(
                    client, term, "hi", allow_language_fallback=False,
                )
            if hindi_definitions:
                results = await self._bilingual_definitions(hindi_definitions, "hi", document_title)
                self._cache.set(cache_key, results)
                return list(results)

        english_term = term if resolved_language == "en" else await self._translate_or_empty(
            term, "hi", "en", document_title,
        )
        definitions = self._passage_helpers(english_term, "en") if english_term else []

        if not definitions and english_term:
            candidates = self._candidate_terms(english_term, "en")
            async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "PDFReaderApp/1.0"}) as client:
                for candidate in candidates:
                    candidate_definitions = await self._english_definitions(client, candidate)
                    if not candidate_definitions:
                        candidate_definitions = await self._wiktionary_definitions(client, candidate, "en")
                    if candidate_definitions:
                        prefix = f"{candidate} — " if candidate.casefold() != english_term.casefold() else ""
                        definitions.extend(f"{prefix}{definition}" for definition in candidate_definitions[:2])
                    if len(definitions) >= 6:
                        break

        if definitions:
            results = await self._bilingual_definitions(definitions, "en", document_title)
        else:
            hindi_term = term if resolved_language == "hi" else await self._translate_or_empty(
                term, "en", "hi", document_title,
            )
            results = [
                f"English: {english_term or 'Translation is temporarily unavailable.'}",
                f"Hindi: {hindi_term or 'Translation is temporarily unavailable.'}",
            ]

        self._cache.set(cache_key, results)
        return list(results)
