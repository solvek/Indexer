# Індексатор архівних документів

Скрипт автоматично обробляє скани архівних документів (метрики, акти, списки) через Gemini AI та зберігає знайдені генеалогічні дані в локальну SQLite базу.

**Оригінальна специфікація проєкту** (текст з PDF, зіставлення з поточною реалізацією та зауваження щодо застарілого): [docs/spetsyfikatsiia.md](docs/spetsyfikatsiia.md).

## Встановлення

### 1. Python

Потрібен Python 3.11+. Перевірити версію:
```bash
python --version
```

### 2. Залежності

```bash
pip install -r requirements.txt
```

Або у віртуальному середовищі (рекомендовано):
```bash
python -m venv .venv

# Linux / macOS / Oracle Cloud
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Ключі

```bash
cp sample.env .env
# відкрити .env і заповнити своїми ключами
```

| Змінна | Де взяти |
|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GOOGLE_DRIVE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials → API key. Увімкніть Google Drive API. Для публічних папок. |
| `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS` | (опційно) Абсолютний шлях до JSON **Desktop** OAuth client, якщо API key у проєкті блокують методи `files.get` / `files.list` (403 *method … blocked*). Перший запуск відкриє вхід Google у браузері. |
| `GOOGLE_DRIVE_OAUTH_TOKEN` | (опційно) Файл збереження access token, напр. `data/drive_oauth_token.json` (створюється автоматично; не комітайте). |
| `GOOGLE_DRIVE_SERVICE_ACCOUNT` | (рекоменд. для сервера) Абсолютний шлях до JSON **service account** (ключ, без пароля). **Браузер не потрібен** — зручно на Oracle Cloud та ін. Див. розділ нижче. Якщо змінна задана, індексатор **не** використає `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS` / `GOOGLE_DRIVE_API_KEY` для Drive. |
| `DEFAULT_MODEL` | Модель за замовчуванням, можна не міняти |

### Google Drive: Service account (сервер / Oracle Cloud, без браузера)

1. **APIs & Services** → **Credentials** → **Create credentials** → **Service account** (назва довільна) → **Done** → зайдіть в обліковий запис SA → **Keys** → **Add key** → **Create new key** — формат **JSON** (файл з `client_email` та `private_key`).
2. **Спільний доступ** у [Google Drive](https://drive.google.com): на папку (або батьківську) зі сканами **додайте** e-mail service account, показаний у JSON у полі **`client_email`**, з роллю **Переглядач** (read-only; для індексації цього достатньо).
3. У **`.env` на VM** (шлях до ключа, який ви скопіювали/змонтували безпечно):
   ```env
   GOOGLE_DRIVE_SERVICE_ACCOUNT=/шлях/на/сервері/keys/drive-reader-sa.json
   ```
4. **Не** комітьте JSON ключа. На Oracle: покладіть файл в `$HOME/…`, secrets manager або `chmod 600` і **не** у публічні директорії.
5. Якщо одночасно задати `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS` і `GOOGLE_DRIVE_SERVICE_ACCOUNT` — **використовується** service account (для оркестраторів: лишіть тільки SA в `.env` з деплойменту, щоб не плутати).

### Google Drive: OAuth (якщо вистачить API key або 403 на завантаження `alt=media`)

У **тому самому** Google Cloud проєкті, де вже увімкнено **Google Drive API**:

1. **OAuth consent screen** (меню **APIs & Services** → **OAuth consent screen**):
   - **User type**: зазвичай **External** (для особистого зберігання).
   - Заповніть обов’язкові поля (назва застосунку, e-mail власника), збережіть.
   - Поки застосунок **Testing** — у **Test users** **додайте свій Google-акаунт**, яким зайдете в Drive (той самий, з якого відкривається папка). Інакше Google покаже «this app is blocked».
2. **OAuth client ID** (**APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**):
   - **Application type**: **Desktop** (як “Desktop app”).
   - Створіть, потім **Download JSON** (файл вигляду `client_secret_…json`). Зберігайте **поза** репозиторієм.
3. У **`.env`** (абсолютний шлях — надійніше):
   ```env
   GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS=/home/ви/Проекти/secret/client_secret_xxx.json
   GOOGLE_DRIVE_OAUTH_TOKEN=/home/ви/Projects/Indexer/data/drive_oauth_token.json
   ```
   Якщо задано `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS`, **індексатор використає OAuth**, а не `GOOGLE_DRIVE_API_KEY` (ключ можна залишити для тесту або прибрати).
4. **Перший запуск** `indexer` зі вказівкою на **Google Drive URL** — відкриється **браузер**, увійдіть тим **test user**-акаунтом, дозвольте доступ. Файл токена створиться на `GOOGLE_DRIVE_OAUTH_TOKEN`, наступні запуски без браузера, доки дійсний **refresh** token.
5. Репозиторій: додайте `data/*oauth*.json` і `client_secret_*.json` в `.gitignore`, якщо це ще не зроблено.

**Важливо:** акаунт у веб-логіні **має мати** доступ у Drive до цієї папки (роль переглядача достатньо для `drive.readonly`).

## Використання

```bash
python indexer.py DBNAME SOURCE [опції]
```

`DBNAME` — перший обов'язковий аргумент: ім'я або шлях до файлу SQLite. Відносні імена потрапляють у каталог **`data/`** (суфікс `.db` додається, якщо не вказано); абсолютний шлях використовується як є. `SOURCE` — локальний шлях або Google Drive URL.

### Приклади

```bash
# Ліміт (за замовч. вже оброблені пропускаються; для перезапису додайте --rewrite)
python indexer.py lutsk_marriages https://drive.google.com/drive/folders/1IC43A3HaSn-FluEl88PFb9YOSYuYdRVf?usp=drive_link --files "121974535/" --limit 20 --description volyn_darts_marriages

# Вивантаження в Google Drive
python indexer.py lutskyi_rayon_marriages /home/solvek/Projects/VolynRagz/scans/122484190 --limit 20 --description volyn_darts_marriages --model gemini-3-flash-preview


# Локальна папка — всі файли рекурсивно (БД: data/volyn.db)
python indexer.py volyn /mnt/scans

# Конкретний файл
python indexer.py volyn /mnt/scans --files scan_00023.jpg

# Файл у підпапці
python indexer.py volyn /mnt/scans --files "Метрики/scan_00023.jpg"

# Тільки папка "Метрики" (без підпапок)
python indexer.py volyn /mnt/scans --files "Метрики/"

# Папка "Архів" рекурсивно
python indexer.py volyn /mnt/scans --files "Архів/**"

# Google Drive
python indexer.py volyn https://drive.google.com/drive/folders/FOLDER_ID

# З описом контексту для моделі
python indexer.py volyn /mnt/scans --description "Метричні книги Київської губернії, 19 ст."

# Детальні логи
python indexer.py volyn /mnt/scans --verbose
```

### Параметри

| Параметр | Тип | За замовч. | Опис |
|---|---|---|---|
| `dbname` | positional | — | Ім'я або шлях до SQLite; відносні → `data/…` (без розширення додається `.db`) |
| `source` | positional | — | Локальний шлях або Google Drive URL |
| `--files` | optional | всі рекурсивно | Фільтр файлів (див. нижче) |
| `--limit` | optional | без ліміту | Максимум спроб обробки; файли, що лише пропускаються (вже в БД без `--rewrite`), у ліміт не входять |
| `--rewrite` / `--no-rewrite` | optional | `--no-rewrite` | Перезаписувати вже оброблені (`--rewrite`) чи пропускати їх |
| `--description` | optional | — | Додатковий контекст для моделі |
| `--model` | optional | з `.env` | Назва моделі Gemini |
| `--temperature` | optional | `0.1` | Температура 0.0–1.0 |
| `--verbose` | flag | — | Детальні логи |

### Фільтр --files

| Значення | Поведінка |
|---|---|
| _(не задано)_ | Всі файли рекурсивно |
| `scan_001.jpg` | Конкретний файл у корені source |
| `Метрики/scan_001.jpg` | Конкретний файл у підпапці |
| `Метрики/` | Всі файли в папці (без підпапок) |
| `Архів/**` | Всі файли в папці рекурсивно |

## Моніторинг

Лог пишеться одночасно в консоль і у файл `indexer.log` поряд зі скриптом.

```bash
# Стежити за процесом у реальному часі (локально або по SSH)
tail -f indexer.log

# Швидка статистика з бази
sqlite3 data/volyn.db "SELECT COUNT(*) || ' сканів, ' || (SELECT COUNT(*) FROM persons) || ' осіб' FROM scans"
```

## Структура бази даних

Файл SQLite задається першим аргументом `dbname` (відносні шляхи — у `data/`, типово з суфіксом `.db`), каталог і файл створюються автоматично.

```sql
scans:   id, folder, file, number, processed_at
persons: id, scan_id, name, surname, father, yob, location
```

Приклад запиту:
```sql
SELECT p.surname, p.name, p.father, p.yob, p.location, s.file
FROM persons p JOIN scans s ON s.id = p.scan_id
WHERE p.surname LIKE 'Коваленко%'
ORDER BY p.yob;
```

## Підтримувані формати файлів

`.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.webp`
