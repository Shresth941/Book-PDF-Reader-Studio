"""Create an explicit manifest AFTER model license/code review and measured pilot.

This command hashes local files; it does not download, train or certify a model.
"""
import argparse
import hashlib
import json
from pathlib import Path
from app.services.local_translation import MODELS

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--direction", choices=list(MODELS), required=True)
    parser.add_argument("--revision", required=True, help="Exact upstream commit used for weights and custom code")
    parser.add_argument("--pilot-report", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--confirm-license-and-code-review", action="store_true", required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    report = json.loads(args.pilot_report.read_text(encoding="utf-8"))
    if report.get("qualified") is not True or report.get("direction") != args.direction or report.get("revision") != args.revision:
        parser.error("Pilot must explicitly qualify this direction and exact revision.")
    if not (root / "config.json").is_file() or not list(root.glob("*.safetensors")):
        parser.error("Expected complete local model config, tokenizer, reviewed Python files and safetensors weights.")
    manifest = {"model_id": MODELS[args.direction], "revision": args.revision, "qualified": True,
                "input_tokens": 512, "reviewer": args.reviewer, "pilot_report": report, "files": {}}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json" and "__pycache__" not in path.parts:
            with path.open("rb") as stream:
                manifest["files"][path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Manifest written. All inference remains offline and each translation requires review.")
