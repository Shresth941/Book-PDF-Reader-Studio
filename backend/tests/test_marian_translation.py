"""Small offline tests; never download weights or claim bilingual qualification."""
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import zipfile

import pytest

from app.core.review import ReviewError
from app.services import marian_translation as marian
from tools import install_marian as installer


@pytest.fixture
def recorded_model(tmp_path, monkeypatch):
    folder = tmp_path / "marian" / "en-hi"
    folder.mkdir(parents=True)
    (folder / "model.bin").write_bytes(b"small inert test fixture; not a model")
    manifest = {
        "schema_version": 1, "adapter": "marian-ct2", "direction": "en-hi",
        "model_id": marian.MODELS["en-hi"][0], "revision": marian.MODELS["en-hi"][1],
        "runtime_smoke_tested": True, "draft_only": True, "human_review_required": True,
        "bilingual_quality_qualified": False, "runtime_versions": dict(installer.RUNTIME),
        "files": {"model.bin": hashlib.sha256((folder / "model.bin").read_bytes()).hexdigest()},
    }
    def save():
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save()
    monkeypatch.setattr(marian.importlib.metadata, "version", lambda name: installer.RUNTIME[name])
    return tmp_path, folder, manifest, save


def test_missing_model_is_reviewable(tmp_path):
    with pytest.raises(ReviewError, match="Install") as error:
        marian.model_manifest(tmp_path, "en-hi", verify=True)
    assert error.value.status == 503


def test_model_verification_accepts_honest_draft_record(recorded_model):
    root, folder, manifest, _ = recorded_model
    found, data = marian.model_manifest(root, "en-hi", verify=True)
    assert found == folder
    assert data["bilingual_quality_qualified"] is False


@pytest.mark.parametrize("field,value", [
    ("runtime_smoke_tested", False), ("draft_only", False),
    ("human_review_required", False), ("revision", "unrecorded-new-model"),
    ("direction", "hi-en"), ("adapter", "other"), ("files", {}),
])
def test_manifest_rejects_unverified_or_mismatched_record(recorded_model, field, value):
    root, _, manifest, save = recorded_model
    manifest[field] = value
    save()
    with pytest.raises(ReviewError):
        marian.model_manifest(root, "en-hi", verify=True)


@pytest.mark.parametrize("runtime", [None, {}, {"ctranslate2": "4.8.2"}])
def test_manifest_requires_complete_pinned_runtime(recorded_model, runtime):
    root, _, manifest, save = recorded_model
    if runtime is None:
        manifest.pop("runtime_versions")
    else:
        manifest["runtime_versions"] = runtime
    save()
    with pytest.raises(ReviewError):
        marian.model_manifest(root, "en-hi", verify=True)


def test_changed_runtime_is_unavailable(recorded_model, monkeypatch):
    root, _, _, _ = recorded_model
    monkeypatch.setattr(marian.importlib.metadata, "version", lambda name: "999.0")
    with pytest.raises(ReviewError) as error:
        marian.model_manifest(root, "en-hi")
    assert error.value.code == "marian_runtime_changed"


def test_missing_runtime_is_reviewable(recorded_model, monkeypatch):
    root, _, _, _ = recorded_model
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(marian.importlib.metadata, "version", missing)
    with pytest.raises(ReviewError) as error:
        marian.model_manifest(root, "en-hi")
    assert error.value.code == "marian_runtime_missing"


@pytest.mark.parametrize("damage", ["changed", "missing", "unrecorded", "escaping"])
def test_model_file_integrity_and_containment(recorded_model, damage):
    root, folder, manifest, save = recorded_model
    if damage == "changed":
        (folder / "model.bin").write_bytes(b"different weights")
    elif damage == "missing":
        (folder / "model.bin").unlink()
    elif damage == "unrecorded":
        (folder / "extra.bin").write_bytes(b"unexpected")
    else:
        outside = root / "outside.bin"
        outside.write_bytes(b"outside")
        manifest["files"]["../../outside.bin"] = hashlib.sha256(b"outside").hexdigest()
        save()
    with pytest.raises(ReviewError) as error:
        marian.model_manifest(root, "en-hi", verify=True)
    assert error.value.code == "model_integrity"


def test_capabilities_keep_runtime_and_quality_distinct(recorded_model, monkeypatch):
    root, _, _, _ = recorded_model
    monkeypatch.setattr(marian.importlib.util, "find_spec", lambda name: object())
    available = marian.capabilities(root)
    assert available["en-hi"]["available"] is True
    assert available["en-hi"]["draft_only"] is True
    assert available["en-hi"]["quality_qualified"] is False
    assert available["hi-en"]["available"] is False
    monkeypatch.setattr(marian.importlib.util, "find_spec", lambda name: None)
    assert marian.capabilities(root)["en-hi"]["available"] is False


class FakePieces:
    def encode(self, text, out_type):
        if out_type is str:
            return list(text)
        return [0 if c == "\u2603" else 1 for c in text]

    def unk_id(self):
        return 0

    def decode(self, pieces):
        return "".join(pieces)


class FakeEngine:
    def __init__(self, output=None):
        self.output = ["draft", "</s>"] if output is None else output
        self.calls = []

    def translate_batch(self, tokens, **options):
        self.calls.append((tokens, options))
        return [SimpleNamespace(hypotheses=[self.output])]


def draft_translator(output=None, limit=256):
    translator = marian.DraftTranslator.__new__(marian.DraftTranslator)
    translator.manifest = {"direction": "en-hi"}
    translator.source = "en"
    translator.input_limit = limit
    translator.output_limit = 512
    translator.source_tokens = FakePieces()
    translator.target_tokens = FakePieces()
    translator.engine = FakeEngine(output)
    return translator


def test_constructor_uses_only_one_cpu_direction_and_two_threads(monkeypatch, tmp_path):
    engine_calls = []
    def engine(*args, **kwargs):
        engine_calls.append((args, kwargs))
        return FakeEngine()
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(Translator=engine))
    monkeypatch.setitem(sys.modules, "sentencepiece", SimpleNamespace(SentencePieceProcessor=lambda **kwargs: FakePieces()))
    translator = marian.DraftTranslator(tmp_path, {"direction": "en-hi", "input_tokens": 999, "output_tokens": 999})
    assert translator.input_limit == 256 and translator.output_limit == 512
    assert engine_calls == [((str(tmp_path),), {"device": "cpu", "compute_type": "int8", "inter_threads": 1, "intra_threads": 2})]


def test_chunking_preserves_every_original_character_and_offset():
    translator = draft_translator(limit=12)
    text = "  First clause.\nSecond clause; third clause.  "
    translated, alignment = translator.translate(text)
    assert translated
    assert alignment[0]["source_start"] == 0 and alignment[-1]["source_end"] == len(text)
    assert all(a["source_end"] == b["source_start"] for a, b in zip(alignment, alignment[1:]))
    assert "".join(text[a["source_start"]:a["source_end"]] for a in alignment) == text
    assert all(len(tokens[0]) <= 12 for tokens, _ in translator.engine.calls)
    assert all(options["max_input_length"] == 0 and options["return_end_token"] is True
               and options["max_decoding_length"] == 512 for _, options in translator.engine.calls)


def test_whitespace_retains_alignment_without_model_call():
    translator = draft_translator()
    value, alignment = translator.translate(" \n\t ")
    assert value == "" and not translator.engine.calls
    assert [(a["source_start"], a["source_end"], a["target_index"]) for a in alignment] == [(0, 4, None)]


def test_unknown_source_stops_before_inference():
    translator = draft_translator()
    with pytest.raises(ReviewError) as error:
        translator.translate("Unsupported snowman \u2603.")
    assert error.value.code == "translation_unknown_source"
    assert not translator.engine.calls


@pytest.mark.parametrize("tokens,code", [
    (["unfinished"], "translation_truncated"), ([], "translation_truncated"),
    (["<unk>", "</s>"], "translation_unknown_output"),
    (["</s>"], "empty_translation"),
])
def test_incomplete_unknown_or_empty_outputs_are_never_saved(tokens, code):
    translator = draft_translator(output=tokens)
    with pytest.raises(ReviewError) as error:
        translator.translate("Text")
    assert error.value.code == code


def test_regions_contract_never_approves_model_output(monkeypatch, tmp_path):
    translator = draft_translator()
    manifest = {"model_id": "test-only", "revision": "pinned-test"}
    verified = []
    def model(root, direction, verify=False):
        verified.append((direction, verify))
        return tmp_path, manifest
    monkeypatch.setattr(marian, "model_manifest", model)
    monkeypatch.setattr(marian, "DraftTranslator", lambda folder, data: translator)
    result = marian.translate_segments(tmp_path, [{"id": "region-1", "source": "Text", "source_language": "en"}], "hi")
    assert verified == [("en-hi", True)]
    region = result["regions"][0]
    assert region["id"] == "region-1" and region["translation"] == "draft"
    assert region["translation_approved"] is False and region["draft_only"] is True
    assert region["quality_qualified"] is False and region["model_revision"] == "pinned-test"


@pytest.mark.parametrize("sources,target,code", [
    (["en", "hi"], "hi", "language_required"), (["unknown"], "hi", "language_required"),
    (["en"], "fr", "language_required"), (["hi"], "hi", "same_language"),
])
def test_language_confirmation_before_loading_model(tmp_path, sources, target, code):
    regions = [{"source_language": source} for source in sources]
    with pytest.raises(ReviewError) as error:
        marian.translate_segments(tmp_path, regions, target)
    assert error.value.code == code


@pytest.mark.parametrize("control", ["\x00", "\x07", "\u202e", "\ud800"])
def test_controls_are_rejected_instead_of_erased(control):
    with pytest.raises(ReviewError) as error:
        marian.normalize_source("left" + control + "right", "en")
    assert error.value.code == "translation_controls"


def test_english_normalization_corrects_native_fullwidth_one_typo():
    # The pinned EN native shell script incorrectly maps U+FF11 to a quote.
    assert marian.normalize_source("Amount １２３; “do not sign”…", "en") == 'Amount 123; "do not sign"...'
    assert marian.normalize_source("A\r\n B\tC", "en") == "A B C"


def test_hindi_normalization_keeps_joiners_and_digits():
    pytest.importorskip("sacremoses")
    assert "\u200d" in marian.normalize_source("क्\u200dष राशि १२३", "hi")
    assert "123" in marian.normalize_source("राशि １２３", "hi")


def test_normalization_rejects_numeric_drift_in_underlying_normalizer(monkeypatch):
    # A changed third-party normalizer must never silently alter source numbers.
    normalizer = SimpleNamespace(normalize=lambda text: text.replace("123", "1203"))
    monkeypatch.setitem(sys.modules, "sacremoses", SimpleNamespace(MosesPunctNormalizer=lambda **kwargs: normalizer))
    with pytest.raises(ReviewError) as error:
        marian.normalize_source("राशि 123", "hi")
    assert error.value.code == "normalization_numbers"


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute", "C:/absolute", "folder\\escape", "x:stream", "NUL.txt", "folder/COM1", "trailing. "])
def test_zip_rejects_unsafe_paths_before_writing_valid_members(tmp_path, unsafe):
    archive = make_zip(tmp_path / "bad.zip", [("good.txt", b"good"), (unsafe, b"bad")])
    if "\\" in unsafe:
        # Windows zipfile normalizes separators on write; restore the malicious
        # filename in both local and central records without changing its length.
        archive.write_bytes(archive.read_bytes().replace(unsafe.replace("\\", "/").encode(), unsafe.encode()))
    target = tmp_path / "extracted"
    with pytest.raises(ValueError):
        installer.safe_extract_archive(archive, target)
    assert not (target / "good.txt").exists()


@pytest.mark.parametrize("alias", ["nested/./file", "nested//file", "NESTED/file"])
def test_zip_rejects_duplicate_canonical_paths_before_extraction(tmp_path, alias):
    archive = make_zip(tmp_path / "duplicate.zip", [("good.txt", b"good"), ("nested/file", b"one"), (alias, b"two")])
    target = tmp_path / "extracted"
    with pytest.raises(ValueError):
        installer.safe_extract_archive(archive, target)
    assert not (target / "good.txt").exists()


def test_zip_rejects_symlinks_and_expansion_limits(tmp_path, monkeypatch):
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = make_zip(tmp_path / "link.zip", [(link, "../outside")])
    with pytest.raises(ValueError):
        installer.safe_extract_archive(archive, tmp_path / "links")
    archive = make_zip(tmp_path / "large.zip", [("large", b"12345")])
    monkeypatch.setattr(installer, "MAX_UNCOMPRESSED", 4)
    with pytest.raises(ValueError):
        installer.safe_extract_archive(archive, tmp_path / "large")


def test_zip_preserves_safe_binary_members(tmp_path):
    archive = make_zip(tmp_path / "valid.zip", [("nested/model.bin", b"\x00\xffdata"), ("LICENSE", b"attribution")])
    installer.safe_extract_archive(archive, tmp_path / "out")
    assert (tmp_path / "out/nested/model.bin").read_bytes() == b"\x00\xffdata"


def test_zip_crc_failure_cannot_become_installed_model(tmp_path):
    archive = make_zip(tmp_path / "crc.zip", [("model.bin", b"ORIGINAL_PAYLOAD")])
    archive.write_bytes(archive.read_bytes().replace(b"ORIGINAL_PAYLOAD", b"CORRUPT_PAYLOAD!", 1))
    with pytest.raises(zipfile.BadZipFile):
        installer.safe_extract_archive(archive, tmp_path / "out")


def fake_download(monkeypatch, payload=b"public archive", wrong_etag=False):
    etag = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    spec = {"model_id": "public/test", "revision": "pinned", "url": "https://example.invalid/public.zip",
            "bytes": len(payload), "etag": etag}
    class Response(io.BytesIO):
        headers = {"ETag": '"' + ("wrong" if wrong_etag else etag) + '"', "Content-Length": str(len(payload))}
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        return Response(payload)
    monkeypatch.setattr(installer.urllib.request, "build_opener", lambda handler: SimpleNamespace(open=open_request))
    return spec, requests


def test_download_verifies_records_and_reuses_public_bytes(tmp_path, monkeypatch):
    payload = b"public archive"
    spec, requests = fake_download(monkeypatch, payload)
    archive, record = installer.download_archive(spec, tmp_path)
    assert archive.read_bytes() == payload
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert record["etag"] == spec["etag"] and record["bytes"] == len(payload)
    assert all(key.lower() != "authorization" for key in requests[0].headers)
    installer.download_archive(spec, tmp_path)
    assert len(requests) == 1
    archive.write_bytes(b"changed archive")
    with pytest.raises(ValueError, match="integrity"):
        installer.download_archive(spec, tmp_path)


def test_download_rejects_changed_upstream_before_publishing(tmp_path, monkeypatch):
    spec, _ = fake_download(monkeypatch, wrong_etag=True)
    with pytest.raises(ValueError, match="size/ETag"):
        installer.download_archive(spec, tmp_path)
    assert not list(tmp_path.glob("*.zip")) and not list(tmp_path.glob("*.json"))
    assert not list(tmp_path.glob("*.part"))


def test_second_installer_never_removes_first_installers_partial(tmp_path, monkeypatch):
    spec, requests = fake_download(monkeypatch)
    partial = tmp_path / "public--test--pinned.part"
    partial.write_bytes(b"another installer is downloading")
    with pytest.raises(FileExistsError):
        installer.download_archive(spec, tmp_path)
    assert partial.read_bytes() == b"another installer is downloading"
    assert not requests


def test_download_never_follows_redirect():
    with pytest.raises(ValueError, match="redirected"):
        installer.NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://elsewhere.invalid/model")


@pytest.fixture
def installed_recipe(recorded_model, monkeypatch):
    root, folder, manifest, save = recorded_model
    spec = installer.CATALOG["en-hi"]
    for name in ("config.json", "shared_vocabulary.json", "source.spm", "target.spm", "LICENSE", "ATTRIBUTION.txt"):
        (folder / name).write_bytes(b"inert test-only data: " + name.encode())
        manifest["files"][name] = installer.sha256_file(folder / name)
    manifest.update({
        "license": "CC-BY-4.0", "upstream_source": spec["source"],
        "conversion": {"quantization": "int8", "converter": "OpusMTConverter", "source": installer.CONVERTER_SOURCE},
        "requirements_sha256": installer.sha256_file(installer.BACKEND / "requirements-translation.txt"),
        "input_tokens": 256, "output_tokens": 512, "beam_size": 2, "cpu_threads": 2,
        "normalization": "safe-native-punctuation-v1", "runtime_smoke": {"passed": True},
        "archive": {**{key: spec[key] for key in ("url", "bytes", "etag")}, "sha256": "a" * 64},
    })
    save()
    def forbidden(*args, **kwargs):
        pytest.fail("An existing installation must never download, convert, or run inference.")
    monkeypatch.setattr(installer, "download_archive", forbidden)
    monkeypatch.setattr(marian, "runtime_smoke_test", forbidden)
    # Repeat setup must not even import the heavy converter.
    monkeypatch.setitem(sys.modules, "ctranslate2.converters", None)
    return root, folder, manifest, save


def directory_snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_repeat_install_verifies_and_reuses_without_touching_files(installed_recipe):
    root, folder, manifest, _ = installed_recipe
    before = directory_snapshot(root)
    result = installer.install_direction("en-hi", root, root / "unused-cache")
    assert result == {"direction": "en-hi", "path": str(folder), "manifest": manifest, "reused": True}
    assert directory_snapshot(root) == before
    assert not (root / "unused-cache").exists()


@pytest.mark.parametrize("damage", [
    "changed_weight", "missing_file", "unrecorded_file", "unrecorded_nested_manifest",
    "malformed_manifest", "nonobject_manifest", "archive_changed", "archive_missing_sha", "archive_nonobject",
    "wrong_conversion", "changed_requirements", "changed_normalization", "missing_smoke", "quality_claim",
    "incomplete_inventory",
])
def test_repeat_install_refuses_damaged_or_changed_installation_without_modification(installed_recipe, damage):
    root, folder, manifest, save = installed_recipe
    if damage == "changed_weight":
        (folder / "model.bin").write_bytes(b"modified weights")
    elif damage == "missing_file":
        (folder / "source.spm").unlink()
    elif damage == "unrecorded_file":
        (folder / "extra.bin").write_bytes(b"extra")
    elif damage == "unrecorded_nested_manifest":
        (folder / "nested").mkdir()
        (folder / "nested/manifest.json").write_text("{}", encoding="utf-8")
    elif damage == "malformed_manifest":
        (folder / "manifest.json").write_text("{broken", encoding="utf-8")
    elif damage == "nonobject_manifest":
        (folder / "manifest.json").write_text("[]", encoding="utf-8")
    else:
        if damage == "archive_changed":
            manifest["archive"]["url"] = "https://example.invalid/other.zip"
        elif damage == "archive_missing_sha":
            manifest["archive"].pop("sha256")
        elif damage == "archive_nonobject":
            manifest["archive"] = []
        elif damage == "wrong_conversion":
            manifest["conversion"]["quantization"] = "float32"
        elif damage == "changed_requirements":
            manifest["requirements_sha256"] = "0" * 64
        elif damage == "changed_normalization":
            manifest["normalization"] = "different"
        elif damage == "missing_smoke":
            manifest.pop("runtime_smoke")
        elif damage == "quality_claim":
            manifest["bilingual_quality_qualified"] = True
        elif damage == "incomplete_inventory":
            manifest["files"].pop("LICENSE")
            (folder / "LICENSE").unlink()
        save()
    before = directory_snapshot(root)
    with pytest.raises(ValueError, match="No files were changed.*never overwrites"):
        installer.install_direction("en-hi", root, root / "unused-cache")
    assert directory_snapshot(root) == before
    assert not (root / "unused-cache").exists()


def test_repeat_install_refuses_non_directory_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "verify_runtime", lambda: dict(installer.RUNTIME))
    destination = tmp_path / "marian/en-hi"
    destination.parent.mkdir()
    destination.write_bytes(b"existing user file")
    with pytest.raises(ValueError, match="No files were changed"):
        installer.install_direction("en-hi", tmp_path, tmp_path / "unused-cache")
    assert destination.read_bytes() == b"existing user file"
    assert not (tmp_path / "unused-cache").exists()
