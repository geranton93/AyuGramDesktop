# AyuGram fork release runbook

This repository is published as `geranton93/AyuGramDesktop`. The application
uses the fork's raw update feeds and GitHub Release assets, and the update
payloads are verified with the Ed25519 trust material committed under
`Telegram/Resources/update/`. The source remains mergeable with upstream, but
the release and update control plane is fork-only.

The bundle and application data identifiers intentionally remain compatible
with the existing AyuGram installation. This makes the updater independent
without silently creating a second account directory or forcing a data
migration.

## Release flow

1. Update `Telegram/build/version` and commit the complete source change to
   `dev`.
2. For a stable build, set `BetaChannel 0` and push a tag such as `v7.2.7`.
   For a beta build, set `BetaChannel 1` and push a tag such as
   `v7.2.7-beta` (a descriptive suffix is also accepted).
3. `Fork release` validates the tag against the source version, checks the
   signed root manifest, and refuses to run outside `geranton93/AyuGramDesktop`.
4. The matrix builds macOS Intel and Apple Silicon, Windows x86/x64/ARM64,
   and Linux x64. Every platform builds `Telegram`, `Updater`, and `Packer`
   with auto-update enabled.
5. Platform binaries and installers are signed before the update payload is
   packed. The update helper signs the TDUP signing input with both channel
   keys, and `Packer` performs the same verification used by the client before
   the artifact is uploaded.
6. The publish job uploads the installers, portable archives, checksums, and
   six platform update payloads to the fork Release. Only after the complete
   set is present does it advance both `updates/current6` and `updates/current2`
   on `dev`.

The feed generator is monotonic: a lower version is rejected, a different
asset cannot replace an already published version, and an exact retry is
idempotent. A feed never points to an asset that is not under the fork's
canonical Release URL.

## Required repository secrets

The release workflow deliberately fails closed if any of these are missing.
Set them in the fork repository, not in the upstream repository:

| Secret | Value |
| --- | --- |
| `TDESKTOP_API_ID` | Numeric Telegram API ID belonging to this build. |
| `TDESKTOP_API_HASH` | 32-character Telegram API hash. |
| `AYUGRAM_UPDATE_RELEASE_PRIVATE_KEY` | PEM Ed25519 private key for `ayugram-release-2026`. |
| `AYUGRAM_UPDATE_SECONDARY_PRIVATE_KEY` | PEM Ed25519 private key for `ayugram-release-secondary-2026`. |
| `AYUGRAM_MACOS_P12_BASE64` | Base64-encoded Developer ID Application `.p12`. |
| `AYUGRAM_MACOS_P12_PASSWORD` | Password for that `.p12`. |
| `AYUGRAM_MACOS_SIGNING_IDENTITY` | Exact Developer ID Application certificate identity. |
| `AYUGRAM_APPLE_ID` | Apple ID used by `notarytool`. |
| `AYUGRAM_APPLE_TEAM_ID` | Apple Developer Team ID. |
| `AYUGRAM_APPLE_APP_PASSWORD` | App-specific password for notarization. |
| `AYUGRAM_WINDOWS_SIGNING_PFX_BASE64` | Base64-encoded Authenticode `.pfx`. |
| `AYUGRAM_WINDOWS_SIGNING_PFX_PASSWORD` | Password for that `.pfx`. |

For multiline PEM secrets, use `gh secret set` with standard input so the
private material is not placed in shell history:

```bash
gh secret set AYUGRAM_UPDATE_RELEASE_PRIVATE_KEY --repo geranton93/AyuGramDesktop < /secure/path/release-private.pem
gh secret set AYUGRAM_UPDATE_SECONDARY_PRIVATE_KEY --repo geranton93/AyuGramDesktop < /secure/path/secondary-private.pem
```

Do not commit any private key, certificate, password, API credential, signing
input, or generated signature. The workflow deletes temporary signing files
after packaging, and the artifact verification step checks SHA-256 files before
publication.

## Trust rotation

`root-public.pem`, `manifest.min.json`, and `manifest.sig` are public trust
material. The root private key is kept offline. To rotate a release key,
prepare and sign a new manifest offline, commit only the public manifest and
its root signature, then update the corresponding repository secret before
publishing a build that uses the new key. Keep at least one valid key in each
stable/beta threshold group during the overlap window.

An older installed binary that still trusts a different root cannot be
cryptographically migrated to this new root. Such users need one manual
installation of a build containing the new public root; later updates are
automatic. This is an intentional trust boundary, not a fallback to the
upstream updater.

## Remote configuration trust

The supporter/developer map and donation display values are a separate signed
configuration channel. The client accepts only the envelope documented in
[`docs/remote-config.md`](remote-config.md), verifies it with
`Telegram/Resources/update/rc-config-public.pem`, validates its time window and
field limits, and only then updates in-memory state. An HTTPS response alone is
not sufficient.

Both remote-config endpoints must serve the same signed envelope before a build
with this contract is published. Until that server migration is complete, an
old unsigned response is deliberately rejected and the client continues with
its built-in defaults. The private remote-config signing key is an operational
secret and must stay outside this repository.

## Checks before publication

For a local preflight, run:

```bash
python3 -m unittest Telegram/build/tests/test_generate_update_feed.py -v
python3 -m unittest Telegram/build/tests/test_sign_rc_config.py -v
openssl pkeyutl -verify -pubin -rawin -inkey Telegram/Resources/update/root-public.pem -in Telegram/Resources/update/manifest.min.json -sigfile Telegram/Resources/update/manifest.sig
```

The complete native packaging proof is produced by the release matrix. A
successful local feed test or C++ verifier test alone is not evidence that all
platform binaries were built, signed, notarized, uploaded, or that the fork's
live feeds were advanced.
