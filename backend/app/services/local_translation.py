"""Offline IndicTrans2 reference adapter; no network or silent truncation."""
import hashlib
import importlib.util
import json
from pathlib import Path

from app.core.review import ReviewError

MODELS = {"en-hi": "ai4bharat/indictrans2-en-indic-dist-200M", "hi-en": "ai4bharat/indictrans2-indic-en-dist-200M"}


def model_manifest(root, direction, verify=False):
    folder = Path(root).resolve() / direction
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        raise ReviewError("model_missing", f"Install and qualify {MODELS[direction]} under models/{direction}. Manual translation remains available.", 503)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("model_id") != MODELS[direction] or not data.get("revision") or not data.get("qualified") or not data.get("files"):
        raise ReviewError("model_unqualified", "This local model has no qualified, versioned manifest. Complete the local pilot first.", 503)
    if verify:
        for relative, expected in data["files"].items():
            path = (folder / relative).resolve()
            if not path.is_relative_to(folder) or not path.is_file():
                raise ReviewError("model_integrity", "A model file is missing or outside the model directory.", 503)
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != expected:
                raise ReviewError("model_integrity", "Local model files changed. Requalify before use.", 503)
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
        if actual != set(data["files"]):
            raise ReviewError("model_integrity", "Model directory contains unrecorded files.", 503)
    return folder, data


def capabilities(root):
    result = {}
    dependencies = all(importlib.util.find_spec(n) for n in ("torch", "transformers", "IndicTransToolkit"))
    for direction in MODELS:
        try:
            _, manifest = model_manifest(root, direction)
            result[direction] = {"available": bool(dependencies), "reason": "" if dependencies else "Install the optional local translation runtime.", "revision": manifest["revision"]}
        except (ReviewError, ValueError) as exc:
            result[direction] = {"available": False, "reason": str(exc)}
    from app.services.marian_translation import capabilities as marian_capabilities
    baseline = marian_capabilities(root)
    for direction in MODELS:
        if not result[direction]["available"] and baseline[direction]["available"]:
            result[direction] = baseline[direction]
    return result


def split_exact(text, fits):
    """Preserve every source character and its offsets while bounding token count."""
    if fits(text):
        return [(0, len(text), text)]
    result, start = [], 0
    while start < len(text):
        low, high = start + 1, len(text)
        best = start
        while low <= high:
            mid = (low + high) // 2
            if fits(text[start:mid]):
                best, low = mid, mid + 1
            else:
                high = mid - 1
        if best == start:
            raise ReviewError("token_limit", "A source unit cannot fit this model. Split or transcribe it manually.")
        if best < len(text):
            boundary = max(text.rfind(c, start, best) for c in ("\n", "।", ". ", "; ", " "))
            if boundary > start:
                best = boundary + 1
        result.append((start, best, text[start:best]))
        start = best
    assert "".join(t for _, _, t in result) == text
    return result


def translate_segments(models_dir, regions, target):
    if not regions:
        return {"regions": []}
    sources = {r["source_language"] for r in regions}
    if len(sources) != 1 or sources - {"en", "hi"}:
        raise ReviewError("language_required", "Translate one confirmed source language per processing unit.")
    source = next(iter(sources))
    if source == target:
        raise ReviewError("same_language", "Mark this passage as already in the target language after review.")
    selected = capabilities(models_dir)[f"{source}-{target}"]
    if selected.get("provider") == "marian-ct2":
        from app.services.marian_translation import translate_segments as translate_draft
        return translate_draft(models_dir, regions, target)
    folder, manifest = model_manifest(models_dir, f"{source}-{target}", verify=True)
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        from IndicTransToolkit.processor import IndicProcessor
    except ImportError as exc:
        raise ReviewError("runtime_missing", "Install the optional IndicTrans2 runtime in this environment.", 503) from exc
    torch.set_num_threads(2)
    processor = IndicProcessor(inference=True)
    tokenizer = AutoTokenizer.from_pretrained(str(folder), local_files_only=True, trust_remote_code=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(str(folder), local_files_only=True, trust_remote_code=True,
                                                attn_implementation="eager", torch_dtype=torch.float32).to("cpu").eval()
    src, tgt = {"en": "eng_Latn", "hi": "hin_Deva"}[source], {"en": "eng_Latn", "hi": "hin_Deva"}[target]
    maximum = min(512, int(manifest.get("input_tokens", 512)))
    results = []
    def preprocess(text):
        return processor.preprocess_batch([text], src_lang=src, tgt_lang=tgt)
    def fits(text):
        return len(tokenizer(preprocess(text), truncation=False)["input_ids"][0]) <= maximum
    for region in regions:
        alignment, translated = [], []
        for start, end, text in split_exact(region["source"], fits):
            if not text.strip():
                translated.append(text)
                continue
            inputs = tokenizer(preprocess(text), truncation=False, return_tensors="pt", padding=True)
            if inputs["input_ids"].shape[-1] > maximum:
                raise ReviewError("token_limit", "Preprocessed input exceeds the qualified limit.")
            with torch.inference_mode():
                tokens = model.generate(**inputs, num_beams=1, max_new_tokens=512, use_cache=True)
            eos = model.generation_config.eos_token_id
            eos_ids = set(eos if isinstance(eos, list) else [eos])
            if int(tokens[0, -1]) not in eos_ids:
                raise ReviewError("translation_truncated", "Generation reached its limit. No truncated translation was saved.")
            value = processor.postprocess_batch(tokenizer.batch_decode(tokens, skip_special_tokens=True), lang=tgt)[0]
            if not value.strip():
                raise ReviewError("empty_translation", "The model returned no translation for a nonempty source.")
            alignment.append({"source_start": start, "source_end": end, "target_index": len(translated)})
            translated.append(value)
        results.append({"id": region["id"], "translation": " ".join(translated), "alignment": alignment,
                        "model": manifest["model_id"], "model_revision": manifest["revision"]})
    return {"regions": results}
