"""
Джерело файлів: локальна файлова система.
"""
from pathlib import Path
from typing import List, Optional

from source import FileEntry, Source, SUPPORTED_EXTENSIONS, normalize_files_filter


class LocalSource(Source):
    def __init__(self, base_path: str):
        self.base = Path(base_path).resolve()
        if not self.base.exists():
            raise ValueError(f"Шлях не існує: {self.base}")
        if not self.base.is_dir():
            raise ValueError(f"Очікується папка, а не файл: {self.base}")

    def list_files(self, files_filter: Optional[str]) -> List[FileEntry]:
        raw_paths = self._resolve_paths(files_filter)
        entries = []
        for p in raw_paths:
            if not p.is_file():
                continue
            if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            rel = p.relative_to(self.base)
            folder = str(rel.parent) if str(rel.parent) != "." else ""
            if not folder:
                folder = self.base.name
            entries.append(
                FileEntry(folder=folder, file=p.name, _local_path=str(p))
            )
        return sorted(entries, key=lambda e: (e.folder, e.file))

    def _resolve_paths(self, files_filter: Optional[str]):
        files_filter = normalize_files_filter(files_filter)
        if files_filter is None:
            # Усі підпапки, усі підтримувані файли рекурсивно (за замовчанням, коли фільтр не задано)
            return self.base.rglob("*")

        if files_filter.endswith("/**"):
            # Рекурсивно з підпапки
            subfolder = files_filter[:-3]
            target = self.base / subfolder
            if not target.exists():
                raise ValueError(f"Папка не існує: {target}")
            return target.rglob("*")

        if files_filter.endswith("/"):
            # Тільки верхній рівень папки
            subfolder = files_filter.rstrip("/")
            target = self.base / subfolder
            if not target.exists():
                raise ValueError(f"Папка не існує: {target}")
            return target.glob("*")

        # Конкретний файл (може містити підпапку: "Метрики/scan_001.jpg")
        target = self.base / files_filter
        if not target.exists():
            raise ValueError(f"Файл не знайдено: {target}")
        return [target]

    def get_local_path(self, entry: FileEntry) -> str:
        return entry._local_path

    def cleanup(self, entry: FileEntry):
        pass  # локальні файли не видаляємо
