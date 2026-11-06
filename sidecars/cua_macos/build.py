# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

"""构建独立 ARM64 bundle；开发包与已公证发布包使用同一文件布局。"""

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def run(*arguments: str) -> str:
    """执行有界构建步骤，失败时保留工具原始诊断。"""
    result = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, timeout=1200)
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        result.check_returncode()
    return result.stdout.strip()


def archive_bundle(bundle: Path, archive: Path) -> None:
    """归档常规 bundle 文件，保留执行位并拒绝开发目录链接。"""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(bundle.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Bundle contains a symlink: {path}")
            if path.is_file():
                output.write(path, path.relative_to(bundle.parent))


def digest(path: Path) -> str:
    """计算产物的 SHA-256。"""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    """只发布新版本目录，不覆盖已构建版本或上传服务端清单。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--identity", default="-", help="Developer ID identity; '-' creates a development-only ad-hoc bundle")
    parser.add_argument("--notary-profile", help="Existing notarytool keychain profile; required for a release")
    parser.add_argument("--output", type=Path, help="New version directory, defaults to publish/<version>")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Build requires macOS and Xcode")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        parser.error("Version must have three numeric components")
    release = args.identity != "-"
    if release and (not args.identity.startswith("Developer ID Application:") or not args.notary_profile):
        parser.error("A release requires a Developer ID Application identity and --notary-profile")
    if not release and args.notary_profile:
        parser.error("An ad-hoc bundle cannot be notarized")
    root = Path(__file__).resolve().parent
    output = (args.output or root / "publish" / args.version).resolve()
    if output.exists():
        parser.error(f"Output already exists: {output}")
    scratch = root.parents[1] / ".cache/cua-macos-swift"
    build = ["swift", "build", "--package-path", str(root), "--scratch-path", str(scratch),
             "--configuration", "release", "--arch", "arm64", "--disable-sandbox"]
    print(run(*build, "--product", "desktop-sidecar"))
    binary = Path(run(*build, "--show-bin-path")) / "desktop-sidecar"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-", dir=output.parent) as temporary:
        work = Path(temporary)
        bundle = work / "runtime/DesktopSidecar.app"
        contents = bundle / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        executable = contents / "MacOS/desktop-sidecar"
        shutil.copy2(binary, executable)
        executable.chmod(0o755)
        with (contents / "Info.plist").open("wb") as stream:
            plistlib.dump({
                "CFBundleIdentifier": "com.axionixlabs.desktop-sidecar", "CFBundleExecutable": "desktop-sidecar",
                "CFBundleName": "Desktop Sidecar", "CFBundleDisplayName": "Desktop Sidecar",
                "CFBundlePackageType": "APPL", "CFBundleVersion": args.version,
                "CFBundleShortVersionString": args.version, "LSMinimumSystemVersion": "14.2",
                "LSUIElement": True, "NSHighResolutionCapable": True,
            }, stream)
        signing = ["/usr/bin/codesign", "--force", "--options", "runtime", "--sign", args.identity]
        if release:
            signing.append("--timestamp")
        run(*signing, str(bundle))
        archive = work / "package.zip"
        if release:
            archive_bundle(bundle, archive)
            run("xcrun", "notarytool", "submit", str(archive), "--keychain-profile", args.notary_profile, "--wait")
            run("xcrun", "stapler", "staple", str(bundle))
            run("xcrun", "stapler", "validate", str(bundle))
            run("/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose", str(bundle))
        run("/usr/bin/codesign", "--verify", "--strict", str(bundle))
        archive_bundle(bundle, archive)
        sha = digest(archive)
        name = f"cua-macos-arm64-{args.version}-{sha[:16]}.zip"
        archive.rename(work / name)
        inventory = {path.relative_to(work / "runtime").as_posix(): {"size": path.stat().st_size, "hash": digest(path)}
                     for path in sorted(bundle.rglob("*")) if path.is_file()}
        (work / "artifact.json").write_text(json.dumps({
            "version": args.version, "station": "darwin", "arch": "arm64", "bundleIdentifier": "com.axionixlabs.desktop-sidecar",
            "minimumOS": "14.2", "signature": args.identity, "notarized": release,
            "filename": name, "size": (work / name).stat().st_size, "hash": sha, "files": inventory,
            "swift": run("swift", "--version"), "xcode": run("xcodebuild", "-version"),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        work.rename(output)
    print(output / "artifact.json")


if __name__ == "__main__":
    main()
