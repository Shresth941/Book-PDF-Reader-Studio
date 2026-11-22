"""Vercel build hook: package native OCR and a redistributable Hindi font.

Runs in the Linux build container, never on the user's Windows installation.
Build sources live outside the deployment tree and are removed on completion.
This assembles dependencies; it does not run project or document quality tests.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parent
NATIVE = ROOT / "vendor" / "native"
SOURCES = {
    "leptonica": ("https://github.com/DanBloomberg/leptonica.git", "63aef18d98432b8582a1565e241f7bd2ee9cc8d9", "1.85.0"),
    "tesseract": ("https://github.com/tesseract-ocr/tesseract.git", "db0ec62f81b0737fbbe184d8fea40af5738f8eef", "5.5.3"),
}
NOTO_COMMIT = "ffebf8c1ee449e544955a7e813c54f9b73848eac"
TESSDATA_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def fetch_file(url, destination, max_bytes, git_blob=None):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError(f"Dependency exceeds its bounded size: {destination.name}")
    if git_blob:
        actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if actual != git_blob:
            raise RuntimeError(f"Upstream font integrity mismatch: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def checkout(name, directory):
    url, commit, _ = SOURCES[name]
    directory.mkdir()
    run(["git", "init", "--quiet", str(directory)])
    run(["git", "-C", str(directory), "remote", "add", "origin", url])
    run(["git", "-C", str(directory), "fetch", "--depth", "1", "origin", commit])
    run(["git", "-C", str(directory), "checkout", "--detach", "FETCH_HEAD"])
    actual = run(["git", "-C", str(directory), "rev-parse", "HEAD"], capture_output=True).stdout.strip()
    if actual != commit:
        raise RuntimeError(f"Unexpected source revision for {name}")


def bundle_libraries(binary):
    # ldd resolves transitive linked libraries. Do not replace the runtime's libc
    # or dynamic loader; the build and runtime both target Amazon Linux 2023.
    output = run(["ldd", str(binary)], capture_output=True).stdout
    if "not found" in output:
        raise RuntimeError("Native OCR has an unresolved dynamic library")
    paths = re.findall(r"=>\s+(/\S+)\s+\(", output)
    runtime_owned = {"libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1"}
    rpm_packages = set()
    for value in paths:
        source = Path(value)
        if source.name in runtime_owned:
            continue
        shutil.copy2(source.resolve(), NATIVE / "lib" / source.name)
        query = run(["rpm", "-qf", "--qf", "%{NAME}\n", str(source.resolve())], capture_output=True)
        rpm_packages.update(query.stdout.strip().splitlines())
    records = []
    for package in sorted(rpm_packages):
        info = run(["rpm", "-q", "--qf", "%{NAME} %{VERSION}-%{RELEASE} %{LICENSE}\n", package], capture_output=True).stdout.strip()
        records.append(info)
        files = run(["rpm", "-ql", package], capture_output=True).stdout.splitlines()
        for value in files:
            source = Path(value)
            if source.is_file() and value.startswith("/usr/share/licenses/"):
                dest = NATIVE / "licenses" / "rpm" / package / source.relative_to("/usr/share/licenses")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
    (NATIVE / "licenses" / "RPM-PACKAGES.txt").write_text("\n".join(records) + "\n", encoding="utf-8")


def main():
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("Native deployment assembly requires the Linux x86_64 Vercel build image")
    if not shutil.which("dnf"):
        raise RuntimeError("Expected the Vercel Amazon Linux 2023 build image with dnf")
    run(["dnf", "install", "-y", "gcc", "gcc-c++", "make", "cmake", "git", "pkgconfig",
         "zlib-devel", "libpng-devel", "libjpeg-turbo-devel", "libtiff-devel",
         "libwebp-devel", "giflib-devel", "openjpeg2-devel"])
    if NATIVE.exists():
        raise RuntimeError("Native output already exists; use a fresh sanitized deployment stage")
    for subdirectory in ("bin", "libexec", "lib", "licenses"):
        (NATIVE / subdirectory).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdf-reader-native-build-") as temp:
        temp = Path(temp)
        prefix = temp / "install"
        for name in SOURCES:
            checkout(name, temp / name)
        common = ["-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF", f"-DCMAKE_INSTALL_PREFIX={prefix}"]
        run(["cmake", "-S", str(temp / "leptonica"), "-B", str(temp / "lept-build"),
             *common, "-DBUILD_PROG=OFF", "-DSW_BUILD=OFF", "-DSTRICT_CONF=ON"])
        run(["cmake", "--build", str(temp / "lept-build"), "--parallel", "2"])
        run(["cmake", "--install", str(temp / "lept-build")])
        run(["cmake", "-S", str(temp / "tesseract"), "-B", str(temp / "tess-build"),
             *common, f"-DCMAKE_PREFIX_PATH={prefix}", "-DBUILD_TRAINING_TOOLS=OFF", "-DBUILD_TESTS=OFF",
             "-DENABLE_NATIVE=OFF", "-DGRAPHICS_DISABLED=ON", "-DDISABLE_ARCHIVE=ON", "-DDISABLE_CURL=ON"])
        run(["cmake", "--build", str(temp / "tess-build"), "--parallel", "2"])
        run(["cmake", "--install", str(temp / "tess-build")])
        binary = NATIVE / "libexec" / "tesseract"
        shutil.copy2(prefix / "bin" / "tesseract", binary)
        run(["strip", str(binary)])
        bundle_libraries(binary)
        shutil.copy2(temp / "tesseract" / "LICENSE", NATIVE / "licenses" / "TESSERACT-LICENSE")
        shutil.copy2(temp / "leptonica" / "leptonica-license.txt", NATIVE / "licenses" / "LEPTONICA-LICENSE")
        configs = prefix / "share" / "tessdata" / "configs"
        if configs.is_dir():
            shutil.copytree(configs, ROOT / "models" / "tessdata" / "configs")
    wrapper = NATIVE / "bin" / "tesseract"
    wrapper.write_text('#!/bin/sh\nset -eu\nbase="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"\n'
                       'export LD_LIBRARY_PATH="$base/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
                       'exec "$base/libexec/tesseract" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    binary.chmod(0o755)
    fonts = ROOT / "models" / "fonts"
    fetch_file(f"https://raw.githubusercontent.com/notofonts/noto-fonts/{NOTO_COMMIT}/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Regular.ttf",
               fonts / "NotoSansDevanagari-Regular.ttf", 500_000, "d9167f632b699b82eb81e0e0ad1ddb89bcdbd6c3")
    fetch_file(f"https://raw.githubusercontent.com/notofonts/noto-fonts/{NOTO_COMMIT}/LICENSE",
               fonts / "OFL.txt", 20_000, "c82d72e422e2d08c5ab439b6bac7c2177ea0c565")
    fetch_file(f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{TESSDATA_COMMIT}/LICENSE",
               ROOT / "models" / "tessdata" / "LICENSE", 30_000)
    files = [*NATIVE.rglob("*"), *fonts.rglob("*")]
    manifest = {
        "platform": "Linux x86_64 / Amazon Linux 2023",
        "sources": {name: {"url": url, "commit": commit, "version": version} for name, (url, commit, version) in SOURCES.items()},
        "font_source": f"https://github.com/notofonts/noto-fonts/tree/{NOTO_COMMIT}",
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()},
        "note": "Dependency assembly record only; not document accuracy or end-to-end qualification.",
    }
    (NATIVE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Packaged native OCR, shared libraries, language-data licenses and the Hindi font.")


if __name__ == "__main__":
    main()
