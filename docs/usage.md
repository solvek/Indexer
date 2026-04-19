# Використання

```bash
python indexer.py DBNAME SOURCE [опції]
```

`DBNAME` — перший обов'язковий аргумент: ім'я або шлях до файлу SQLite. Відносні імена потрапляють у каталог **`data/`** (суфікс `.db` додається, якщо не вказано); абсолютний шлях використовується як є. `SOURCE` — локальний шлях або Google Drive URL.

Очікувані документи — скани історичних архівних джерел (метрики, акти, списки тощо) з генеалогічною інформацією. Модель націлена на якісне розпізнавання **прізвищ**; імена, прізвища й локації варто **нормалізувати до сучасної української** за контекстом. **Ім’я батька** береться з контексту або з по батькові (наприклад «Іванович» → «Іван»). Параметр `--description` допомагає задати регіон і тип книги, щоб краще орієнтувати модель.

## Приклади

```bash
# Ліміт (за замовч. вже оброблені пропускаються; для перезапису додайте --rewrite)
python indexer.py lutsk_marriages 'https://drive.google.com/drive/folders/1IC43A3HaSn-FluEl88PFb9YOSYuYdRVf?usp=drive_link' --limit 20 --description volyn_darts_marriages

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

## Параметри

| Параметр | Тип | За замовч. | Опис |
|---|---|---|---|
| `dbname` | positional | — | Ім'я або шлях до SQLite; відносні → `data/…` (без розширення додається `.db`) |
| `source` | positional | — | Локальний шлях або Google Drive URL |
| `--files` | optional | всі рекурсивно | Фільтр файлів (див. нижче) |
| `--limit` | optional | без ліміту | Максимум спроб обробки; файли, що лише пропускаються (вже в БД без `--rewrite`), у ліміт не входять |
| `--rewrite` / `--no-rewrite` | optional | `--no-rewrite` | Перезаписувати вже оброблені (`--rewrite`) чи пропускати їх. При **`--rewrite`** для відомого скану спочатку видаляються старі записи про людей для цього скану (каскадно), потім зберігаються нові |
| `--description` | optional | — | Додатковий контекст для моделі |
| `--model` | optional | з `.env` | Назва моделі Gemini |
| `--temperature` | optional | `0.1` | Температура 0.0–1.0 |
| `--verbose` | flag | — | Детальні логи |
| `--csv` | flag | — | Допис у `out/<ім'я_бд>.csv` рядків зі сканів цього запуску; повна поведінка — у [експорті CSV](csv-export.md) |

## Фільтр --files

| Значення | Поведінка |
|---|---|
| _(не задано)_ | Всі файли рекурсивно |
| `scan_001.jpg` | Конкретний файл у корені source |
| `Метрики/scan_001.jpg` | Конкретний файл у підпапці |
| `Метрики/` | Всі файли в папці (без підпапок) |
| `Архів/**` | Всі файли в папці рекурсивно |
