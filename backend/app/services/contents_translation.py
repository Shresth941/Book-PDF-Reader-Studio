"""Preserve contents entries and references instead of translating a list as prose."""
import re
from app.services.legal_translation import normalized, exact_legal_text

# Exact heading equivalents only, never replacements keyed solely by article number.
HEADINGS = {
    "अनुच्छेद": "Article", "पृष्ठ": "Page", "विषय-सूची": "Contents",
    "उच्चतम न्यायालय से परामर्श करने की राष्ट्रपति की शक्ति": "Power of the President to consult the Supreme Court",
    "सिविल और न्यायिक प्राधिकारियों द्वारा उच्चतम न्यायालय की सहायता में कार्य किया जाना": "Civil and judicial authorities to act in aid of the Supreme Court",
    "[निरसित।]": "[Omitted.]", "न्यायालय के नियम आदि": "Rules of Court, etc.",
    "उच्चतम न्यायालय के अधिकारी और सेवक तथा व्यय": "Officers and servants and the expenses of the Supreme Court",
    "निर्वचन": "Interpretation", "भारत का नियंत्रक-महालेखापरीक्षक": "Comptroller and Auditor-General of India",
    "नियंत्रक-महालेखापरीक्षक के कर्तव्य और शक्तियां": "Duties and powers of the Comptroller and Auditor-General",
    "संघ के और राज्यों के लेखाओं का प्ररूप": "Form of accounts of the Union and of the States",
    "संपरीक्षा प्रतिवेदन": "Audit reports", "राज्य": "The States", "साधारण": "General",
    "परिभाषा": "Definition", "कार्यपालिका": "The Executive", "राज्यपाल": "The Governor",
    "राज्यों के राज्यपाल": "Governors of States", "राज्य की कार्यपालिका शक्ति": "Executive power of State",
    "राज्यपाल की नियुक्ति": "Appointment of Governor", "राज्यपाल की पदावधि": "Term of office of Governor",
    "राज्यपाल नियुक्त होने के लिए अर्हताएं": "Qualifications for appointment as Governor",
    "राज्यपाल के पद के लिए शर्तें": "Conditions of Governor’s office",
    "राज्यपाल द्वारा शपथ या प्रतिज्ञान": "Oath or affirmation by the Governor",
    "कुछ आकस्मिकताओं में राज्यपाल के कृत्यों का निर्वहन": "Discharge of the functions of the Governor in certain contingencies",
    "क्षमा आदि की और कुछ मामलों में दंडादेश के निलंबन, परिहार या लघुकरण की राज्यपाल की शक्ति": "Power of Governor to grant pardons, etc., and to suspend, remit or commute sentences in certain cases",
    "राज्य की कार्यपालिका शक्ति का विस्तार": "Extent of executive power of State",
    "मंत्रि-परिषद्": "Council of Ministers",
    "राज्यपाल को सहायता और सलाह देने के लिए मंत्रि-परिषद्": "Council of Ministers to aid and advise Governor",
    "मंत्रियों के बारे में अन्य उपबंध": "Other provisions as to Ministers",
}
LEADER = re.compile(r"\.{3,}|…{2,}")
ENTRY = re.compile(r"^(\d+[A-Za-zक-ह]*\.)\s*(.*)$")
PAGE = re.compile(r"\d+(?:\s*[–-]\s*\d+)?")


def is_contents(regions):
    return (sum(len(LEADER.findall(r["source"])) for r in regions) >= 2
            and sum(bool(ENTRY.match(line.strip())) for r in regions for line in r["source"].splitlines()) >= 2)


def parse_rows(text):
    lines = text.splitlines()
    rows, i = [], 0
    while i < len(lines):
        value = lines[i].strip()
        if not value:
            i += 1
            continue
        start = i
        entry = ENTRY.match(value)
        if entry:
            label, title = entry.groups()
            # Wrapped titles end at a leader; a following number is its page.
            while not LEADER.search(title) and i + 1 < len(lines):
                if ENTRY.match(lines[i + 1].strip()) or PAGE.fullmatch(lines[i + 1].strip()):
                    return None
                i += 1
                title += " " + lines[i].strip()
            parts = LEADER.split(title, maxsplit=1)
            if len(parts) != 2:
                return None
            title, number = parts[0].strip(), parts[1].strip()
            if not number and i + 1 < len(lines) and PAGE.fullmatch(lines[i + 1].strip()):
                i += 1
                number = lines[i].strip()
            if not PAGE.fullmatch(number):
                return None
            rows.append({"kind": "entry", "label": label, "source": title, "page": number, "line": start})
        else:
            rows.append({"kind": "folio" if re.fullmatch(r"\(?[ivxlcdm]+\)?", value, re.I) else "heading", "source": value, "label": "", "page": "", "line": start})
        i += 1
    return rows


def exact_title(text, target, entries, legal):
    exact = exact_legal_text(text, target, entries)
    if exact is not None:
        return exact
    if re.fullmatch(r"\(?[ivxlcdm]+\)?", text, re.I):
        return text
    if not legal:
        return None
    mapping = HEADINGS if target == "en" else {v: k for k, v in HEADINGS.items()}
    key = normalized(text)
    if key in mapping:
        return mapping[key]
    if key.startswith("(") and key.endswith(")") and key[1:-1] in mapping:
        return "(" + mapping[key[1:-1]] + ")"
    pattern = r"(भाग|अध्याय)\s+(\d+)\s*(?:[—–-]\s*(.*))?" if target == "en" else r"(Part|Chapter)\s+(\d+)\s*(?:[—–-]\s*(.*))?"
    m = re.fullmatch(pattern, key)
    if m:
        kind, number, title = m.groups()
        heading = {"भाग": "Part", "अध्याय": "Chapter", "Part": "भाग", "Chapter": "अध्याय"}[kind]
        translated = exact_title(title, target, entries, legal) if title else ""
        if translated is not None:
            return f"{heading} {number}" + (" — " + translated if title else "")
    return None


def translate_contents(models_dir, regions, target, entries, legal, translate, edition=False):
    plans, pending = {}, []
    right = max((r.get("text_layout") or {}).get("bbox", r["bbox"])[2] for r in regions)
    for r in regions:
        rows = parse_rows(r["source"])
        if rows is None:
            # Never quietly flatten malformed contents into prose.
            from app.core.review import ReviewError
            raise ReviewError("contents_structure", "A contents entry has no clear title/page boundary. Correct its extracted text before translating.")
        for index, row in enumerate(rows):
            row["id"] = f"{r['id']}:contents:{index}"
            row["text"] = exact_title(row["source"], target, entries, legal)
            if edition and row["kind"] == "entry":
                from app.services.constitution_reference import contents_title
                reference = contents_title(row["label"], row["source"])
                if reference:
                    article, item = reference
                    row.update(label=article + ".", text=item["en"], reference_page=item["en_page"])
            if row["text"] is None:
                pending.append({"id": row["id"], "source": row["source"], "source_language": r["source_language"]})
        plans[r["id"]] = rows
    translated = {r["id"]: r for r in translate(models_dir, pending, target)["regions"]} if pending else {}
    result = []
    for r in regions:
        rows = plans[r["id"]]
        for row in rows:
            if row["text"] is None:
                row["text"] = translated[row["id"]]["translation"]
            # Article suffixes are part of identifiers, not words for the model.
            if target == "en":
                row["label"] = row["label"].translate(str.maketrans({"क": "A", "ख": "B", "ग": "C"}))
        value = "\n".join((f"{row['label']} {row['text']} .... {row['page']}" if row["kind"] == "entry" else row["text"]) for row in rows)
        bbox = list((r.get("text_layout") or {}).get("bbox", r["bbox"]))
        bbox[2] = right
        result.append({"id": r["id"], "translation": value, "translation_approved": False,
            "draft_only": True, "quality_qualified": False, "model": "contents-entry-translation", "model_revision": "1",
            "translation_layout": {"source": r["source"], "text": value, "bbox": bbox, "rows": rows},
            "alignment": [], "contents_models": list({item.get("model", "local") for item in translated.values()})})
    return {"regions": result}
