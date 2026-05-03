#!/usr/bin/env python3
"""
Індексатор - автоматична обробка сканів архівних документів через ШІ.

Використання:
  python indexer.py DBNAME /path/to/scans [опції]
  python indexer.py DBNAME https://drive.google.com/drive/folders/ID [опції]
"""
import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

import db
import processor
from source import create_source, normalize_files_filter


# ------------------------------------------------------------------ #
#  Логування                                                          #
# ------------------------------------------------------------------ #

def setup_logging(verbose: bool = False, log_file: str = "indexer.log"):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=level, format=fmt, datefmt=date_fmt, handlers=handlers)


# ------------------------------------------------------------------ #
#  Основна логіка                                                     #
# ------------------------------------------------------------------ #

_CSV_HEADER = ["Папка", "Файл", "Скан", "Прізвище", "Ім'я", "Рік народження"]

# Скільки проходів загалом (перший — з CLI; наступні при помилках — лише «не в БД», без ліміту).
_MAX_INDEX_PASSES = 10


def _yob_from_person_meta(person: dict):
    """Рік народження з persons.meta (dict або JSON-рядок)."""
    m = person.get("meta")
    if m is None:
        return None
    if isinstance(m, dict):
        return m.get("yob")
    if isinstance(m, str):
        try:
            return json.loads(m).get("yob")
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _csv_rows_for_scan(folder: str, file: str, number, persons: list) -> list:
    """Один рядок на особу; скан без осіб — один рядок з порожніми полями особи."""
    n = "" if number is None else number
    if not persons:
        return [[folder, file, n, "", "", ""]]
    rows = []
    for p in persons:
        yob = _yob_from_person_meta(p)
        rows.append(
            [
                folder,
                file,
                n,
                p.get("surname") or "",
                p.get("name") or "",
                "" if yob is None else yob,
            ]
        )
    return rows


def _append_csv_rows(path: Path, rows: list) -> None:
    """Додає рядки в кінець файлу; заголовок — лише якщо файл новий або порожній."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(_CSV_HEADER)
        w.writerows(rows)


def _run_index_pass(
    entries: list,
    source: Any,
    args: argparse.Namespace,
    *,
    rewrite: bool,
    limit: Optional[int],
    log: logging.Logger,
) -> tuple[int, int, int, list]:
    """
    Один прохід по списку entries. Повертає (processed, skipped, errors, csv_new_rows).
    """
    processed = skipped = errors = 0
    work_num = 0
    csv_new_rows: list = []
    total_found = len(entries)

    if total_found == 0:
        return processed, skipped, errors, csv_new_rows

    for i, entry in enumerate(entries, 1):
        rel = f"{entry.folder + '/' if entry.folder else ''}{entry.file}"

        already_done = db.is_processed(entry.folder, entry.file)
        if already_done and not rewrite:
            log.info(f"ПРОПУСК (вже оброблено): [{i}/{total_found}] {rel}")
            skipped += 1
            continue

        if limit is not None and work_num >= limit:
            break

        work_num += 1
        label = (
            f"[{work_num}/{limit}] {rel}"
            if limit is not None
            else f"[{i}/{total_found}] {rel}"
        )

        if already_done and rewrite:
            db.delete_scan(entry.folder, entry.file)
            log.debug(f"Видалено попередній запис: {label}")

        log.info(f"Обробка: {label}")

        local_path = None
        try:
            local_path = source.get_local_path(entry)
            number = processor.extract_number(entry.file)
            persons, scan_meta = processor.process_image(
                local_path, args.model, args.temperature, args.extended_prompt
            )
            db.save_scan(entry.folder, entry.file, number, persons, scan_meta)
            if args.csv:
                csv_new_rows.extend(
                    _csv_rows_for_scan(entry.folder, entry.file, number, persons)
                )

            names_preview = ", ".join(
                f"{p.get('surname', '?')} {p.get('name', '')}".strip()
                for p in persons[:3]
            )
            if len(persons) > 3:
                names_preview += f" ... (+{len(persons) - 3})"

            log.info(f"  → {len(persons)} осіб: {names_preview or '—'}")
            processed += 1

        except Exception as e:
            log.error(f"  ПОМИЛКА: {e}")
            if args.verbose:
                log.exception("Деталі помилки:")
            errors += 1

        finally:
            if local_path:
                source.cleanup(entry)

        if args.request_delay > 0:
            time.sleep(args.request_delay)

    return processed, skipped, errors, csv_new_rows


def _sqlite_db_path(s: str) -> Path:
    """Ім'я/шлях до БД; відносні шляхи (крім уже під data/) — у каталозі data/; без розширення — .db"""
    raw = s.strip()
    if not raw:
        raise argparse.ArgumentTypeError("dbname не може бути порожнім")
    p = Path(raw)
    if p.suffix == "":
        p = p.with_suffix(".db")
    if not p.is_absolute() and (not p.parts or p.parts[0] != "data"):
        p = Path("data") / p
    return p


def main():
    default_model = os.environ.get("DEFAULT_MODEL", "gemini-2.0-flash-lite")

    parser = argparse.ArgumentParser(
        prog="indexer",
        description="Автоматичне витягування генеалогічних даних зі сканів архівних документів",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Приклади:
  # Локальна папка, всі файли рекурсивно
  python indexer.py volyn /mnt/scans

  # Конкретний файл
  python indexer.py volyn /mnt/scans --files scan_00023.jpg

  # Підпапка (не рекурсивно)
  python indexer.py volyn /mnt/scans --files "Метрики/"

  # Підпапка рекурсивно
  python indexer.py volyn /mnt/scans --files "Архів/**"

  # Google Drive: обробити до 10 нових файлів (уже в БД не входять у ліміт; за замовч. без перезапису)
  python indexer.py volyn https://drive.google.com/drive/folders/ID --limit 10

  # Примусово перезаписати вже проіндексовані скани
  python indexer.py volyn /mnt/scans --rewrite

  # Розширений промпт: довільний текст для моделі
  python indexer.py volyn /mnt/scans --extended-prompt "Метричні книги Київської губернії, 19 ст."

  # Розширений промпт з файлу prompts/<ім'я>.txt (без розширення в аргументі)
  python indexer.py volyn /mnt/scans --extended-prompt volyn_darts_marriages
        """,
    )

    parser.add_argument(
        "dbname",
        type=_sqlite_db_path,
        help=(
            "Ім'я або шлях до SQLite; відносні імена зберігаються в data/ "
            "(якщо без розширення — додається .db; абсолютний шлях — без змін)"
        ),
    )
    parser.add_argument(
        "source",
        help="Google Drive URL або локальний шлях до папки зі сканами",
    )
    parser.add_argument(
        "--files", default=None, metavar="FILTER",
        help='"file.jpg" | "Folder/" | "Folder/**" | без аргументу = всі рекурсивно',
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Не більше N спроб обробки (файли, що пропускаються як вже в БД, не рахуються)",
    )
    parser.add_argument(
        "--rewrite", action=argparse.BooleanOptionalAction, default=False,
        help="Перезаписувати вже оброблені скани (за замовч.: --no-rewrite)",
    )
    parser.add_argument(
        "--extended-prompt",
        "--description",
        dest="extended_prompt",
        default=None,
        help=(
            "Розширений промпт (необов'язково): довільний рядок АБО ім'я файлу без .txt "
            "з каталогу prompts/ (лише латиниця, цифри, _ та -), наприклад volyn_darts_marriages. "
            "--description — застарілий псевдонім, збережено для сумісності"
        ),
    )
    parser.add_argument(
        "--model", default=default_model,
        help=f"Назва моделі Gemini (default: {default_model})",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.1,
        help="Температура моделі 0.0–1.0 (default: 0.1)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Детальні логи (debug рівень)",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        metavar="SEC",
        help=(
            "Пауза після кожного звернення до моделі (секунд). Знижує ризик 429/503 "
            "при серії сканів; 0 = без паузи (default: 0)"
        ),
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help=(
            "Додати у out/<ім'я_бд>.csv лише рядки зі сканів, успішно оброблених у цьому запуску "
            "(допис у кінець; заголовок — якщо файлу ще не було)"
        ),
    )

    args = parser.parse_args()
    if args.request_delay < 0:
        parser.error("--request-delay має бути >= 0")

    args.files = normalize_files_filter(args.files)

    setup_logging(args.verbose)
    log = logging.getLogger("indexer")

    # Ключі API
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        log.error("GEMINI_API_KEY не задано у .env")
        sys.exit(1)
    processor.init_client(gemini_key)

    drive_key = os.environ.get("GOOGLE_DRIVE_API_KEY", "").strip() or None
    drive_sa = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT", "").strip() or None
    drive_oauth_secrets = os.environ.get("GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS", "").strip() or None
    drive_oauth_token = os.environ.get("GOOGLE_DRIVE_OAUTH_TOKEN", "").strip() or None
    if drive_oauth_secrets and not drive_oauth_token:
        drive_oauth_token = "data/drive_oauth_token.json"

    # Ініціалізація бази
    db.set_database(args.dbname)
    db.init_db()
    log.info(f"База даних: {db.DB_FILE.resolve()}")

    # Джерело файлів
    try:
        source = create_source(
            args.source,
            drive_key,
            drive_oauth_secrets,
            drive_oauth_token,
            drive_sa,
        )
    except ValueError as e:
        log.error(f"Помилка джерела: {e}")
        sys.exit(1)

    log.info(f"Джерело: {args.source}")
    log.info(f"Фільтр файлів: {args.files or '(всі рекурсивно)'}")
    log.info(f"Модель: {args.model}, температура: {args.temperature}")
    if args.request_delay > 0:
        log.info(f"Пауза між запитами: {args.request_delay} с")

    # Список файлів
    try:
        entries = source.list_files(args.files)
    except (ValueError, RuntimeError) as e:
        log.error(f"Не вдалось отримати список файлів: {e}")
        sys.exit(1)

    total_found = len(entries)
    log.info(
        f"Знайдено файлів: {total_found}"
        + (
            f", ліміт обробки цього запуску: {args.limit} (пропуски «вже в БД» не рахуються)"
            if args.limit is not None
            else ""
        )
    )

    processed = skipped = errors = 0
    csv_new_rows: list = []

    if total_found == 0:
        log.warning("Немає файлів для обробки.")
    else:
        current_entries = entries
        for pass_num in range(1, _MAX_INDEX_PASSES + 1):
            if pass_num > 1:
                log.info(
                    f"Прохід {pass_num}/{_MAX_INDEX_PASSES}: після попереднього залишились "
                    "помилки — пропуск вже успішно оброблених сканів, ліміт знято"
                )
                try:
                    current_entries = source.list_files(args.files)
                except (ValueError, RuntimeError) as e:
                    log.error(
                        f"Не вдалось отримати список файлів для проходу {pass_num}: {e}"
                    )
                    break

            p, s, e, rows = _run_index_pass(
                current_entries,
                source,
                args,
                rewrite=args.rewrite if pass_num == 1 else False,
                limit=args.limit if pass_num == 1 else None,
                log=log,
            )
            processed += p
            skipped += s
            errors = e
            csv_new_rows.extend(rows)

            if pass_num > 1:
                log.info(
                    f"Прохід {pass_num}: оброблено +{p}, пропущено {s}, помилок {e}"
                )

            if e == 0:
                break
            if pass_num == _MAX_INDEX_PASSES:
                log.warning(
                    f"Після {_MAX_INDEX_PASSES} проходів залишились помилки ({e}); "
                    "перезапустіть індексатор пізніше або перевірте проблемні файли."
                )

    log.info(
        f"\n{'='*50}\n"
        f"  Готово!\n"
        f"  Оброблено:  {processed}\n"
        f"  Пропущено:  {skipped}\n"
        f"  Помилок:    {errors}\n"
        f"{'='*50}"
    )

    if args.csv:
        csv_path = Path("out") / f"{args.dbname.stem}.csv"
        if csv_new_rows:
            _append_csv_rows(csv_path, csv_new_rows)
            log.info(
                f"CSV: додано {len(csv_new_rows)} рядків → {csv_path.resolve()}"
            )
        else:
            log.info("CSV: нових успішних сканів немає, файл не змінювався")


if __name__ == "__main__":
    main()
