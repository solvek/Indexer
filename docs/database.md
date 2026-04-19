# Структура бази даних

Файл SQLite задається першим аргументом `dbname` (відносні шляхи — у `data/`, типово з суфіксом `.db`), каталог і файл створюються автоматично.

```sql
scans:   id, folder, file, number, processed_at, meta
persons: id, scan_id, surname, name, meta
```

## Семантика полів

**`scans`**

- **`number`** — опційно: намагається витягнутися з імені файлу (евристика `processor.extract_number()`: останній числовий блок у stem; у БД `INTEGER`, ведучі нулі не зберігаються). Якщо не вдалося — `NULL`.
- **`processed_at`** — час обробки скану (ISO 8601, UTC).
- **`meta`** — опційно: JSON-текст з додатковими відомостями про весь скан (заповнення залежить від промпта та майбутніх змін коду).

**`persons`**

- **`surname`**, **`name`** — з контексту документа.
- **`meta`** — JSON-текст; типово містить ключі `father`, `yob`, `location` (ім’я батька, рік народження, місцевість), якщо їх описано в промпті й модель їх повернула.

Приклад запиту:

```sql
SELECT s.folder, s.number, p.surname, p.name,
       json_extract(p.meta, '$.yob') AS yob,
       json_extract(p.meta, '$.father') AS father,
       json_extract(p.meta, '$.location') AS location,
       s.file
FROM persons p JOIN scans s ON s.id = p.scan_id
ORDER BY p.surname;
```

Сортування за роком народження з `meta`: `ORDER BY CAST(json_extract(p.meta, '$.yob') AS INTEGER)`.
