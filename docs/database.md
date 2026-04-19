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
- **`meta`** — опційно: JSON-текст про весь скан. Якщо **не** задано `--extended-prompt`, базовий промпт просить модель покласти сюди дату документа: зазвичай `document_year` (рік, мінімум) і за потреби `document_date` (календарна дата **ISO 8601**, `YYYY-MM-DD`). За наявності розширеного промпта набір ключів задає він (або лишається порожнім).

**`persons`**

- **`surname`**, **`name`** — з контексту документа.
- **`meta`** — JSON-текст. У режимі **без** розширеного промпта базовий шаблон очікує в `meta` людини принаймні: `father`, `location`, `yob` (рік), `birth_date` (дата народження рядком **ISO 8601** `YYYY-MM-DD`, якщо відома повністю); з розширеним промптом додаються або змінюються ключі згідно з інструкціями.

Приклад запиту:

```sql
SELECT s.folder, s.number, p.surname, p.name,
       json_extract(p.meta, '$.yob') AS yob,
       json_extract(p.meta, '$.father') AS father,
       json_extract(p.meta, '$.location') AS location,
       json_extract(p.meta, '$.birth_date') AS birth_date,
       json_extract(s.meta, '$.document_year') AS document_year,
       s.file
FROM persons p JOIN scans s ON s.id = p.scan_id
ORDER BY p.surname;
```

Сортування за роком народження з `meta`: `ORDER BY CAST(json_extract(p.meta, '$.yob') AS INTEGER)`.
