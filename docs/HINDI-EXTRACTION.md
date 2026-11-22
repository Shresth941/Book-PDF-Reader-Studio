# Hindi extraction architecture and acceptance scenarios

## Problem and baseline
The supplied 544-page PDF embeds XpressChanak legacy Hindi fonts. Its ToUnicode map describes Latin glyph names rather than the visible Hindi. Treating every paragraph as an image adds OCR processes and corrupts words and numbers. Existing tests only checked Devanagari presence, which is insufficient.

## Intended pipeline
Original bytes + fingerprint -> native spans (font, text, geometry) -> route per span:
- valid Unicode: retain exactly;
- supported XpressChanak: recover its byte encoding, map glyphs, reorder pre-base i and reph, validate all symbols;
- unsupported or incomplete mapping: preserve raw evidence and offer OCR for that region;
- image: OCR, preserving overlap warnings.
Assemble spans in original lines and regions. Store raw text, decoding provenance, and editable Unicode separately. Never automatically approve text or overwrite saved corrections. Recognition refresh remains revision-checked and audited. Translation consumes reviewed Unicode, not raw legacy symbols.

No font-name guessing across unrelated Chanakya variants. No transliteration, generative correction, article-number inference, or document-specific replacements. Unknown symbols must be visible as a failure, never silently dropped.

## Acceptance matrix (written before implementation)
| Scenario | Expected check |
| --- | --- |
| Cover | Exact भारत / का / संविधान, no OCR |
| Body article 16 | Exact article number, अवसर की समता, मूलवंश, नियुक्ति |
| Body article 124 | Exact article number and उच्चतम न्यायालय |
| Matras / conjuncts | ि before ligature, reph, anusvara, nukta, independent vowels |
| Numeric content | Preserve printed digits, years, references; no OCR digit guesses |
| Mixed fonts | Decode only legacy spans; English punctuation/digits untouched |
| Unicode Hindi and English | Preserve native extraction; no OCR |
| Unknown legacy symbol/font | OCR fallback, retain raw text, no false success |
| Scans / blank / forms / tables | Existing handling and review safeguards retained |
| Existing imports | Refresh suggestions; retain corrections, reject stale revisions |
| Full supplied document | Audit all 544 pages for decoding coverage and unknown glyphs |
| Performance | Measure actual pages and full-document span decoding; zero OCR calls for supported text |

Ground truth must be checked against rendered original pages. Unit fixtures must include exact words and digits, not just Hindi character counts. Full-document coverage does not establish human-verified semantic accuracy on every page.

## Page presentation
Native extraction additionally records display geometry derived from span baselines rather than unreliable font ascenders. The simple reader scales one page-sized editing surface to the viewport; region IDs stay separate internally for safe editing and review, while visible card borders and numbering are removed. Original and extracted text share zoom. Existing imports without display metadata use a conservative bounding-box approximation; Hindi recognition refresh adds the new metadata without replacing saved corrections.

Text positioning is approximate when the original font is unavailable. Overflow from longer translations is marked and scrollable while editing, rather than discarded. Tables/forms still require their existing detailed review workflow.
