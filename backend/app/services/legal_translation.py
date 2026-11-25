"""Source-bound legal terminology assistance; never a claim of legal qualification.

Constitution terminology: Legislative Department / India Code official bilingual
texts. See docs/LEGAL-TRANSLATION.md for scope and provenance.
"""
import re
import unicodedata

TERMS = [
    ("भारत का संविधान", "Constitution of India", ["India's Constitution", "Indian Constitution"]),
    ("मूल अधिकार", "Fundamental Rights", ["Root Authority", "Root Rights", "Basic Rights", "Original Rights"]),
    ("मौलिक अधिकार", "Fundamental Rights", ["Basic Rights"]),
    ("राज्य की नीति के निदेशक तत्व", "Directive Principles of State Policy", []),
    ("मूल कर्तव्य", "Fundamental Duties", ["Basic Duties"]),
    ("उच्चतम न्यायालय", "Supreme Court", ["Highest Court"]),
    ("उच्च न्यायालय", "High Court", []),
    ("मुख्य न्यायमूर्ति", "Chief Justice", ["Chief Judge"]),
    ("न्यायपालिका", "Judiciary", []),
    ("अधिकारिता", "Jurisdiction", []),
    ("प्राकृतिक न्याय", "Natural Justice", []),
    ("विधि का शासन", "Rule of Law", []),
    ("सुनवाई का अवसर", "Opportunity of being heard", []),
    ("लोक नियोजन के विषय में अवसर की समता", "Equality of opportunity in matters of public employment", []),
    ("विधि के समक्ष समता", "Equality before law", []),
]


def is_legal(domain):
    return bool(re.search(r"legal|law|court|constitution|विधि|विधिक|कानून", domain or "", re.I))


def normalized(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def legal_glossary(target):
    direction = "hi-en" if target == "en" else "en-hi"
    entries = [{"id": f"legal-{i}-{direction}", "source": hi if target == "en" else en,
             "target": en if target == "en" else hi, "direction": direction,
             "sense": "Indian legal terminology; verify in context", "scope": "domain",
             "reference": "https://www.legislative.gov.in/constitution-of-india/"}
            for i, (hi, en, _) in enumerate(TERMS)]
    unique = {}
    for entry in entries:
        unique.setdefault(entry["source"].casefold(), entry)
    return list(unique.values())


def exact_legal_text(text, target, entries):
    """Translate recognised standalone headings only; no fuzzy statute matching."""
    mapping = {normalized(e["source"]).casefold(): e["target"] for e in reversed(entries)}
    def line(value):
        clean = normalized(value)
        if not clean:
            return ""
        if clean.casefold() in mapping:
            return mapping[clean.casefold()]
        if re.fullmatch(r"[\d\s.()\[\]—–*-]+", clean):
            return clean
        pattern = r"(\()?\s*भाग\s+([\dIVXLCDM]+)\s*[—–-]\s*(.*?)\s*(\))?" if target == "en" else r"(\()?\s*Part\s+([\dIVXLCDM]+)\s*[—–-]\s*(.*?)\s*(\))?"
        match = re.fullmatch(pattern, clean, re.I)
        if match:
            opening, number, title, closing = match.groups()
            translated = mapping.get(title.casefold())
            if translated:
                return f"{opening or ''}{'Part' if target == 'en' else 'भाग'} {number} — {translated}{closing or ''}"
        return None
    output = [line(value) for value in text.split("\n")]
    if any(value is None for value in output):
        # A cover title is often broken across lines rather than paragraphs.
        return mapping.get(normalized(text).casefold())
    return "\n".join(output)


def correct_known_terms(source, translated, target, entries):
    """Only replace a known mistranslation when its complete source term occurs."""
    if target != "en":
        return translated, []
    changes = []
    for hi, en, aliases in TERMS:
        if normalized(hi) not in normalized(source):
            continue
        preferred = next((e["target"] for e in entries if normalized(e["source"]) == hi), en)
        for alias in aliases:
            translated, count = re.subn(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", lambda _: preferred, translated, flags=re.I)
            if count:
                changes.append({"source_term": hi, "model_term": alias, "preferred_term": preferred})
    return translated, changes
