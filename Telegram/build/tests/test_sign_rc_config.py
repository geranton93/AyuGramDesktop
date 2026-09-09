#!/usr/bin/env python3

import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "sign_rc_config.py"


def decode_base64_url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class SignRemoteConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="ayugram-rc-test-")
        self.root = Path(self.directory.name)
        self.private_key = self.root / "private.pem"
        self.public_key = self.root / "public.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", self.private_key],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                self.private_key,
                "-pubout",
                "-out",
                self.public_key,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def config(self) -> dict:
        return {
            "format": 1,
            "issued": 1_800_000_000,
            "expires": 1_800_003_600,
            "developers": ["139303278"],
            "officialChannels": [1_172_503_281],
            "supporters": ["5079320635"],
            "supporterChannels": [3_116_497_667],
            "customBadges": [
                {
                    "id": "6007644928",
                    "badge": {
                        "documentId": "987654321012345",
                        "text": "supporter",
                    },
                }
            ],
            "donateUsername": "@ayugramOwner",
            "donateAmountUsd": "5.00",
            "donateAmountTon": "3.50",
            "donateAmountRub": "386",
        }

    def sign(self, document: dict, output: Path) -> subprocess.CompletedProcess:
        source = self.root / "config.json"
        source.write_text(json.dumps(document), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--input",
                str(source),
                "--output",
                str(output),
                "--key",
                str(self.private_key),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_output_is_signed_and_does_not_contain_private_key(self) -> None:
        output = self.root / "response.json"
        result = self.sign(self.config(), output)
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(output.read_text(encoding="utf-8"))
        payload = decode_base64_url(envelope["payload"])
        signature = decode_base64_url(envelope["signature"])
        payload_path = self.root / "payload.json"
        signature_path = self.root / "payload.sig"
        payload_path.write_bytes(payload)
        signature_path.write_bytes(signature)
        verified = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-rawin",
                "-inkey",
                self.public_key,
                "-in",
                payload_path,
                "-sigfile",
                signature_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr.decode())
        self.assertNotIn(
            self.private_key.read_text(encoding="utf-8"),
            output.read_text(encoding="utf-8"),
        )
        self.assertEqual(json.loads(payload), self.config())

    def test_invalid_donation_value_is_rejected(self) -> None:
        document = self.config()
        document["donateAmountUsd"] = "5\n"
        output = self.root / "response.json"
        result = self.sign(document, output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_missing_field_is_rejected(self) -> None:
        document = self.config()
        del document["supporters"]
        result = self.sign(document, self.root / "response.json")
        self.assertNotEqual(result.returncode, 0)

    def test_large_numeric_id_is_rejected(self) -> None:
        document = self.config()
        document["developers"] = [9_876_543_210_123_456]
        result = self.sign(document, self.root / "response.json")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
