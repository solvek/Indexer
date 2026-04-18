"""
Джерело файлів: Google Drive (публічні папки через API key).
"""
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from googleapiclient.discovery import build

from source import FileEntry, Source, SUPPORTED_EXTENSIONS

# Типи MIME які відповідають зображенням
IMAGE_MIMES = {
    "image/jpeg", "image/png", "image/tiff", "image/webp",
}
FOLDER_MIME = "application/vnd.google-apps.folder"


def extract_folder_id(url: str) -> str:
    """Витягує folder ID з URL Google Drive."""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    # Формат: ?id=FOLDER_ID
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    raise ValueError(f"Не вдалось витягнути folder ID з URL: {url}")


class DriveSource(Source):
    def __init__(self, url: str, api_key: Optional[str]):
        if not api_key:
            raise ValueError(
                "GOOGLE_DRIVE_API_KEY не задано у .env — потрібен для Google Drive"
            )
        self.root_folder_id = extract_folder_id(url)
        self.api_key = api_key
        self.service = build("drive", "v3", developerKey=api_key)
        # (folder, file) → drive file id
        self._id_map: Dict[Tuple[str, str], str] = {}
        # Ім'я кореневої папки за URL (для файлів безпосередньо в корені списку)
        self._root_label: str = ""

    # ------------------------------------------------------------------ #
    #  Public interface                                                     #
    # ------------------------------------------------------------------ #

    def list_files(self, files_filter: Optional[str]) -> List[FileEntry]:
        self._id_map.clear()
        self._root_label = self._get_folder_display_name(self.root_folder_id)
        raw = self._collect(files_filter)
        entries = []
        for r in raw:
            key = (r["folder"], r["name"])
            self._id_map[key] = r["id"]
            entries.append(FileEntry(folder=r["folder"], file=r["name"], _drive_id=r["id"]))
        return sorted(entries, key=lambda e: (e.folder, e.file))

    def get_local_path(self, entry: FileEntry) -> str:
        """Завантажує файл у тимчасову директорію і повертає шлях."""
        file_id = self._id_map.get((entry.folder, entry.file)) or entry._drive_id
        if not file_id:
            raise RuntimeError(f"Невідомий Drive ID для {entry.folder}/{entry.file}")

        suffix = Path(entry.file).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()

        # Використовуємо простий HTTP-запит (швидше ніж MediaIoBaseDownload для зображень)
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={self.api_key}"
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)

        entry._local_path = tmp.name
        return tmp.name

    def cleanup(self, entry: FileEntry):
        """Видаляє тимчасовий файл після обробки."""
        if entry._local_path and os.path.exists(entry._local_path):
            os.unlink(entry._local_path)
            entry._local_path = None

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _collect(self, files_filter: Optional[str]) -> List[dict]:
        if files_filter is None:
            return self._list_recursive(self.root_folder_id, "")

        if files_filter.endswith("/**"):
            subfolder_name = files_filter[:-3]
            folder_id = self._find_subfolder_id(self.root_folder_id, subfolder_name)
            return self._list_recursive(folder_id, subfolder_name)

        if files_filter.endswith("/"):
            subfolder_name = files_filter.rstrip("/")
            folder_id = self._find_subfolder_id(self.root_folder_id, subfolder_name)
            return self._list_flat(folder_id, subfolder_name)

        # Конкретний файл: може бути "file.jpg" або "Folder/file.jpg"
        parts = Path(files_filter).parts
        filename = parts[-1]
        folder_path = "/".join(parts[:-1]) if len(parts) > 1 else ""

        if folder_path:
            parent_id = self._resolve_folder_path(self.root_folder_id, folder_path)
        else:
            parent_id = self.root_folder_id

        return self._find_file_in_folder(parent_id, filename, folder_path)

    def _list_pages(self, query: str, fields: str) -> List[dict]:
        """Обходить пагінацію Drive API."""
        results = []
        page_token = None
        while True:
            resp = (
                self.service.files()
                .list(q=query, fields=fields, pageToken=page_token, pageSize=1000)
                .execute()
            )
            results.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def _list_recursive(self, folder_id: str, folder_path: str) -> List[dict]:
        results = []
        items = self._list_pages(
            f"'{folder_id}' in parents and trashed=false",
            "nextPageToken, files(id, name, mimeType)",
        )
        for item in items:
            if item["mimeType"] == FOLDER_MIME:
                sub_path = f"{folder_path}/{item['name']}" if folder_path else item["name"]
                results.extend(self._list_recursive(item["id"], sub_path))
            elif Path(item["name"]).suffix.lower() in SUPPORTED_EXTENSIONS:
                results.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "folder": folder_path or self._root_label,
                    }
                )
        return results

    def _list_flat(self, folder_id: str, folder_path: str) -> List[dict]:
        items = self._list_pages(
            f"'{folder_id}' in parents and trashed=false and mimeType != '{FOLDER_MIME}'",
            "nextPageToken, files(id, name, mimeType)",
        )
        return [
            {"id": i["id"], "name": i["name"], "folder": folder_path}
            for i in items
            if Path(i["name"]).suffix.lower() in SUPPORTED_EXTENSIONS
        ]

    def _find_subfolder_id(self, parent_id: str, name: str) -> str:
        items = self._list_pages(
            f"'{parent_id}' in parents and name='{name}' and mimeType='{FOLDER_MIME}' and trashed=false",
            "files(id, name)",
        )
        if not items:
            raise ValueError(f"Папку '{name}' не знайдено у Drive")
        return items[0]["id"]

    def _resolve_folder_path(self, root_id: str, path: str) -> str:
        """Рекурсивно знаходить ID папки за відносним шляхом."""
        current_id = root_id
        for part in Path(path).parts:
            current_id = self._find_subfolder_id(current_id, part)
        return current_id

    def _find_file_in_folder(self, folder_id: str, filename: str, folder_path: str) -> List[dict]:
        items = self._list_pages(
            f"'{folder_id}' in parents and name='{filename}' and trashed=false",
            "files(id, name)",
        )
        if not items:
            raise ValueError(f"Файл '{filename}' не знайдено")
        return [
            {"id": items[0]["id"], "name": filename, "folder": folder_path or self._root_label}
        ]

    def _get_folder_display_name(self, folder_id: str) -> str:
        """Ім'я папки в Drive за fileId (для колонки scans.folder)."""
        meta = self.service.files().get(fileId=folder_id, fields="name").execute()
        return meta.get("name") or ""
