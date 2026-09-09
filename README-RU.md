# AyuGram

![AyuGram Лого](.github/AyuGram.png) ![AyuChan](.github/AyuChan.png)

[ [English](README.md)  | Русский ]

## Функции и Фишки

- Полный режим призрака (настраиваемый)
- История удалений и изменений сообщений
- Кастомизация шрифта
- Режим Стримера
- Локальный телеграм премиум
- Переводчик
- Превью медиа и быстрая реакция при сильном нажатии на тачпад (macOS)
- Улучшенный вид

И многое другое. Посмотрите нашу [Документацию](https://docs.ayugram.one/desktop/) для более подробной информации.

<h3>
  <details>
    <summary>Превью</summary>
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

## Установка

Канонические сборки этого форка публикуются в [последнем стабильном релизе форка](https://github.com/geranton93/AyuGramDesktop/releases/latest).
Полный [список релизов](https://github.com/geranton93/AyuGramDesktop/releases) используйте, если намеренно устанавливаете beta или pre-release.
Обновлятор форка доверяет только релизам из `geranton93/AyuGramDesktop`.
Если в последнем релизе ещё нет файла для вашей платформы, используйте приведённое ниже руководство по сборке, а не пакет upstream-проекта.

### Windows

#### Рекомендуемый способ: подписанный релиз форка

Скачайте установщик для нужной архитектуры из [последнего стабильного релиза форка](https://github.com/geranton93/AyuGramDesktop/releases/latest).
Для большинства компьютеров с Windows подходит x64; ARM64 используйте на Windows for ARM, а x86 — только для старой 32-разрядной Windows.
Если релиз их публикует, там также доступны portable ZIP-архивы.

С помощью [GitHub CLI](https://cli.github.com/) текущий установщик x64 можно скачать командой:

```powershell
gh release download --repo geranton93/AyuGramDesktop --pattern 'td-setup-win-x64-*.exe' --dir "$env:USERPROFILE\Downloads"
```

Запустите скачанный установщик вручную. Для portable-версии скачайте соответствующий `td-portable-win-*.zip`, распакуйте его
и запустите `AyuGram.exe`.

Пакеты Winget и Scoop поддерживаются сообществом, а не этим форком. Они могут устанавливать другую сборку AyuGram,
поэтому для fork-specific updater используйте Releases форка.

#### Сборка вручную

Следуйте [руководству по сборке форка](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-win.md), если
вы хотите собрать AyuGram сами.

### macOS

#### Рекомендуемый способ: подписанный релиз форка

Скачайте `.dmg` для своей архитектуры из [последнего стабильного релиза форка](https://github.com/geranton93/AyuGramDesktop/releases/latest),
откройте его и перетащите AyuGram в `Applications`. Для Mac с arm64 выбирайте Apple Silicon, для x86_64 — Intel.

С помощью [GitHub CLI](https://cli.github.com/) пакет macOS можно скачать командами:

```bash
gh release download --repo geranton93/AyuGramDesktop --pattern '*.dmg' --dir "$HOME/Downloads"
open "$HOME/Downloads"/*.dmg
```

Homebrew cask поддерживается сообществом, а не этим форком. Для fork-specific updater используйте подписанный DMG из Releases форка.

Для сборки из исходников используйте [руководство по сборке macOS](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-mac.md).

### Linux x64

#### Релиз форка

Если в релизе уже опубликован архив Linux x64, скачайте и распакуйте его через [GitHub CLI](https://cli.github.com/):

```bash
mkdir -p "$HOME/Downloads" "$HOME/.local/opt/ayugram"
gh release download --repo geranton93/AyuGramDesktop --pattern 'td-setup-linux-x64-*.tar.xz' --dir "$HOME/Downloads"
tar -xJf "$HOME"/Downloads/td-setup-linux-x64-*.tar.xz -C "$HOME/.local/opt/ayugram"
cd "$HOME/.local/opt/ayugram/AyuGram"
./AyuGram
```

Если подходящего файла пока нет, используйте [руководство по сборке Linux](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-linux.md)
или один из пакетов сообщества ниже.

### Arch Linux

Следующие Linux-пакеты поддерживаются сообществом. Это удобные варианты установки, но они не контролируются этим форком
и могут не использовать fork-specific updater. Для канонической сборки форка используйте Linux-архив из Releases или соберите приложение из исходников.

#### Из исходников (рекомендованный способ)

Установите `ayugram-desktop` из [AUR](https://aur.archlinux.org/packages/ayugram-desktop).

#### Готовые бинарники

Установите `ayugram-desktop-bin` из [AUR](https://aur.archlinux.org/packages/ayugram-desktop-bin).

Примечание: данный пакет собирается не нами.

### NixOS

#### Флейк (рекомендуется)

Установите `ayugram-desktop` из [ndfined-crp/ayugram-desktop](https://github.com/ndfined-crp/ayugram-desktop)

#### Nixpkgs

Установите `ayugram-desktop` из [nixpkgs](https://search.nixos.org/packages?channel=unstable&show=ayugram-desktop)

### ALT Linux

[Sisyphus](https://packages.altlinux.org/en/sisyphus/srpms/ayugram-desktop/)

### Gentoo Linux

Инструкцию по установке можно найти в [этом репозитории](https://codeberg.org/OverLessArtem/ayugram-ebuild-gentoo).

### Void Linux
Инструкцию по установке можно найти в [этом репозитории](https://codeberg.org/OverLessArtem/ayugram-template-void)

### EPM

`epm play ayugram`

### Fedora

Из репозитория [RPM Fusion](https://admin.rpmfusion.org/pkgdb/package/free/ayugram-desktop/).

```bash
dnf install ayugram-desktop
```

### Любой другой Линукс дистрибутив

Flatpak: https://github.com/0FL01/AyuGramDesktop-flatpak

Или следуйте [руководству по сборке форка](https://github.com/geranton93/AyuGramDesktop/blob/dev/docs/building-linux.md).

## Пожертвования

Вам нравится использовать **AyuGram**? Оставьте нам чаевые!

[Здесь доступные варианты.](https://docs.ayugram.one/donate/)

## Использованные материалы

### Телеграм клиенты

- [Telegram Desktop](https://github.com/telegramdesktop/tdesktop)
- [Kotatogram](https://github.com/kotatogram/kotatogram-desktop)
- [64Gram](https://github.com/TDesktop-x64/tdesktop)
- [Forkgram](https://github.com/forkgram/tdesktop)

### Использованные библиотеки

- [JSON for Modern C++](https://github.com/nlohmann/json)
- [SQLite](https://github.com/sqlite/sqlite)
- [sqlite_orm](https://github.com/fnc12/sqlite_orm)

### Иконки

- [Solar Icon Set](https://www.figma.com/community/file/1166831539721848736)

### Боты

- [TelegramDB](https://t.me/tgdatabase) для получения юзернейма по ID (до закрытия бесплатной версии 2 апреля 2026)
