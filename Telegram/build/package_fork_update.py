#!/usr/bin/env python3

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


PRIMARY_KEY_ID = "ayugram-release-2026"
SECONDARY_KEY_ID = "ayugram-release-secondary-2026"


def parse_args():
	parser = argparse.ArgumentParser(
		description="Build and verify a fork-specific signed AyuGram update.")
	parser.add_argument("--packer", required=True, type=Path)
	parser.add_argument("--release-dir", required=True, type=Path)
	parser.add_argument("--output-dir", required=True, type=Path)
	parser.add_argument("--keys-dir", required=True, type=Path)
	parser.add_argument("--primary-key", required=True, type=Path)
	parser.add_argument("--secondary-key", required=True, type=Path)
	parser.add_argument("--openssl", default="openssl")
	parser.add_argument("--target", choices=("linux", "mac", "win", "win64", "winarm"), required=True)
	parser.add_argument("--arch", choices=("arm64", "x86_64"))
	parser.add_argument("--version", required=True, type=int)
	parser.add_argument("--channel", choices=("stable", "beta"), required=True)
	return parser.parse_args()


def update_name(target, arch, version, channel):
	suffix = "-beta" if channel == "beta" else ""
	if target == "linux":
		platform = "linux-x64"
	elif target == "mac":
		if arch == "arm64":
			platform = "mac-arm"
		elif arch == "x86_64":
			platform = "mac-x64"
		else:
			raise ValueError("macOS updates require --arch")
	elif target == "win":
		platform = "win-x86"
	elif target == "win64":
		platform = "win-x64"
	else:
		platform = "win-arm"
	return f"td-update-{platform}-{version}{suffix}"


def payload_paths(release_dir, target):
	if target == "mac":
		return [release_dir / "AyuGram.app"]
	paths = [release_dir / ("AyuGram.exe" if target.startswith("win") else "AyuGram")]
	paths.append(release_dir / ("Updater.exe" if target.startswith("win") else "Updater"))
	if target in ("win", "win64"):
		paths.append(release_dir / "modules" / ("x86" if target == "win" else "x64") / "d3d" / "d3dcompiler_47.dll")
	return paths


def run(command, **kwargs):
	print("$ " + " ".join(str(part) for part in command))
	subprocess.run([str(part) for part in command], check=True, **kwargs)


def main():
	args = parse_args()
	release_dir = args.release_dir.resolve()
	keys_dir = args.keys_dir.resolve()
	packer = args.packer.resolve()
	primary_key = args.primary_key.resolve()
	secondary_key = args.secondary_key.resolve()
	output_dir = args.output_dir.resolve()

	if args.version <= 1016 or args.version > 999999999:
		raise SystemExit("version must be a valid v2 numeric build version")
	if not packer.is_file():
		raise SystemExit(f"Packer not found: {packer}")
	for path in (primary_key, secondary_key):
		if not path.is_file():
			raise SystemExit(f"signing key not found: {path}")
	for path in payload_paths(release_dir, args.target):
		if not path.exists():
			raise SystemExit(f"update payload not found: {path}")
	for path in (keys_dir / "root-public.pem", keys_dir / "manifest.min.json", keys_dir / "manifest.sig"):
		if not path.is_file():
			raise SystemExit(f"update trust material not found: {path}")

	output_dir.mkdir(parents=True, exist_ok=True)
	name = update_name(args.target, args.arch, args.version, args.channel)
	temporary_dir = Path(tempfile.mkdtemp(prefix="ayugram-update-", dir=output_dir))
	try:
		signing_input = temporary_dir / "signing-input.bin"
		packer_args = [packer]
		for path in payload_paths(release_dir, args.target):
			packer_args.extend(("-path", path))
		packer_args.extend((
			"-target", args.target,
			"-version", args.version,
			"-channel", args.channel,
			"-keys-loc", keys_dir,
			"-emit-signing-input", signing_input,
		))
		if args.arch:
			packer_args.extend(("-arch", args.arch))
		run(packer_args, cwd=temporary_dir)

		unsigned = temporary_dir / f"{name}.unsigned"
		if not unsigned.is_file():
			raise SystemExit(f"Packer did not create {unsigned.name}")

		signatures = []
		for key_id, key_path in (
			(PRIMARY_KEY_ID, primary_key),
			(SECONDARY_KEY_ID, secondary_key),
		):
			signature = temporary_dir / f"{key_id}.sig"
			run((
				args.openssl,
				"pkeyutl",
				"-sign",
				"-rawin",
				"-inkey", key_path,
				"-in", signing_input,
				"-out", signature,
			))
			if signature.stat().st_size != 64:
				raise SystemExit(f"unexpected Ed25519 signature size for {key_id}")
			signatures.append(f"{key_id}:{signature}")

		run((
			packer,
			"-channel", args.channel,
			"-keys-loc", keys_dir,
			"-unsigned", unsigned,
			"-embed-signatures", *signatures,
		), cwd=temporary_dir)

		finished = temporary_dir / name
		if not finished.is_file():
			raise SystemExit(f"Packer did not create signed update {name}")
		shutil.copy2(finished, output_dir / name)
		print(f"Signed update written to {output_dir / name}")
	finally:
		shutil.rmtree(temporary_dir, ignore_errors=True)


if __name__ == "__main__":
	main()
