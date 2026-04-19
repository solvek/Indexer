# Релізи на GitHub (готові бінарники)

Після **push тега** виду `v0.1`, `v0.2`, … GitHub Actions збирає три виконувані файли (Linux, Windows, macOS) і **створює або оновлює** [Release](https://github.com/solvek/Indexer/releases) з цими файлами як assets.

## Нумерація версій

Рекомендований формат тегів — **`v` + мінорна версія**, наприклад:

- `v0.1`, `v0.2`, `v0.3` — зручно для ранніх ітерацій;
- за потреби дрібні виправлення: `v0.1.1`, `v0.2.0` — теж підходять (workflow реагує на будь-який тег, що починається з `v`).

Головне: тег має збігатися з фільтром `v*` у workflow (див. `.github/workflows/build-binaries.yml`).

## Що зробити, щоб з’явився новий Release

1. Переконайся, що потрібні зміни **закомічені** і **запушені** в гілку (зазвичай `main`), з якої ти релізиш.
2. Створи **легкий тег** на потрічному коміті (приклад для `v0.2`):

   ```bash
   git tag v0.2
   ```

   Якщо тег уже був локально — видали й створи знову, або використай іншу версію.

3. **Запуш тег** (саме push тега запускає збірку й публікацію Release):

   ```bash
   git push origin v0.2
   ```

4. Відкрий **Actions** у репозиторії → workflow **Build binaries** → дочекайся успіху всіх трьох платформ і job **Publish GitHub Release**.
5. Відкрий **Releases** — має з’явитися реліз `v0.2` з файлами на кшталт:
   - `indexer-v0.2-linux-x86_64`
   - `indexer-v0.2-windows-x86_64.exe`
   - `indexer-v0.2-macos`

Ручний запуск workflow (**Run workflow**) з гілки лише збирає артефакти в Actions; **Release з гілки не створюється** — для Release потрібен **push тега** `v*`.

## Якщо Release не створюється

У **Settings → Actions → General → Workflow permissions** має бути дозвіл на запис для `GITHUB_TOKEN` (наприклад *Read and write contents and packages permissions*), інакше job публікації не зможе створити Release. Деталі — у [документації GitHub про GITHUB_TOKEN](https://docs.github.com/en/actions/security-guides/automatic-token-authentication#permissions-for-the-github_token).

Якщо збірка однієї з ОС падає, job **Publish GitHub Release** не запуститься — спочатку виправ помилку в Actions.
