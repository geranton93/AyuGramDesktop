# Signed remote configuration

The desktop client uses a small remote configuration for developer/supporter
identity lists, custom supporter badges, and donation display values. The
configuration is not an update payload and has its own Ed25519 trust key:

```text
Telegram/Resources/update/rc-config-public.pem
```

The key is public and may be committed. Its matching private key must be kept in
an operator-controlled secret store and must never be placed in this repository,
an artifact, or a client log.

## Wire format

Each endpoint returns one JSON envelope:

```json
{
  "format": 1,
  "payload": "<base64url without padding>",
  "signature": "<base64url without padding>"
}
```

`payload` is the exact UTF-8 byte sequence covered by `signature`. The payload
is canonical JSON produced by `Telegram/build/sign_rc_config.py`:

```json
{
  "customBadges": [
    {"badge": {"documentId": "987654321012345", "text": "supporter"}, "id": "6007644928"}
  ],
  "developers": ["139303278"],
  "donateAmountRub": "386",
  "donateAmountTon": "3.50",
  "donateAmountUsd": "5.00",
  "donateUsername": "@ayugramOwner",
  "expires": 1800003600,
  "format": 1,
  "issued": 1800000000,
  "officialChannels": [1172503281],
  "supporterChannels": [3116497667],
  "supporters": ["5079320635"]
}
```

The signer sorts object keys, uses compact JSON with ASCII-safe escaping, and
adds one final newline before signing. The client verifies the signature over
those exact bytes; it does not verify a re-serialized or partially parsed
variant.

## Validation contract

The client rejects the response before changing RCManager state when any of the
following is true:

- the envelope is malformed, oversized, has another format, or has invalid
  base64url values;
- the Ed25519 signature is not exactly 64 bytes or does not verify against the
  pinned public key;
- the signed payload is outside its validity window, is more than five minutes
  in the future, or is valid for more than 30 days;
- an identity list or badge list has more than 4096 entries, an ID is invalid or
  duplicated, or an object contains unsupported fields;
- a badge text exceeds 256 characters, a username is outside its restricted
  format, or a donation amount is outside the decimal format accepted by the
  client.

The network layer also caps the response at 256 KiB, including chunked and
misreported `Content-Length` responses. Failed verification leaves the prior
valid state untouched; on first startup the built-in defaults remain active.

## Signing and publishing

Create a source payload using the exact schema above and sign it offline:

```bash
python3 Telegram/build/sign_rc_config.py \
  --input /secure/path/remote-config.json \
  --output /secure/path/desktop2 \
  --key /secure/path/rc-config-private.pem
```

Publish the resulting `desktop2` bytes from both configured endpoints. The
fallback endpoint must not return the legacy unsigned object. Test the output
locally before publishing:

```bash
python3 -m unittest Telegram/build/tests/test_sign_rc_config.py -v
```

The current client still contains the historical primary and fallback URLs so
the service can be migrated without a client-side endpoint change. That is a
temporary operational dependency, not a trust decision: unsigned data from
either URL is rejected. A future endpoint move should update both URLs in the
client and this document in the same change.
