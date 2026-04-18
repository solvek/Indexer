#!/usr/bin/env python3
"""
Індексатор - автоматична обробка сканів архівних документів через ШІ.

Використання:
  python indexer.py /path/to/scans [опції]
  python indexer.py https://drive.google.com/drive/folders/ID [опції]
"""
import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

import db
import processor
from source import create_source


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

def main():
    default_model = os.environ.get("DEFAULT_MODEL", "gemini-2.0-flash-lite")

    parser = argparse.ArgumentParser(
        prog="indexer",
        description="Автоматичне витягування генеалогічних даних зі сканів архівних документів",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Приклади:
  # Локальна папка, всі файли рекурсивно
  python indexer.py /mnt/scans

  # Конкретний файл
  python indexer.py /mnt/scans --files scan_00023.jpg

  # Підпапка (не рекурсивно)
  python indexer.py /mnt/scans --files "Метрики/"

  # Підпапка рекурсивно
  python indexer.py /mnt/scans --files "Архів/**"

  # Google Drive: обробити до 10 нових файлів (уже в БД не входять у ліміт; за замовч. без перезапису)
  python indexer.py https://drive.google.com/drive/folders/ID --limit 10

  # Примусово перезаписати вже проіндексовані скани
  python indexer.py /mnt/scans --rewrite

  # Довільний опис для моделі (як раніше)
  python indexer.py /mnt/scans --description "Метричні книги Київської губернії, 19 ст."

  # Специфіка запуску з файлу prompts/<ім'я>.txt (без розширення в аргументі)
  python indexer.py /mnt/scans --description volyn_darts_marriages
        """,
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
        "--description", default=None,
        help=(
            "Контекст для моделі: довільний рядок АБО ім'я файлу без .txt з каталогу "
            "prompts/ (лише латиниця, цифри, _ та -), наприклад volyn_darts_marriages"
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

    args = parser.parse_args()
    if args.request_delay < 0:
        parser.error("--request-delay має бути >= 0")
    setup_logging(args.verbose)
    log = logging.getLogger("indexer")

    # Ключі API
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        log.error("GEMINI_API_KEY не задано у .env")
        sys.exit(1)
    processor.init_client(gemini_key)

    drive_key = os.environ.get("GOOGLE_DRIVE_API_KEY", "").strip() or None

    # Ініціалізація бази
    db.init_db()
    log.info(f"База даних: {db.DB_FILE.resolve()}")

    # Джерело файлів
    try:
        source = create_source(args.source, drive_key)
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
    except ValueError as e:
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

    if total_found == 0:
        log.warning("Немає файлів для обробки.")
        return

    processed = skipped = errors = 0
    work_num = 0  # скільки файлів реально пішли в обробку (не пропуск)

    for i, entry in enumerate(entries, 1):
        rel = f"{entry.folder + '/' if entry.folder else ''}{entry.file}"

        already_done = db.is_processed(entry.folder, entry.file)
        if already_done and not args.rewrite:
            log.info(f"ПРОПУСК (вже оброблено): [{i}/{total_found}] {rel}")
            skipped += 1
            continue

        if args.limit is not None and work_num >= args.limit:
            break

        work_num += 1
        label = (
            f"[{work_num}/{args.limit}] {rel}"
            if args.limit is not None
            else f"[{i}/{total_found}] {rel}"
        )

        if already_done and args.rewrite:
            db.delete_scan(entry.folder, entry.file)
            log.debug(f"Видалено попередній запис: {label}")

        log.info(f"Обробка: {label}")

        local_path = None
        try:
            local_path = source.get_local_path(entry)
            number = processor.extract_number(entry.file)
            persons = processor.process_image(
                local_path, args.model, args.temperature, args.description
            )
            db.save_scan(entry.folder, entry.file, number, persons)

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

    log.info(
        f"\n{'='*50}\n"
        f"  Готово!\n"
        f"  Оброблено:  {processed}\n"
        f"  Пропущено:  {skipped}\n"
        f"  Помилок:    {errors}\n"
        f"{'='*50}"
    )


if __name__ == "__main__":
    main()
