#!/usr/bin/env python3
"""Sign the fork-owned remote AyuGram configuration envelope."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


MAX_ARRAY_ITEMS = 4096
MAX_BADGE_TEXT_SIZE = 256
MAX_DONATION_VALUE_SIZE = 32
MAX_LIFETIME = 30 * 24 * 60 * 60
MAX_EXACT_JSON_INTEGER = 9_007_199_254_740_991
ID_PATTERN = re.compile(r"^[1-9][0-9]{0,18}$")
USERNAME_PATTERN = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")
AMOUNT_PATTERN = re.compile(r"^[0-9]{1,9}(?:\.[0-9]{1,2})?$")
LIST_FIELDS = (
    "developers",
    "officialChannels",
    "supporters",
    "supporterChannels",
)
REQUIRED_FIELDS = {
    "format",
    "issued",
    "expires",
    *LIST_FIELDS,
    "customBadges",
    "donateUsername",
    "donateAmountUsd",
    "donateAmountTon",
    "donateAmountRub",
}


class ConfigError(ValueError):
    """A remote configuration cannot be signed safely."""


def fail(message: str) -> None:
    raise ConfigError(message)


def read_positive_id(value: Any, field: str) -> int:
    if isinstance(value, bool):
        fail(f"{field} must be a positive integer")
    if isinstance(value, int):
        result = value
        if result > MAX_EXACT_JSON_INTEGER:
            fail(f"{field} must be a decimal string above the JSON-safe range")
    elif isinstance(value, str) and ID_PATTERN.fullmatch(value):
        result = int(value, 10)
    else:
        fail(f"{field} must be a positive integer")
    if result <= 0 or result > 9_223_372_036_854_775_807:
        fail(f"{field} is outside the supported range")
    return result


def read_timestamp(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > MAX_EXACT_JSON_INTEGER
    ):
        fail(f"{field} must be a positive decimal integer")
    return value


def validate_id_list(document: dict[str, Any], field: str) -> None:
    values = document.get(field)
    if not isinstance(values, list) or len(values) > MAX_ARRAY_ITEMS:
        fail(f"{field} must be an array with at most {MAX_ARRAY_ITEMS} entries")
    seen: set[int] = set()
    for index, value in enumerate(values):
        result = read_positive_id(value, f"{field}[{index}]")
        if result in seen:
            fail(f"{field}[{index}] is duplicated")
        seen.add(result)


def validate_string(
    document: dict[str, Any],
    field: str,
    pattern: re.Pattern[str],
) -> None:
    value = document.get(field)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_DONATION_VALUE_SIZE
        or not pattern.fullmatch(value)
    ):
        fail(f"{field} has an invalid value")


def validate_config(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        fail("remote configuration must be a JSON object")
    if set(document) != REQUIRED_FIELDS:
        fail("remote configuration has missing or unsupported fields")
    if document["format"] != 1:
        fail("remote configuration format must be 1")

    issued = read_timestamp(document["issued"], "issued")
    expires = read_timestamp(document["expires"], "expires")
    if expires <= issued or expires - issued > MAX_LIFETIME:
        fail("remote configuration validity window is invalid")

    for field in LIST_FIELDS:
        validate_id_list(document, field)

    custom_badges = document["customBadges"]
    if not isinstance(custom_badges, list) or len(custom_badges) > MAX_ARRAY_ITEMS:
        fail(
            "customBadges must be an array with "
            f"at most {MAX_ARRAY_ITEMS} entries"
        )
    seen_badges: set[int] = set()
    for index, value in enumerate(custom_badges):
        if not isinstance(value, dict) or set(value) != {"id", "badge"}:
            fail(f"customBadges[{index}] has an invalid shape")
        badge_id = read_positive_id(value["id"], f"customBadges[{index}].id")
        if badge_id in seen_badges:
            fail(f"customBadges[{index}].id is duplicated")
        seen_badges.add(badge_id)
        badge = value["badge"]
        if not isinstance(badge, dict) or set(badge) - {"documentId", "text"}:
            fail(f"customBadges[{index}].badge has unsupported fields")
        read_positive_id(
            badge.get("documentId"),
            f"customBadges[{index}].badge.documentId",
        )
        text = badge.get("text", "")
        if not isinstance(text, str) or len(text) > MAX_BADGE_TEXT_SIZE:
            fail(f"customBadges[{index}].badge.text is invalid")

    validate_string(document, "donateUsername", USERNAME_PATTERN)
    for field in ("donateAmountUsd", "donateAmountTon", "donateAmountRub"):
        validate_string(document, field, AMOUNT_PATTERN)
    return document


def canonical_payload(path: Path) -> bytes:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exception:
        fail(f"could not read input JSON: {exception}")
    validate_config(document)
    return (
        json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sign_payload(payload: bytes, key: Path, openssl: str) -> bytes:
    if not key.is_file():
        fail(f"signing key not found: {key}")
    with tempfile.TemporaryDirectory(prefix="ayugram-rc-sign-") as temporary:
        temporary_path = Path(temporary)
        payload_path = temporary_path / "payload.json"
        signature_path = temporary_path / "payload.sig"
        payload_path.write_bytes(payload)
        try:
            subprocess.run(
                [
                    openssl,
                    "pkeyutl",
                    "-sign",
                    "-rawin",
                    "-inkey",
                    str(key),
                    "-in",
                    str(payload_path),
                    "-out",
                    str(signature_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exception:
            fail(f"Ed25519 signing failed: {exception}")
        signature = signature_path.read_bytes()
    if len(signature) != 64:
        fail("Ed25519 signature must contain exactly 64 bytes")
    return signature


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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", required=True, type=Path)
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--key", required=True, type=Path)
    result.add_argument("--openssl", default="openssl")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        payload = canonical_payload(args.input)
        signature = sign_payload(payload, args.key, args.openssl)
        envelope = json.dumps(
            {
                "format": 1,
                "payload": base64.urlsafe_b64encode(payload)
                .decode("ascii")
                .rstrip("="),
                "signature": base64.urlsafe_b64encode(signature)
                .decode("ascii")
                .rstrip("="),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii") + b"\n"
        write_atomically(args.output, envelope)
    except ConfigError as exception:
        raise SystemExit(str(exception)) from exception


if __name__ == "__main__":
    main()
