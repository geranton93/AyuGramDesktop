#!/usr/bin/env python3
"""Create the canonical fork-owned HTTP update feed."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


REPOSITORY = "geranton93/AyuGramDesktop"
PLATFORMS = ("armac", "linux", "mac", "win", "win64", "winarm")
CHANNELS = ("stable", "beta")
TAG_PATTERN = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
ASSET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class FeedError(ValueError):
    """An input cannot be accepted into the update feed."""


def empty_feed() -> dict[str, dict[str, dict[str, Any]]]:
    return {
        platform: {channel: {} for channel in CHANNELS}
        for platform in PLATFORMS
    }


def error(message: str) -> None:
    raise FeedError(message)


def parse_base(value: str) -> int:
    try:
        result = int(value, 10)
    except ValueError:
        error("base version must be a positive decimal integer")
    if result <= 0:
        error("base version must be a positive decimal integer")
    return result


def parse_assets(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            error(f"asset must use platform=filename syntax: {value!r}")
        platform, filename = value.split("=", 1)
        if platform not in PLATFORMS:
            error(f"unknown platform in asset: {platform!r}")
        if platform in result:
            error(f"duplicate asset platform: {platform}")
        if not ASSET_PATTERN.fullmatch(filename):
            error(f"asset filename is unsafe: {filename!r}")
        result[platform] = filename

    missing = [platform for platform in PLATFORMS if platform not in result]
    if missing:
        error("missing asset platforms: " + ", ".join(missing))
    return result


def release_url(tag: str, filename: str) -> str:
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"


def validate_entry(
    platform: str,
    channel: str,
    entry: Any,
) -> dict[str, Any]:
    if entry == {}:
        return {}
    if not isinstance(entry, dict):
        error(f"feed entry for {platform}:{channel} must be an object")
    if set(entry) != {"link", "released"}:
        error(
            f"feed entry for {platform}:{channel} must contain only link and released"
        )
    released = entry["released"]
    if isinstance(released, bool) or not isinstance(released, int) or released <= 0:
        error(f"feed entry for {platform}:{channel} has an invalid released version")
    link = entry["link"]
    prefix = f"https://github.com/{REPOSITORY}/releases/download/"
    if not isinstance(link, str) or not link.startswith(prefix):
        error(f"feed entry for {platform}:{channel} is not a fork Release URL")
    suffix = link[len(prefix) :]
    if not suffix or "/" not in suffix:
        error(f"feed entry for {platform}:{channel} has an invalid Release URL")
    tag, filename = suffix.split("/", 1)
    if not TAG_PATTERN.fullmatch(tag) or not ASSET_PATTERN.fullmatch(filename):
        error(f"feed entry for {platform}:{channel} has an unsafe Release URL")
    return {"link": link, "released": released}


def read_existing(path: Path | None) -> dict[str, dict[str, dict[str, Any]]]:
    if path is None:
        return empty_feed()
    try:
        raw = path.read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        error(f"could not read existing feed {path}: {exception}")
    if not isinstance(document, dict):
        error("existing feed must be a JSON object")

    unknown = sorted(set(document) - set(PLATFORMS))
    if unknown:
        error("existing feed has unknown platform keys: " + ", ".join(unknown))

    result = empty_feed()
    for platform in PLATFORMS:
        value = document.get(platform)
        if value is None:
            error(f"existing feed is missing platform: {platform}")
        if not isinstance(value, dict):
            error(f"existing feed entry for {platform} must be an object")
        unknown_channels = sorted(set(value) - set(CHANNELS))
        if unknown_channels:
            error(
                f"existing feed has unknown channels for {platform}: "
                + ", ".join(unknown_channels)
            )
        for channel in CHANNELS:
            result[platform][channel] = validate_entry(
                platform,
                channel,
                value.get(channel, {}),
            )
    return result


def write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            descriptor = -1
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def generate_feed(
    *,
    existing: Path | None,
    output: Path,
    repository: str,
    tag: str,
    base: int,
    channel: str,
    assets: list[str],
) -> bytes:
    if repository != REPOSITORY:
        error(f"repository must be exactly {REPOSITORY}")
    if not TAG_PATTERN.fullmatch(tag):
        error("tag must match v<major>.<minor>.<patch> with an optional suffix")
    if channel not in CHANNELS:
        error("channel must be stable or beta")
    if base <= 0:
        error("base version must be a positive decimal integer")

    feed = read_existing(existing)
    parsed_assets = parse_assets(assets)
    for platform, filename in parsed_assets.items():
        previous = feed[platform][channel]
        link = release_url(tag, filename)
        if previous:
            if base < previous["released"]:
                error(
                    f"new version {base} is lower than existing "
                    f"{platform}:{channel} version {previous['released']}"
                )
            if base == previous["released"]:
                if previous["link"] == link:
                    continue
                error(
                    f"version {base} is already published for "
                    f"{platform}:{channel} with a different Release URL"
                )
        feed[platform][channel] = {
            "link": link,
            "released": base,
        }

    return (
        json.dumps(feed, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--existing", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--repo", required=True)
    result.add_argument("--tag", required=True)
    result.add_argument("--base", required=True, type=parse_base)
    result.add_argument("--channel", required=True)
    result.add_argument("--asset", action="append", required=True)
    return result


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        content = generate_feed(
            existing=options.existing,
            output=options.output,
            repository=options.repo,
            tag=options.tag,
            base=options.base,
            channel=options.channel,
            assets=options.asset,
        )
        write_atomically(options.output, content)
    except FeedError as exception:
        print(f"error: {exception}", file=sys.stderr)
        return 2
    except OSError as exception:
        print(f"error: could not write feed: {exception}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
