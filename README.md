# AyuGram

![AyuGram Logo](.github/AyuGram.png) ![AyuChan](.github/AyuChan.png)

[ English  |   [Русский](README-RU.md) ]

## Features

- Full ghost mode (flexible)
- Messages history
- Anti-recall
- Font customization
- Streamer mode
- Local Telegram Premium
- Translator
- Media preview & quick reaction on force click (macOS)
- Enhanced appearance

And many more. Check out our [Documentation](https://docs.ayugram.one/desktop/).

<h3>
  <details>
    <summary>Preview</summary>
    <table>
      <tr>
        <td><img src='.github/demos/demo1.png' width='268' alt='Preferences'></td>
        <td><img src='.github/demos/demo2.png' width='268' alt='AyuGram Options'></td>
        <td><img src='.github/demos/demo3.png' width='268' alt='Message Filters'></td>
      </tr>
      <tr>
        <td><img src='.github/demos/demo4.png' width='268' alt='Appearance'></td>
        <td><img src='.github/demos/demo5.png' width='268' alt='Chats'></td>
      </tr>
    </table>
  </details>
</h3>

## Downloads

The canonical binaries for this fork are published in the [latest stable fork release](https://github.com/geranton93/AyuGramDesktop/releases/latest).
Use the [full releases list](https://github.com/geranton93/AyuGramDesktop/releases) when you intentionally want to test a beta or pre-release.
The fork-specific updater trusts only releases from `geranton93/AyuGramDesktop`.
If a platform asset is not present in the latest release yet, use that platform's build guide below instead of substituting an upstream package.

### Windows

#### Recommended: signed fork release

Download the installer for your architecture from the [latest stable fork release](https://github.com/geranton93/AyuGramDesktop/releases/latest).
The x64 installer is the right choice for most Windows PCs; use the ARM64 installer on Windows for ARM and the x86 installer only
for legacy 32-bit Windows. Portable ZIP archives are also available when a release publishes them.

With the [GitHub CLI](https://cli.github.com/), the current x64 installer can be downloaded with:

```powershell
gh release download --repo geranton93/AyuGramDesktop --pattern 'td-setup-win-x64-*.exe' --dir "$env:USERPROFILE\Downloads"
```

Run the downloaded installer manually. For a portable installation, download the matching `td-portable-win-*.zip`, extract it,
and launch `AyuGram.exe`.

Winget and Scoop packages are community-maintained and are not published by this fork. They may install a different AyuGram build,
so use the fork's Releases when you need the fork-specific updater.

#### Self-built

Follow the [fork build guide](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-win.md) if you want to
build by yourself.

### macOS

#### Recommended: signed fork release

Download the `.dmg` for your architecture from the [latest stable fork release](https://github.com/geranton93/AyuGramDesktop/releases/latest),
open it, and drag AyuGram to `Applications`. Choose the Apple Silicon package on an arm64 Mac and the Intel package on an x86_64 Mac.

With the [GitHub CLI](https://cli.github.com/), macOS packages can be downloaded with:

```bash
gh release download --repo geranton93/AyuGramDesktop --pattern '*.dmg' --dir "$HOME/Downloads"
open "$HOME/Downloads"/*.dmg
```

The Homebrew cask is community-maintained and is not published by this fork. Use the signed DMG from the fork's Releases for the
fork-specific updater.

Follow the [macOS build guide](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-mac.md) to build from source.

### Linux x64

#### Fork release

When a release contains the Linux x64 archive, download and extract it with the [GitHub CLI](https://cli.github.com/):

```bash
mkdir -p "$HOME/Downloads" "$HOME/.local/opt/ayugram"
gh release download --repo geranton93/AyuGramDesktop --pattern 'td-setup-linux-x64-*.tar.xz' --dir "$HOME/Downloads"
tar -xJf "$HOME"/Downloads/td-setup-linux-x64-*.tar.xz -C "$HOME/.local/opt/ayugram"
cd "$HOME/.local/opt/ayugram/AyuGram"
./AyuGram
```

If the matching asset is not present yet, use the [Linux build guide](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-linux.md)
or one of the community packages below.

### Arch Linux

The following Linux packages are community-maintained. They are convenient installation options, but they are not controlled by this
fork and may not use the fork-specific updater. For the canonical fork build, use the Linux archive from Releases or build from source.

#### From source (recommended)

Install `ayugram-desktop` from [AUR](https://aur.archlinux.org/packages/ayugram-desktop).

#### Prebuilt binaries

Install `ayugram-desktop-bin` from [AUR](https://aur.archlinux.org/packages/ayugram-desktop-bin).

Note: these binaries aren't officially maintained by us.

### NixOS

#### Flake (recommended)

Install `ayugram-desktop` from [ndfined-crp/ayugram-desktop](https://github.com/ndfined-crp/ayugram-desktop)

#### Nixpkgs

Install `ayugram-desktop` from [nixpkgs](https://search.nixos.org/packages?channel=unstable&show=ayugram-desktop)

### ALT Linux

[Sisyphus](https://packages.altlinux.org/en/sisyphus/srpms/ayugram-desktop/)

### Gentoo Linux

See [this repository](https://codeberg.org/OverLessArtem/ayugram-ebuild-gentoo) for installation manual.

### Void Linux
See [this repository](https://codeberg.org/OverLessArtem/ayugram-template-void) for installation manual.

### EPM

`epm play ayugram`

### Fedora

From [RPM Fusion](https://admin.rpmfusion.org/pkgdb/package/free/ayugram-desktop/) repository.

```bash
dnf install ayugram-desktop
```

### Any other Linux distro

Flatpak: https://github.com/0FL01/AyuGramDesktop-flatpak

Or follow the [fork build guide](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-linux.md).

## Donation

Enjoy using **AyuGram**? Consider sending us a tip!

[Here's available methods.](https://docs.ayugram.one/donate/)

## Credits

### Telegram clients

- [Telegram Desktop](https://github.com/telegramdesktop/tdesktop)
- [Kotatogram](https://github.com/kotatogram/kotatogram-desktop)
- [64Gram](https://github.com/TDesktop-x64/tdesktop)
- [Forkgram](https://github.com/forkgram/tdesktop)

### Libraries used

- [JSON for Modern C++](https://github.com/nlohmann/json)
- [SQLite](https://github.com/sqlite/sqlite)
- [sqlite_orm](https://github.com/fnc12/sqlite_orm)
- [androidx sources](https://github.com/androidx/androidx)

### Icons

- [Solar Icon Set](https://www.figma.com/community/file/1166831539721848736)

### Bots

- [TelegramDB](https://t.me/tgdatabase) for username lookup by ID (until closing free inline mode at 2 April 2026)
