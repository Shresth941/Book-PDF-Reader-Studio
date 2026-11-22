# Dependency and model record

Updated 2026-09-20 for the Windows x64 / CPython 3.12.14 development environment. `backend/requirements-local.lock.txt` pins the core Python environment; `backend/requirements-translation.txt` adds the optional translation runtime with wheel hashes. Setup installs the core first, then the optional runtime when requested. `frontend/package-lock.json` records npm dependencies. Re-run relevant qualification checks after changing dependencies or model files.

## Installed runtime

| Component | Installed version | Installed package metadata license |
|---|---|---|
| PyMuPDF | 1.25.1 | GNU AFFERO GPL 3.0 |
| fpdf2 | 2.8.8 | LGPL-3.0-only |
| uharfbuzz | 0.56.1 | Apache License 2.0 |
| pytesseract | 0.3.13 | Apache License 2.0 |
| python-docx | 1.2.0 | MIT |
| Pillow | 11.3.0 | MIT-CMU |
| psutil | 7.0.0 | BSD-3-Clause |
| ReportLab | 4.4.9 | BSD; see its installed license.txt |
| CTranslate2 | 4.8.2 | MIT |
| SentencePiece | 0.2.2 | Apache-2.0 |
| NumPy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| PyYAML | 6.0.3 | MIT |
| sacremoses | 0.2.0 | MIT |

The translation requirements also pin regex, joblib, cloudpickle, tqdm, click and colorama. This inventory is not redistribution approval. A packaged release needs complete dependency notices and licensing review. Windows Nirmala is loaded from the user's installed fonts, temporarily extracted for shaping, and not checked into the project. No Windows font installer is bundled.

## Installed public local draft models

Both native archives below are converted locally with CTranslate2 `OpusMTConverter`, INT8 quantization, into `backend/models/marian/en-hi` and `backend/models/marian/hi-en`. Each converted installation is approximately 85 MB. The two original downloads total approximately 564 MB; conversion and cache require additional disk space. Installation is explicit; inference does not download models or send document text online.

| Direction | Native model and revision | Archive bytes | License |
|---|---|---:|---|
| English → Hindi | Helsinki-NLP/Tatoeba-MT-eng-hin, `opus+bt-2021-04-10` | 282,894,140 | CC-BY-4.0 |
| Hindi → English | Helsinki-NLP/OPUS-MT-hi-en, `opus-2019-12-18` | 281,129,780 | CC-BY-4.0 |

These are the native OPUS/Tatoeba archives, **not** similarly named Hugging Face checkpoints. Do not substitute a Hugging Face model card's Apache-2.0 license for the native archives' CC-BY-4.0 attribution requirement. Installed directories retain `LICENSE`, `ATTRIBUTION.txt`, model provenance, exact runtime versions, conversion settings and every installed file's SHA-256 in `manifest.json`.

| Archive | Locally computed SHA-256 |
|---|---|
| [English → Hindi native archive](https://object.pouta.csc.fi/Tatoeba-MT-models/eng-hin/opus+bt-2021-04-10.zip) | `48801b2a15e2ab9985e78154a1544a757895dbc339806cdccf06c1faf4e35a47` |
| [Hindi → English native archive](https://object.pouta.csc.fi/OPUS-MT-models/hi-en/opus-2019-12-18.zip) | `24228662b66469e64abdeb5c66e584c715d77655cb72c85275f9d83aa081b0a2` |

These hashes were computed from HTTPS downloads checked against pinned sizes and MD5-style ETags; they are not upstream SHA-256 signatures. Provenance: [pinned English–Hindi listing](https://github.com/Helsinki-NLP/Tatoeba-Challenge/blob/d34a89ac102fd236503a1911dd1050564bf4e682/models/eng-hin/README.md), [pinned Hindi–English listing](https://github.com/Helsinki-NLP/OPUS-MT-train/blob/d5e932c96d77596378010aa7e7f1caeb8ccaa9f8/models/hi-en/README.md), and [pinned converter source](https://github.com/OpenNMT/CTranslate2/blob/d44d2d069eb88c7b7804da864c10c201501cb4a9/python/ctranslate2/converters/opus_mt.py).

The installer bounds archive entries and expanded size, rejects unsafe ZIP names, retains source scripts as evidence without executing them, and publishes an installation only after an actual offline runtime smoke test. A repeat installation reuses an exact hash-verified installation without network access or inference; changed or damaged directories are left untouched with an actionable error. Inference checks recorded dependency versions and model file inventory/hashes. Preprocessing is the recorded `safe-native-punctuation-v1` implementation; it intentionally fixes a fullwidth digit mapping in the upstream script and rejects changes to protected numeric tokens.

Runtime limits are 256 source tokens per deliberately split unit, 512 generated tokens, beam size 2 and two CPU threads. Generation must include an actual end token; unknown tokens, unsupported control characters or incomplete output fail explicitly. Source character offsets are retained across splitting. The manifest labels every output `draft_only`, `human_review_required` and `bilingual_quality_qualified=false`.

Four real runtime-smoke examples per direction passed. This establishes execution, not translation quality. A Hindi legal example mistranslated the actor and currency, so competent bilingual review remains essential; see [implementation and validation](IMPLEMENTATION-VALIDATION.md).

## OCR and optional upgrades

Hindi OCR data is `tesseract-ocr/tessdata_fast` at revision `87416418657359cb625c412a48b6e1d6d41c29bd`; downloaded `hin.traineddata` SHA-256 is `4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825`. This computer's English and OSD files were copied from its installed Tesseract data; their exact hashes are retained in the ignored local manifest. They are not claimed to originate from that commit. New setup downloads all three from the pinned upstream revision. Install the Tesseract executable separately.

The IndicTrans2 reference adapter remains available as an optional, unqualified upgrade: `ai4bharat/indictrans2-en-indic-dist-200M` and `ai4bharat/indictrans2-indic-en-dist-200M`. Neither snapshot nor its inference stack is installed here. Authorized upstream access, applicable license acceptance, exact model/code/runtime revisions and a reviewed feasibility report are required before activation. It is preferred by the dispatcher only when its qualification and runtime prerequisites are available; otherwise the installed public Marian baseline is selected. Inference errors never trigger online fallback.

Docling and PaddleOCR are not integrated or qualified. Current native ruled-table proposals and AcroForm appearance evidence use pinned PyMuPDF; do not describe them as Docling output. PDF.js 6.3.289 with Node 24 is a separate development verification tool, not a shipped Python runtime dependency.

Primary references: [CTranslate2 Marian support](https://opennmt.net/CTranslate2/guides/marian.html), [IndicTrans2 reference inference](https://github.com/AI4Bharat/IndicTrans2/tree/main/huggingface_interface), [Tesseract image quality](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html), [fpdf2 shaping](https://py-pdf.github.io/fpdf2/TextShaping.html), [PDF replacement text specification](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/pdfreference1.5_v6.pdf).