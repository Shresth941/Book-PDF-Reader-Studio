# Offline legal translation and layout

Choose **Legal terminology** under Translation style. Law/Constitution filenames suggest this style; the selection becomes the document domain when translation starts. Check the source against the original before translating. Existing approved translations are not replaced by translation jobs.

## Architecture and scope

The reader sends document domain and target language to the review API. The translation job passes that context and the document glossary to the isolated local worker. The local translator first handles exact known headings, then sends remaining passages to the installed offline model. Source-bound corrections replace only known erroneous equivalents when the corresponding Hindi term is present. Document glossary definitions take precedence. Review checks flag missing glossary terms even across line breaks.

For example, `भारत का संविधान\n(भाग 3—मूल अधिकार)` becomes `Constitution of India\n(Part 3 — Fundamental Rights)`. Constitutional heading terminology follows the [Legislative Department bilingual Constitution resources](https://www.legislative.gov.in/constitution-of-india/) and the [India Code Constitution](https://www.indiacode.nic.in/bitstream/123456789/19150/1/constitution_of_india.pdf). The small glossary also contains general Indian legal vocabulary that must be checked in context. It is not an exhaustive legal dictionary.

There is no fuzzy replacement of statutory clauses, substitution of newer statutory text, or automatic rewriting of may/shall, exceptions or negation. The installed Marian model is a general translation model: this change improves terminology and exact headings, but cannot guarantee court-quality prose or legally equivalent full passages. Every generated result remains an unapproved draft; terminology assistance does not confer model quality qualification.

## Optional online review

Offline remains the default. The reader links to the existing detailed glossary/online review screen. Online translation requires a configured provider, successful existing provider-policy checks, and explicit approval of the exact prepared payload before sending text. A failed connection does not silently switch providers or send documents elsewhere. No document text was sent to an online translator during these checks.

## Layout

Contents pages are detected from repeated numbered entries and dotted leaders. Each title is translated independently; article identifiers and page references stay outside the model. Wrapped titles are joined within their entry, not with adjacent entries. Exact constitutional heading equivalents apply only to matching titles; article numbers alone never select a translation. Ambiguous entries stop translation for correction instead of silently dropping content.

The reader displays contents rows with separate article, title and right-aligned page columns, plus centered section headings. Editing the source or translation disables the generated preview rather than showing stale text. Exports retain each entry in saved text; the structured on-screen preview does not promise identical PDF pagination.

Hindi retains original line positions, centered headings and dominant italics/bold, including headings containing plain punctuation. Word spacing does not stretch centered headings or separate Devanagari letters. Saved older pages can refresh recognition metadata without replacing corrected source text.

English translations keep source margins, alignment and typography. Their passages grow vertically at the reading font size; subsequent passages in overlapping columns move down to avoid overlap. Longer translations can make the displayed sheet taller than the original. Identical pagination is therefore not promised. Existing export layouts and their review gates remain separate from the editable reader display.

## Validation (2026-11-25)

- Backend suite with installed local OCR/translation models and supplied Hindi PDF: **486 passed, 1 skipped**.
- Contents-page follow-up: **491 passed, 1 skipped**. Real page 16 preserves all 23 entries (143–164, including 144A) and their original page references. The browser regression verifies columns, wrapping, no overlap/clipping, 100/150% zoom, editing and mobile. Exact headings are processed without loading the model or contacting a service.
- Legal unit regressions: exact bilingual headings, reverse glossary uniqueness, document overrides, source-bound corrections, unchanged general mode, missing terminology checks and punctuation-safe italic detection.
- `frontend/tests/legal-browser.mjs`: offline legal selection/heading, unapproved output, very long translation without clipping, following passage moved below it, 100/150% zoom, mobile and no external browser requests.
- `frontend/tests/reader-browser.mjs`: 20 existing upload/edit/recovery/translation/export/review/error/mobile/OCR checks passed.
- `frontend/tests/hindi-browser.mjs`: supplied PDF cover and pages 41/101, Hindi geometry, dates/articles, edit fallback, zoom and mobile passed. Page extraction measured approximately 1.9 seconds each on this machine.
- TypeScript and production build passed. Private source PDFs and generated QA outputs are not committed.

Browser scripts require an isolated local QA server (default port 8002). Set `HINDI_ACCEPTANCE_PDF` to the local acceptance PDF for the Hindi script. Run scripts from `frontend`; never point destructive fixture workflows at the main port 8000 database.

## Published English for the 9 November 2015 Constitution

The supplied 544-page Hindi book identifies its edition on PDF page 3. The corresponding 494-page English edition has the same date, publisher and amendment cutoff. The reader includes that publicly issued English PDF, with its printed wording, footnotes, contents and layout. Select **Published English** to read it offline or download the complete edition. The link uses article starts from the two contents lists because the editions have different pagination. Continuations can open at the beginning of the provision. Unindexed schedules and appendices open at the English contents.

For editable translations, the worker checks the imported Hindi file's SHA-256. On a matching edition, 458 indexed headings require both the article identifier and the source title to match. For 32 reviewed passages or headings from the Preamble and selected early articles, the Hindi page and the whole normalized passage must match. These pairs include Article 16(3), which spans two English pages. Exact matches take the wording of this edition, retaining amendment markers. Entries keep the page references printed in the Hindi book. Other editable passages use the existing offline draft translator. Review approvals remain separate from reference matches.

The English publication is *The Constitution of India (As on 9th November, 2015)*, Government of India, Ministry of Law and Justice (Legislative Department), reproduced by [WIPO Lex, IN104](https://www.wipo.int/wipolex/en/legislation/details/15811). Bundled PDF SHA-256 is `5bb100f186eda2e9366f87f74c1b2b1507cf245355ad8886848f9f0c7f221966`. The companion indexes record PDF page numbers in both editions. The [Indian Kanoon Article 143 entry](https://indiankanoon.org/doc/121923369/) provides a secondary comparison and interleaves editorial notes with the text. The bundled government edition governs exact wording here.

**Compare a passage in Google Translate** previews and copies one selected passage. A locally configured Cloud Translation Basic key also enables an in-app comparison after the user approves the displayed public passage. The external link contains only language and interface parameters. Pasting is a separate user action. No account, API key, billing or background submission is needed for this workflow. [Google Cloud Translation](https://cloud.google.com/translate) is a separate metered service.

Validation covered all 23 contents entries on Hindi page 16, opening the matching English contents page, copying Article 16(1) into an editable draft, saving it, and a narrow-screen layout. The Google comparison link includes no passage text.

### Google translation checks

The Chrome page translator was exercised through upload, source review, translation and a fresh read of the saved draft. These outputs came from the installed English/Hindi Chrome model. Wording can change with model updates.

| Direction | Source passage | Saved draft |
| --- | --- | --- |
| English → Hindi | The applicant must file the appeal within 30 days. | आवेदक को 30 दिनों के भीतर अपील दायर करनी होगी। |
| Hindi → English | आवेदक को 30 दिनों के भीतर अपील दायर करनी होगी। | The applicant must file an appeal within 30 days. |
| English → Hindi | The court will not hear case 42 on Monday. | कोर्ट सोमवार को केस 42 पर सुनवाई नहीं करेगा। |
| Hindi → English | न्यायालय सोमवार को मामले की सुनवाई करेगा। | The court will hear the matter on Monday. |

The samples check the actor, obligation, time limit, negation and case number where present. Google used the conversational terms “कोर्ट” and “केस” in its general translation. For the exact legal wording of the supported Constitution edition, select **Published English**. Google drafts still go through the existing legal and number checks.

An isolated browser run can be repeated with `node tests/google-translation-browser.mjs` from `frontend` against a QA server on port 8002. The Chrome profile stores its language files under ignored `tmp/`. Cloud Basic adapter tests use simulated Google responses for both directions, quota, timeout, access denial, unavailable credentials and invalid output. Live Cloud Basic requires a configured key.
