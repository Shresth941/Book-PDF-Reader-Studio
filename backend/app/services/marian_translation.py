"""Offline CPU draft translation with public native OPUS-MT checkpoints.

Installation verifies runtime execution, not bilingual or legal accuracy. No
document text leaves this process; outputs always require ordinary human review.
"""
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import time
import unicodedata

from app.core.review import ReviewError, numeric_tokens

MODELS = {"en-hi": ("Helsinki-NLP/Tatoeba-MT-eng-hin", "opus+bt-2021-04-10"),
          "hi-en": ("Helsinki-NLP/OPUS-MT-hi-en", "opus-2019-12-18")}
RUNTIME = {"ctranslate2": "4.8.2", "sentencepiece": "0.2.2", "numpy": "2.5.3",
           "PyYAML": "6.0.3", "sacremoses": "0.2.0"}


def model_manifest(root, direction, verify=False):
    folder = Path(root).resolve() / "marian" / direction
    try:
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError("marian_missing", f"Install the public {direction} local draft model with tools/install_marian.py.", 503) from exc
    if (data.get("schema_version") != 1 or data.get("runtime_versions") != RUNTIME
            or direction not in MODELS or (data.get("model_id"), data.get("revision")) != MODELS[direction]
            or data.get("adapter") != "marian-ct2" or data.get("direction") != direction
            or data.get("runtime_smoke_tested") is not True or data.get("draft_only") is not True
            or data.get("human_review_required") is not True or not data.get("files")):
        raise ReviewError("marian_unverified", "The local draft model has no valid runtime verification record.", 503)
    for name, version in data.get("runtime_versions", {}).items():
        try:
            if importlib.metadata.version(name) != version:
                raise ReviewError("marian_runtime_changed", "The translation runtime changed. Re-run its local verification before use.", 503)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ReviewError("marian_runtime_missing", "Install requirements-translation.txt to use the local draft model.", 503) from exc
    if verify:
        for name, digest in data["files"].items():
            path = (folder / name).resolve()
            if not path.is_relative_to(folder) or not path.is_file():
                raise ReviewError("model_integrity", "A local model file is missing or outside its directory.", 503)
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ReviewError("model_integrity", "A local model file changed. Reinstall or reverify it before use.", 503)
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p.name != "manifest.json"}
        if actual != set(data["files"]):
            raise ReviewError("model_integrity", "Unrecorded local model files were found.", 503)
    return folder, data


def capabilities(root):
    result = {}
    for direction in MODELS:
        try:
            _, manifest = model_manifest(root, direction)
            if not all(importlib.util.find_spec(n) for n in ("ctranslate2", "sentencepiece", "sacremoses")):
                raise ReviewError("marian_runtime_missing", "Install the optional local translation runtime.", 503)
            result[direction] = {"available": True, "reason": "Local draft model: review every translation. Bilingual/legal quality is not certified.",
                "provider": "marian-ct2", "model": manifest["model_id"], "revision": manifest["revision"], "draft_only": True, "quality_qualified": False}
        except ReviewError as exc:
            result[direction] = {"available": False, "reason": exc.message, "provider": "marian-ct2"}
    return result


def normalize_source(text, source):
    # Native archives remove control characters. Reject unsupported controls
    # visibly instead of silently erasing source content. Preserve Hindi joiners.
    if any(unicodedata.category(c).startswith("C") and c not in "\r\n\t\u200c\u200d" for c in text):
        raise ReviewError("translation_controls", "The source contains unsupported control characters. Review its transcription before translation.")
    if source == "en":
        # The native 2021 script maps fullwidth ONE to a quote by mistake.
        # Preserve digits deliberately; record this reviewed normalization variant.
        replacements = {"，": ",", "。": ". ", "、": ",", "”": '"', "“": '"', "∶": ":", "：": ":",
                        "？": "?", "《": '"', "》": '"', "）": ")", "！": "!", "（": "(", "；": ";",
                        "」": '"', "「": '"', "．": ". ", "～": "~", "’": "'", "…": "...", "━": "-",
                        "〈": "<", "〉": ">", "【": "[", "】": "]", "％": "%"}
        replacements.update({chr(0xFF10 + i): str(i) for i in range(10)})
        normalized = text.translate(str.maketrans(replacements))
    else:
        from sacremoses import MosesPunctNormalizer
        normalized = MosesPunctNormalizer(lang="hi", pre_replace_unicode_punct=True,
            post_remove_control_chars=False, perl_parity=True).normalize(text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if numeric_tokens(text) != numeric_tokens(normalized):
        raise ReviewError("normalization_numbers", "Model preprocessing would change a number or reference. Correct or translate this passage manually.")
    return normalized


class DraftTranslator:
    def __init__(self, folder, manifest):
        import ctranslate2
        import sentencepiece as spm
        self.manifest = manifest
        self.source = manifest["direction"].split("-")[0]
        self.input_limit = min(256, int(manifest.get("input_tokens", 256)))
        self.output_limit = min(512, int(manifest.get("output_tokens", 512)))
        self.source_tokens = spm.SentencePieceProcessor(model_file=str(Path(folder) / "source.spm"))
        self.target_tokens = spm.SentencePieceProcessor(model_file=str(Path(folder) / "target.spm"))
        self.engine = ctranslate2.Translator(str(folder), device="cpu", compute_type="int8", inter_threads=1, intra_threads=2)

    def tokenize(self, text):
        value = normalize_source(text, self.source)
        tokens = self.source_tokens.encode(value, out_type=str)
        ids = self.source_tokens.encode(value, out_type=int)
        if self.source_tokens.unk_id() in ids:
            raise ReviewError("translation_unknown_source", "The local model cannot recognize part of this source. Split or translate that span manually.")
        return value, tokens

    def translate(self, text):
        from app.services.local_translation import split_exact
        # Tokenizer normalization is independently recorded; offsets always
        # reference the immutable, unnormalized source transcription.
        def fits(value):
            return len(self.tokenize(value)[1]) <= self.input_limit
        translated, alignment = [], []
        for start, end, chunk in split_exact(text, fits):
            normalized, tokens = self.tokenize(chunk)
            if not tokens:
                alignment.append({"source_start": start, "source_end": end, "target_index": None, "normalized_source": normalized})
                continue
            if len(tokens) > self.input_limit:
                raise ReviewError("token_limit", "The input exceeds its verified local-model limit.")
            result = self.engine.translate_batch([tokens], beam_size=2, max_input_length=0,
                max_decoding_length=self.output_limit, return_end_token=True)[0]
            output = result.hypotheses[0] if result.hypotheses else []
            if not output or output[-1] != "</s>":
                raise ReviewError("translation_truncated", "The local model did not finish this passage. No truncated result was saved.")
            if "<unk>" in output:
                raise ReviewError("translation_unknown_output", "The local model produced an unknown word. Review and translate the passage manually.")
            value = self.target_tokens.decode(output[:-1]).strip()
            if not value:
                raise ReviewError("empty_translation", "The local model produced no translation for a nonempty source.")
            alignment.append({"source_start": start, "source_end": end, "target_index": len(translated),
                              "normalized_source": normalized, "normalization_changed": normalized != chunk})
            translated.append(value)
        return " ".join(translated), alignment


def translate_segments(models_dir, regions, target):
    if not regions:
        return {"regions": []}
    sources = {r["source_language"] for r in regions}
    if len(sources) != 1 or sources - {"en", "hi"} or target not in {"en", "hi"}:
        raise ReviewError("language_required", "Confirm one Hindi or English source language per translation unit.")
    source = next(iter(sources))
    if source == target:
        raise ReviewError("same_language", "Review this passage as already in the target language.")
    folder, manifest = model_manifest(models_dir, f"{source}-{target}", verify=True)
    translator = DraftTranslator(folder, manifest)
    results = []
    for region in regions:
        value, alignment = translator.translate(region["source"])
        results.append({"id": region["id"], "translation": value, "alignment": alignment,
            "model": manifest["model_id"], "model_revision": manifest["revision"],
            "translation_approved": False, "draft_only": True, "quality_qualified": False})
    return {"regions": results}


def runtime_smoke_test(folder, manifest):
    import psutil
    source = manifest["direction"].split("-")[0]
    examples = (["This is a book.", "The debtor shall pay 100 rupees.", "Do not sign unless the amount is correct.", "Name and address"]
                if source == "en" else ["यह एक पुस्तक है।", "ऋणी को 100 रुपये देने होंगे।", "राशि सही नहीं है।", "नाम और पता"])
    start = time.perf_counter()
    translator = DraftTranslator(folder, manifest)
    samples = []
    for text in examples:
        translated, alignment = translator.translate(text)
        samples.append({"source": text, "translation": translated, "alignment": alignment})
    process = psutil.Process()
    return {"passed": True, "scope": "runtime smoke only; bilingual quality not assessed", "samples": samples,
            "elapsed_seconds": round(time.perf_counter() - start, 3), "rss_mb_at_end": round(process.memory_info().rss / 1024**2, 2)}
