"""Explicit, anonymous installer for two public OPUS models; never run by a job.

Run with the optional requirements-translation.txt runtime. Downloads fixed public
URLs only, checks size/ETag, records SHA256, checks ZIP paths, converts native Marian
weights to INT8, and publishes only after a real runtime smoke test. This is not a
bilingual quality certification. No document content is sent over the network.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

CATALOG = {
    "en-hi": {
        "model_id": "Helsinki-NLP/Tatoeba-MT-eng-hin",
        "revision": "opus+bt-2021-04-10",
        "url": "https://object.pouta.csc.fi/Tatoeba-MT-models/eng-hin/opus+bt-2021-04-10.zip",
        "bytes": 282894140,
        "etag": "6aeb08332f8be8135ce9e8cd0fb4f3eb",
        "model": "opus+bt.spm32k-spm32k.transformer-align.model1.npz.best-perplexity.npz",
        "vocab": "opus+bt.spm32k-spm32k.vocab.yml",
        "source": "https://github.com/Helsinki-NLP/Tatoeba-Challenge/blob/d34a89ac102fd236503a1911dd1050564bf4e682/models/eng-hin/README.md",
    },
    "hi-en": {
        "model_id": "Helsinki-NLP/OPUS-MT-hi-en",
        "revision": "opus-2019-12-18",
        "url": "https://object.pouta.csc.fi/OPUS-MT-models/hi-en/opus-2019-12-18.zip",
        "bytes": 281129780,
        "etag": "44d48e8eb54e7de737118c879fe8d8e6",
        "model": "opus.spm32k-spm32k.transformer-align.model1.npz.best-perplexity.npz",
        "vocab": "opus.spm32k-spm32k.vocab.yml",
        "source": "https://github.com/Helsinki-NLP/OPUS-MT-train/blob/d5e932c96d77596378010aa7e7f1caeb8ccaa9f8/models/hi-en/README.md",
    },
}
RUNTIME = {"ctranslate2": "4.8.2", "sentencepiece": "0.2.2", "numpy": "2.5.3",
           "PyYAML": "6.0.3", "sacremoses": "0.2.0"}
CONVERTER_SOURCE = "https://github.com/OpenNMT/CTranslate2/blob/d44d2d069eb88c7b7804da864c10c201501cb4a9/python/ctranslate2/converters/opus_mt.py"
MAX_UNCOMPRESSED = 400 * 1024 * 1024


def sha256_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("The pinned model URL redirected; review the upstream change manually.")


def download_archive(spec, cache_dir):
    """Cache only complete downloads; ETag is an upstream identifier, not SHA256."""
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = spec["model_id"].replace("/", "--") + "--" + spec["revision"]
    archive, record = cache_dir / (name + ".zip"), cache_dir / (name + ".json")
    if archive.is_file() and record.is_file():
        data = json.loads(record.read_text(encoding="utf-8"))
        if (data.get("url") == spec["url"] and data.get("etag") == spec["etag"]
                and archive.stat().st_size == spec["bytes"] and data.get("bytes") == spec["bytes"]
                and data.get("sha256") == sha256_file(archive)):
            return archive, data
        raise ValueError("Cached archive integrity failed. Move the damaged cache aside before retrying.")
    if archive.exists() or record.exists():
        raise ValueError("Incomplete cache exists. Move it aside before retrying.")
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "PDF-Reader-explicit-model-installer/1", "Accept-Encoding": "identity"})
    opener = urllib.request.build_opener(NoRedirect())
    partial = archive.with_suffix(".part")
    # Exclusive creation prevents concurrent installers from replacing each other.
    digest, etag_digest, total = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
    created_partial = False
    try:
        output = partial.open("xb")
        created_partial = True
        with output, opener.open(request, timeout=60) as response:
            etag = response.headers.get("ETag", "").strip('"')
            if etag != spec["etag"] or int(response.headers.get("Content-Length", -1)) != spec["bytes"]:
                raise ValueError("Upstream archive size/ETag changed; the model pin must be reviewed.")
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > spec["bytes"]:
                    raise ValueError("Archive exceeded its pinned download size.")
                digest.update(chunk)
                etag_digest.update(chunk)
                output.write(chunk)
            if total != spec["bytes"] or etag_digest.hexdigest() != spec["etag"]:
                raise ValueError("Archive was incomplete or its bytes did not match the pinned ETag.")
        data = {"url": spec["url"], "bytes": total, "etag": etag, "sha256": digest.hexdigest(),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "sha256_note": "Computed locally from HTTPS download checked against pinned size and MD5-style ETag; not an upstream SHA256 signature."}
        partial.rename(archive)
        record.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return archive, data
    except Exception:
        # Only our explicitly named partial file is removed; no recursive deletes.
        if created_partial and partial.is_file():
            partial.unlink()
        raise


def safe_extract_archive(archive, destination):
    """Validate the entire central directory before writing any archive member."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if not infos or len(infos) > 256 or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
            raise ValueError("Archive has an unexpected file count or expanded size.")
        seen, checked = set(), []
        for info in infos:
            # ZipInfo may sanitize backslashes/NUL on Windows before exposing
            # filename. Validate the original central-directory name first.
            name = info.orig_filename
            parts = PurePosixPath(name).parts
            mode = info.external_attr >> 16
            if (not parts or "\\" in name or ":" in name or "\x00" in name or name.startswith("/")
                    or any(p in {".", ".."} or p.rstrip(" .") != p or p.split(".")[0].upper() in reserved for p in parts)
                    or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}) or info.flag_bits & 1):
                raise ValueError("Unsafe ZIP member: " + repr(name))
            target = (destination / Path(*parts)).resolve()
            if not target.is_relative_to(destination) or target == destination:
                raise ValueError("Escaping ZIP member.")
            key = target.relative_to(destination).as_posix().casefold()
            if key in seen:
                raise ValueError("Duplicate or escaping ZIP member.")
            seen.add(key)
            checked.append((info, target))
        for info, target in checked:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)


def verify_runtime():
    installed = {}
    for name, expected in RUNTIME.items():
        installed[name] = importlib.metadata.version(name)
        if installed[name] != expected:
            raise ValueError(f"Expected {name}=={expected}; found {installed[name]}. Use requirements-translation.txt.")
    return installed


def verify_existing_install(direction, models_dir):
    """Reuse only a complete, unchanged installation of this exact recipe.

    This verifies recorded smoke evidence and hashes without loading a model or
    accessing the network. It does not re-certify translation quality.
    """
    from app.core.review import ReviewError
    from app.services.marian_translation import model_manifest

    destination = Path(models_dir).resolve() / "marian" / direction
    spec = CATALOG[direction]
    try:
        folder, manifest = model_manifest(models_dir, direction, verify=True)
        expected = {
            "license": "CC-BY-4.0", "upstream_source": spec["source"],
            "conversion": {"quantization": "int8", "converter": "OpusMTConverter", "source": CONVERTER_SOURCE},
            "requirements_sha256": sha256_file(BACKEND / "requirements-translation.txt"),
            "input_tokens": 256, "output_tokens": 512, "beam_size": 2, "cpu_threads": 2,
            "normalization": "safe-native-punctuation-v1", "bilingual_quality_qualified": False,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise ValueError("The installation recipe or its provenance changed.")
        archive = manifest.get("archive", {})
        if any(archive.get(key) != spec[key] for key in ("url", "bytes", "etag")):
            raise ValueError("The archive record does not match the pinned source.")
        digest = archive.get("sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("The original archive SHA256 record is missing or malformed.")
        if manifest.get("runtime_smoke", {}).get("passed") is not True:
            raise ValueError("The recorded runtime smoke test did not pass.")
        required = {"model.bin", "config.json", "shared_vocabulary.json", "source.spm", "target.spm", "LICENSE", "ATTRIBUTION.txt"}
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p != folder / "manifest.json"}
        if not required.issubset(manifest["files"]) or actual != set(manifest["files"]):
            raise ValueError("The installation file inventory is incomplete or changed.")
    except (ReviewError, OSError, ValueError, TypeError, AttributeError) as exc:
        reason = exc.message if isinstance(exc, ReviewError) else str(exc)
        raise ValueError(
            f"Existing installation at {destination} was not reused: {reason} "
            "No files were changed. Move that directory aside explicitly before reinstalling; "
            "the installer never overwrites an existing model."
        ) from exc
    print(f"Verified and reused installed {direction} model; no download or conversion needed.", flush=True)
    return {"direction": direction, "path": str(folder), "manifest": manifest, "reused": True}


def install_direction(direction, models_dir, cache_dir):
    versions = verify_runtime()
    spec = CATALOG[direction]
    destination = Path(models_dir).resolve() / "marian" / direction
    if destination.exists() or destination.is_symlink():
        return verify_existing_install(direction, models_dir)

    from app.services.marian_translation import runtime_smoke_test
    import yaml
    from ctranslate2.converters import OpusMTConverter

    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading/verifying public {direction} archive...", flush=True)
    archive, provenance = download_archive(spec, cache_dir)
    with tempfile.TemporaryDirectory(prefix=".install-" + direction + "-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        raw, converted = stage / "native", stage / "converted"
        safe_extract_archive(archive, raw)
        decoder = yaml.safe_load((raw / "decoder.yml").read_text(encoding="utf-8"))
        if decoder.get("models") != [spec["model"]] or decoder.get("vocabs") != [spec["vocab"], spec["vocab"]]:
            raise ValueError("Unexpected model/vocabulary paths in the native decoder configuration.")
        license_file = raw / "LICENSE"
        if not license_file.is_file() or "Attribution 4.0 International" not in license_file.read_text(encoding="utf-8"):
            raise ValueError("The expected model CC BY 4.0 license is missing.")
        print(f"Converting {direction} to CTranslate2 INT8 (CPU)...", flush=True)
        started = time.perf_counter()
        converter = OpusMTConverter(str(raw))
        converter.convert(str(converted), quantization="int8")
        del converter
        gc.collect()
        for filename in ("source.spm", "target.spm", "LICENSE", "README.md", "preprocess.sh", "postprocess.sh"):
            path = raw / filename
            if path.is_file():
                shutil.copyfile(path, converted / filename)
        attribution = (f"Model: {spec['model_id']}\nNative revision: {spec['revision']}\n"
                       f"Creators: Helsinki-NLP / OPUS-MT / Tatoeba translation model contributors.\n"
                       f"Original source and contributors: {spec['source']}\nArchive: {spec['url']}\n"
                       "Model license: Creative Commons Attribution 4.0 International (CC BY 4.0).\n"
                       "License: https://creativecommons.org/licenses/by/4.0/ (complete LICENSE included).\n"
                       "Changes: converted from native Marian to CTranslate2 and quantized to INT8.\n"
                       "No endorsement by original creators is implied. See original README and license for attribution.\n"
                       "Use: draft translations only; every translation requires bilingual human review.\n")
        (converted / "ATTRIBUTION.txt").write_text(attribution, encoding="utf-8")
        manifest = {"schema_version": 1, "adapter": "marian-ct2", "direction": direction,
                    "model_id": spec["model_id"], "revision": spec["revision"],
                    "archive": provenance, "license": "CC-BY-4.0", "upstream_source": spec["source"],
                    "conversion": {"quantization": "int8", "converter": "OpusMTConverter", "source": CONVERTER_SOURCE},
                    "runtime_versions": versions,
                    "dependency_provenance": {n: f"https://pypi.org/pypi/{n}/{v}/json" for n, v in versions.items()},
                    "requirements_sha256": sha256_file(BACKEND / "requirements-translation.txt"),
                    "input_tokens": 256, "output_tokens": 512, "beam_size": 2,
                    "cpu_threads": 2, "normalization": "safe-native-punctuation-v1",
                    "draft_only": True, "human_review_required": True,
                    "bilingual_quality_qualified": False, "runtime_smoke_tested": False}
        print(f"Smoke testing {direction}; this does not qualify translation quality...", flush=True)
        smoke = runtime_smoke_test(converted, manifest)
        manifest["runtime_smoke_tested"] = True
        manifest["runtime_smoke"] = smoke
        manifest["installed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["conversion_and_smoke_seconds"] = round(time.perf_counter() - started, 3)
        manifest["files"] = {p.relative_to(converted).as_posix(): sha256_file(p)
                             for p in sorted(converted.rglob("*")) if p.is_file()}
        (converted / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        converted.rename(destination)
    return {"direction": direction, "path": str(destination), "manifest": manifest, "reused": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=BACKEND / "models")
    parser.add_argument("--direction", choices=["all", *CATALOG], default="all")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--download-only", action="store_true", help="Only cache and verify the pinned public archives.")
    parser.add_argument("--report", type=Path, help="Optional local JSON report; no document content is uploaded.")
    args = parser.parse_args()
    directions = list(CATALOG) if args.direction == "all" else [args.direction]
    cache_dir = args.cache_dir or args.models_dir.resolve() / ".downloads"
    result = []
    for direction in directions:
        if args.download_only:
            archive, metadata = download_archive(CATALOG[direction], cache_dir)
            result.append({"direction": direction, "path": str(archive), "archive": metadata})
        else:
            result.append(install_direction(direction, args.models_dir, cache_dir))
        print(f"Completed {direction}.", flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Public model setup complete. Outputs remain unapproved drafts requiring human review.", flush=True)


if __name__ == "__main__":
    main()
