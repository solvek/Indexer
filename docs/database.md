# Структура бази даних

Файл SQLite задається першим аргументом `dbname` (відносні шляхи — у `data/`, типово з суфіксом `.db`), каталог і файл створюються автоматично.

```sql
scans:   id, folder, file, number, processed_at
persons: id, scan_id, name, surname, father, yob, location
```

## Семантика полів

**`scans`**

- **`number`** — опційно: намагається витягнутися з імені файлу (евристика `processor.extract_number()`: останній числовий блок у stem; у БД `INTEGER`, ведучі нулі не зберігаються). Якщо не вдалося — `NULL`.
- **`processed_at`** — час обробки скану (ISO 8601, UTC).

**`persons`**

- **`surname`**, **`name`**, **`father`**, **`yob`**, **`location`** — генеалогічні поля з контексту документа.

Приклад запиту:

```sql
SELECT p.surname, p.name, p.father, p.yob, p.location, s.file
FROM persons p JOIN scans s ON s.id = p.scan_id
WHERE p.surname LIKE 'Коваленко%'
ORDER BY p.yob;
```
