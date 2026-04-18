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
| `GOOGLE_DRIVE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials → Create API key (потрібно увімкнути Google Drive API). Потрібен тільки для Google Drive. |
| `DEFAULT_MODEL` | Модель за замовчуванням, можна не міняти |

## Використання

```bash
python indexer.py SOURCE [опції]
```

`SOURCE` — перший обов'язковий аргумент: локальний шлях або Google Drive URL.

### Приклади

```bash
# Локальна папка — всі файли рекурсивно
python indexer.py /mnt/scans

# Конкретний файл
python indexer.py /mnt/scans --files scan_00023.jpg

# Файл у підпапці
python indexer.py /mnt/scans --files "Метрики/scan_00023.jpg"

# Тільки папка "Метрики" (без підпапок)
python indexer.py /mnt/scans --files "Метрики/"

# Папка "Архів" рекурсивно
python indexer.py /mnt/scans --files "Архів/**"

# Google Drive
python indexer.py https://drive.google.com/drive/folders/FOLDER_ID

# Ліміт (за замовч. вже оброблені пропускаються; для перезапису додайте --rewrite)
python indexer.py /home/solvek/Projects/VolynRagz/scans/122484190 --limit 2 --description volyn_darts_marriages

# З описом контексту для моделі
python indexer.py /mnt/scans --description "Метричні книги Київської губернії, 19 ст."

# Детальні логи
python indexer.py /mnt/scans --verbose
```

### Параметри

| Параметр | Тип | За замовч. | Опис |
|---|---|---|---|
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
sqlite3 indexer.db "SELECT COUNT(*) || ' сканів, ' || (SELECT COUNT(*) FROM persons) || ' осіб' FROM scans"
```

## Структура бази даних

`indexer.db` — SQLite файл поряд зі скриптом, створюється автоматично.

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
