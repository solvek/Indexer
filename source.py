"""
Абстракція джерела файлів. Підтримує локальну файлову систему і Google Drive.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


@dataclass
class FileEntry:
    folder: str           # відносний шлях від кореня джерела, "" якщо корінь
    file: str             # ім'я файлу
    _local_path: Optional[str] = field(default=None, repr=False)  # для локального джерела
    _drive_id: Optional[str] = field(default=None, repr=False)    # для Google Drive


class Source(ABC):
    @abstractmethod
    def list_files(self, files_filter: Optional[str]) -> List[FileEntry]:
        """Повертає список файлів за фільтром.

        files_filter варіанти:
          None            → всі файли рекурсивно (за замовчуванням)
          "scan_001.jpg"  → конкретний файл (або відносний шлях: "Folder/scan.jpg")
          "FolderName/"   → всі файли у папці (не рекурсивно)
          "FolderName/**" → всі файли у папці рекурсивно
        """
        ...

    @abstractmethod
    def get_local_path(self, entry: FileEntry) -> str:
        """Повертає локальний шлях до файлу (завантажує якщо потрібно)."""
        ...

    @abstractmethod
    def cleanup(self, entry: FileEntry):
        """Очищає тимчасові файли після обробки."""
        ...


def create_source(
    source_str: str,
    drive_api_key: Optional[str] = None,
    drive_oauth_client_secrets: Optional[str] = None,
    drive_oauth_token_path: Optional[str] = None,
    drive_service_account: Optional[str] = None,
) -> Source:
    """Фабрика: визначає тип джерела за рядком."""
    if source_str.startswith("http://") or source_str.startswith("https://"):
        from source_drive import DriveSource
        return DriveSource(
            source_str,
            drive_api_key,
            drive_oauth_client_secrets,
            drive_oauth_token_path,
            drive_service_account,
        )
    else:
        from source_local import LocalSource
        return LocalSource(source_str)
