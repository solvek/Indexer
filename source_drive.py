"""
Джерело файлів: Google Drive: API key, OAuth (з браузером) або service account (сервер без браузера).
"""
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from source import FileEntry, Source, SUPPORTED_EXTENSIONS, normalize_files_filter

OAUTH_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
# Папки на shared drives (Team Drives) без цих полів files.list зазвичай повертає порожній список.
_DRIVE_LIST_FILE_KW: Dict[str, object] = {
    "supportsAllDrives": True,
    "includeItemsFromAllDrives": True,
}
# Коли в імені файла в Drive немає суфікса, MIME з API → суфікс тимчасового файла для Gemini
_DRIVE_MIME_TO_TMP_SUFFIX: Dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "image/x-tiff": ".tif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
_log = logging.getLogger(__name__)


def _folder_column_value(folder_path: str, root_fallback: str) -> str:
    """Останній сегмент відносного шляху папки (для БД/CSV); у корені — root_fallback."""
    if folder_path:
        return folder_path.rsplit("/", 1)[-1]
    return root_fallback


def _tmp_suffix_for_image_mime(mime: Optional[str]) -> str:
    if not mime:
        return ".jpg"
    m = mime.split(";")[0].strip().lower()
    if m in _DRIVE_MIME_TO_TMP_SUFFIX:
        return _DRIVE_MIME_TO_TMP_SUFFIX[m]
    if m.startswith("image/"):
        return ".jpg"
    return ".jpg"


def _parse_drive_error_body(resp: requests.Response) -> str:
    """Короткий опис помилки Google (квота vs права) без логіну ключа в тексті."""
    try:
        j = resp.json()
        err = j.get("error", j) if isinstance(j, dict) else {}
        if not isinstance(err, dict):
            return str(err)[:400]
        out = [err.get("message", "")]
        for e in err.get("errors", []) or []:
            if isinstance(e, dict):
                out.append(" ".join(filter(None, [e.get("reason"), e.get("message")])))
        return " ".join(s for s in out if s).strip()[:500]
    except Exception:
        t = (getattr(resp, "text", None) or "")[:300]
        return t.strip()


def _looks_like_quota_or_rate_limit(detail: str) -> bool:
    d = detail.lower()
    return any(
        x in d
        for x in (
            "quota",
            "rate",
            "usage",
            "limit",
            "ratelimitexceeded",
            "usaglimit",
        )
    )


def _forbidden_media_message(
    file_label: str, auth_mode: str, drive_detail: str = ""
) -> str:
    if _looks_like_quota_or_rate_limit(drive_detail):
        return (
            f"403 на {file_label}: схоже на ліміт/квоту API (текст від Google: {drive_detail[:300]}). "
            "RPS-ліміти зазвичай дають 429, а не 403, але в Drive інколи квоту пишуть і в 403. "
            "Перевірте: Cloud Console → APIs & Services → Google Drive API → Quotas, "
            "а також денні ліміти/білінг. Можна зменшити обсяг (--request-delay) або пізніше повторити."
        )
    if auth_mode == "service_account":
        return (
            f"403 заборона завантаження: {file_label} — service account не має доступу до цього файла. "
            "У веб-інтерфейсі Google Drive: правий клік на папку → «Спільний доступ» — додайте "
            "e-mail сервісного акаунта (у JSON ключа: client_email) з роллю «Переглядач»."
        )
    if auth_mode == "user_oauth":
        return (
            f"403 заборона завантаження: {file_label} — акаунт з OAuth, ймовірно, "
            "не має права на цей файл, або власник вимкнув завантаження."
        )
    return (
        f"403 заборона завантаження: {file_label} — з одним API key далеко не всі "
        "файли папки можна зчитати так само, як з браузера: часто потрібно «доступ за "
        "посиланням» + перегляд, інколи окремо для цього файла. "
        "Спробуйте GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS (той акаунт, що дійсно "
        "бачить цю папку) або виправте в Drive спільний доступ на проблемні файли."
    )


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


def _is_drive_method_blocked_403(e: HttpError) -> bool:
    if e.resp.status != 403:
        return False
    msg = str(e)
    if e.content and isinstance(e.content, bytes):
        try:
            msg = e.content.decode("utf-8", "replace")
        except Exception:
            pass
    return "blocked" in msg.lower()


def _save_oauth_token(creds: Credentials, token_path: str) -> None:
    p = Path(token_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(creds.to_json(), encoding="utf-8")


def _load_oauth_credentials(client_secrets_path: str, token_path: str) -> Credentials:
    creds: Optional[Credentials] = None
    t = Path(token_path)
    if t.is_file():
        creds = Credentials.from_authorized_user_file(str(t), OAUTH_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_oauth_token(creds, token_path)
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, OAUTH_SCOPES)
            creds = flow.run_local_server(port=0)
            _save_oauth_token(creds, token_path)
    return creds


def _load_service_account_creds(key_path: str) -> service_account.Credentials:
    return service_account.Credentials.from_service_account_file(
        key_path, scopes=OAUTH_SCOPES
    )


class DriveSource(Source):
    def __init__(
        self,
        url: str,
        api_key: Optional[str] = None,
        oauth_client_secrets: Optional[str] = None,
        oauth_token_path: Optional[str] = None,
        service_account_path: Optional[str] = None,
    ):
        self.root_folder_id = extract_folder_id(url)
        # id папки для list/get дітей: збігається з URL, або ціль ярлика, якщо в URL id ярлика
        self._browse_root_id: str = self.root_folder_id
        self._creds: Optional[Any] = None
        self._api_key: Optional[str] = None
        self._oauth_token_path: Optional[str] = None
        self._auth_mode: str = "api_key"
        # (folder, file) → drive file id
        self._id_map: Dict[Tuple[str, str], str] = {}
        # Ім'я кореневої папки за URL (для файлів безпосередньо в корені списку)
        self._root_label: str = ""
        # ID спільного диску (Team Drive), якщо коренева папка лежить там; для files.list(corpora=drive)
        self._list_drive_id: Optional[str] = None

        if service_account_path:
            if not os.path.isfile(service_account_path):
                raise ValueError(
                    f"GOOGLE_DRIVE_SERVICE_ACCOUNT: файл ключа не знайдено: {service_account_path}"
                )
            sa = _load_service_account_creds(service_account_path)
            self._creds = sa
            self._auth_mode = "service_account"
            self.service = build("drive", "v3", credentials=sa, cache_discovery=False)
            _log.info(
                "Google Drive: service account (браузер не потрібен). "
                "Додайте в Drive «Спільний доступ» e-mail: %s",
                sa.service_account_email,
            )
        elif oauth_client_secrets:
            if not os.path.isfile(oauth_client_secrets):
                raise ValueError(
                    f"GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS: файл не знайдено: {oauth_client_secrets}"
                )
            if not oauth_token_path:
                raise ValueError(
                    "Для OAuth задайте GOOGLE_DRIVE_OAUTH_TOKEN (куди зберігати access token)"
                )
            _log.info(
                "Google Drive: OAuth2, токен: %s (при потребі відкриється браузер)",
                oauth_token_path,
            )
            self._oauth_token_path = oauth_token_path
            self._creds = _load_oauth_credentials(oauth_client_secrets, oauth_token_path)
            self._auth_mode = "user_oauth"
            self.service = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        elif api_key:
            self._api_key = api_key
            self.service = build("drive", "v3", developerKey=api_key, cache_discovery=False)
            _log.info(
                "Google Drive: тільки API key. "
                "На Oracle/сервер без браузера: GOOGLE_DRIVE_SERVICE_ACCOUNT=шлях/до/SA.json"
            )
        else:
            raise ValueError(
                "Для Google Drive: GOOGLE_DRIVE_SERVICE_ACCOUNT (сервер/Oracle, без браузера), "
                "або GOOGLE_DRIVE_API_KEY, або GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS+GOOGLE_DRIVE_OAUTH_TOKEN."
            )

    # ------------------------------------------------------------------ #
    #  Public interface                                                  #
    # ------------------------------------------------------------------ #

    def _ensure_valid_creds(self) -> None:
        if not self._creds:
            return
        if self._creds.valid:
            return
        if self._auth_mode == "service_account":
            self._creds.refresh(Request())
            return
        if not self._oauth_token_path:
            return
        if self._creds.expired and getattr(self._creds, "refresh_token", None):
            self._creds.refresh(Request())
            _save_oauth_token(self._creds, self._oauth_token_path)
        if not self._creds.valid:
            raise RuntimeError(
                "OAuth2: не вдалось оновити access token, видаліть token-файл і авторизуйтесь знову"
            )

    def _load_root_list_context(self) -> None:
        """Підготовка до _collect: мітка, driveId, і _browse_root_id (ярлик → цільова папка)."""
        self._list_drive_id = None
        self._browse_root_id = self.root_folder_id
        try:
            meta = self.service.files().get(
                fileId=self.root_folder_id,
                fields="name,driveId,mimeType,shortcutDetails",
                supportsAllDrives=True,
            ).execute()
        except HttpError as e:
            if e.resp.status in (403, 404):
                _log.warning(
                    "Drive files.get (id з URL) HTTP %s; ім'я/ driveId невідомі — тимчасовий label=id.",
                    e.resp.status,
                )
                self._root_label = self.root_folder_id
                return
            raise
        mtype = (meta or {}).get("mimeType", "")

        if mtype == SHORTCUT_MIME:
            d = (meta or {}).get("shortcutDetails") or {}
            tid, tm = d.get("targetId"), d.get("targetMimeType")
            if tid and tm == FOLDER_MIME:
                _log.info(
                    "За URL задано ярлик на папку; обхід цільової папки (targetId=%s).", tid
                )
                self._browse_root_id = tid
                try:
                    meta2 = self.service.files().get(
                        fileId=tid, fields="name,driveId", supportsAllDrives=True
                    ).execute()
                except HttpError as e2:
                    if e2.resp.status in (403, 404):
                        _log.warning(
                            "Немає доступу files.get до цільової папки ярлика (HTTP %s). "
                            "Додайте service account до цільової папки, не лише до ярлика.",
                            e2.resp.status,
                        )
                    else:
                        _log.warning("files.get(ціль ярлика): %s", e2)
                    self._root_label = (meta or {}).get("name") or self.root_folder_id
                    return
                self._root_label = (meta2 or {}).get("name") or ""
                did = (meta2 or {}).get("driveId")
                if did:
                    self._list_drive_id = did
                    _log.info("Цільова папка (shared drive), driveId=%s", did)
                return

            if tid and tm and tm != FOLDER_MIME:
                _log.warning(
                    "URL вказує на ярлик на файл, не на папку. Вкажіть посилання на папку/ярлик папки."
                )
            self._root_label = (meta or {}).get("name") or self.root_folder_id
            return

        if mtype and mtype != FOLDER_MIME:
            _log.warning(
                "id з URL не папка (mimeType=%s). Для такого id files.list('… in parents') порожні. Потрібен folder id.",
                mtype,
            )
        self._root_label = (meta or {}).get("name") or ""
        did = (meta or {}).get("driveId")
        if did:
            self._list_drive_id = did
            _log.info("Коренева папка на shared drive, driveId=%s", did)

    @staticmethod
    def _is_indexable_drive_image(name: str, mime: str) -> bool:
        """Скан у списку: стандартні розширення аби будь-яке image/* з Drive, крім svg."""
        if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
            return True
        m = (mime or "").split(";")[0].strip().lower()
        return m.startswith("image/") and "svg" not in m

    def _get_shortcut_target_info(
        self, shortcut_id: str
    ) -> Optional[Tuple[str, str, str]]:
        # (target_id, mime, name); для папки name=""; target_name для файлів — реальна назва цілі.
        try:
            meta = self.service.files().get(
                fileId=shortcut_id, fields="shortcutDetails", supportsAllDrives=True
            ).execute()
        except HttpError:
            return None
        d = (meta or {}).get("shortcutDetails")
        if not d:
            return None
        tid = d.get("targetId")
        tm = d.get("targetMimeType")
        if not tid or not tm:
            return None
        if tm == FOLDER_MIME:
            return (tid, FOLDER_MIME, "")
        try:
            tmeta = self.service.files().get(
                fileId=tid, fields="name", supportsAllDrives=True
            ).execute()
        except HttpError:
            return None
        tname = (tmeta or {}).get("name", "")
        return (tid, tm, tname)

    def list_files(self, files_filter: Optional[str]) -> List[FileEntry]:
        files_filter = normalize_files_filter(files_filter)
        self._id_map.clear()
        self._load_root_list_context()
        raw = self._collect(files_filter)
        if not raw:
            self._log_drive_list_no_images_diagnostic()
            _log.warning(
                "Google Drive: знайдено 0 зображень. Формати, які шукаємо: %s. "
                "Переконайтесь, що service account (або OAuth) має доступ, у папці/підпапках є зображення "
                "(а не лише Google Docs) і, якщо потрібно, що ярлики вказують на ці цільові папки/файли.",
                ", ".join(sorted(SUPPORTED_EXTENSIONS)),
            )
        entries = []
        for r in raw:
            key = (r["folder"], r["name"])
            self._id_map[key] = r["id"]
            entries.append(
                FileEntry(
                    folder=r["folder"],
                    file=r["name"],
                    _drive_id=r["id"],
                    _drive_mime=r.get("mimeType"),
                )
            )
        return sorted(entries, key=lambda e: (e.folder, e.file))

    def _open_drive_media(
        self, file_id: str, acknowledge_abuse: bool
    ) -> requests.Response:
        """Stream GET .../files/{id}?alt=media (так само, як клієнт Drive API)."""
        base = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        # Без supportsAllDrives= тру файли з shared drive часто не віддаються
        params: Dict[str, str] = {"alt": "media", "supportsAllDrives": "true"}
        if acknowledge_abuse:
            params["acknowledgeAbuse"] = "true"
        if self._creds:
            self._ensure_valid_creds()
            assert self._creds is not None
            return requests.get(
                base,
                params=params,
                headers={"Authorization": f"Bearer {self._creds.token}"},
                stream=True,
                timeout=120,
            )
        params["key"] = self._api_key
        return requests.get(base, params=params, stream=True, timeout=120)

    def get_local_path(self, entry: FileEntry) -> str:
        """Завантажує файл у тимчасову директорію і повертає шлях."""
        file_id = entry._drive_id or self._id_map.get((entry.folder, entry.file))
        if not file_id:
            raise RuntimeError(f"Невідомий Drive ID для {entry.folder}/{entry.file}")

        suffix = Path(entry.file).suffix
        if not suffix and getattr(entry, "_drive_mime", None):
            suffix = _tmp_suffix_for_image_mime(entry._drive_mime)
        if not suffix:
            suffix = ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        label = f"{entry.folder + '/' if entry.folder else ''}{entry.file}"
        try:
            for use_ack in (False, True):
                resp = self._open_drive_media(file_id, acknowledge_abuse=use_ack)
                if resp.status_code == 200:
                    with open(tmp.name, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                    entry._local_path = tmp.name
                    return tmp.name
                if resp.status_code == 403 and not use_ack:
                    _log.info(
                        "alt=media повернув 403, повтор з acknowledgeAbuse=true (правила Google для частини файлів)"
                    )
                    continue
                if resp.status_code == 403:
                    detail = _parse_drive_error_body(resp)
                    if detail:
                        _log.warning("Drive API (тіло помилки alt=media): %s", detail)
                    raise RuntimeError(_forbidden_media_message(label, self._auth_mode, detail))
                resp.raise_for_status()
        except Exception:
            if os.path.exists(tmp.name):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            raise

    def cleanup(self, entry: FileEntry):
        """Видаляє тимчасовий файл після обробки."""
        if entry._local_path and os.path.exists(entry._local_path):
            os.unlink(entry._local_path)
            entry._local_path = None

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                  #
    # ------------------------------------------------------------------ #

    def _log_drive_list_no_images_diagnostic(self) -> None:
        """Після 0 зображень: скільки елементів у корені; якщо 0 — коротка підказка по доступу SA."""
        try:
            level1 = self._list_pages(
                f"'{self._browse_root_id}' in parents and trashed=false",
                "nextPageToken, files(name, mimeType, id)",
            )
        except Exception as e:
            _log.warning("Drive: не вдалось прочитати вміст кореневої папки: %s", e)
            return
        sample = [(x.get("name"), x.get("mimeType")) for x in level1[:8]]
        _log.warning(
            "Drive: у корені (1 рівень) елементів: %d. Приклади (ім'я, mimeType): %s",
            len(level1),
            sample,
        )
        if not level1:
            sa_email = ""
            c = self._creds
            if self._auth_mode == "service_account" and c and getattr(c, "service_account_email", None):
                sa_email = c.service_account_email
            try:
                m = self.service.files().get(
                    fileId=self._browse_root_id,
                    fields="capabilities,shared",
                    supportsAllDrives=True,
                ).execute()
                cap = (m or {}).get("capabilities", {}) or {}
                _log.warning(
                    "Drive: list порожній. shared=%s, canListChildren=%s. "
                    "Перевірте «Спільний доступ» для service account (доступ за посиланням для API часто не еквівалентний).%s",
                    (m or {}).get("shared"),
                    cap.get("canListChildren"),
                    f" SA: {sa_email}." if sa_email else "",
                )
            except HttpError as e2:
                _log.warning("Drive: files.get(корінь) HTTP %s — перевірте id і права.", e2.resp.status)
            except Exception as e2:
                _log.debug("Drive: діагностика кореня: %s", e2)

    def _collect(self, files_filter: Optional[str]) -> List[dict]:
        if files_filter is None:
            return self._list_recursive(self._browse_root_id, "")

        if files_filter.endswith("/**"):
            subfolder_name = files_filter[:-3]
            folder_id = self._find_subfolder_id(self._browse_root_id, subfolder_name)
            return self._list_recursive(folder_id, subfolder_name)

        if files_filter.endswith("/"):
            subfolder_name = files_filter.rstrip("/")
            folder_id = self._find_subfolder_id(self._browse_root_id, subfolder_name)
            return self._list_flat(folder_id, subfolder_name)

        # Конкретний файл: може бути "file.jpg" або "Folder/file.jpg"
        parts = Path(files_filter).parts
        filename = parts[-1]
        folder_path = "/".join(parts[:-1]) if len(parts) > 1 else ""

        if folder_path:
            parent_id = self._resolve_folder_path(self._browse_root_id, folder_path)
        else:
            parent_id = self._browse_root_id

        return self._find_file_in_folder(parent_id, filename, folder_path)

    def _list_pages_paginate(self, query: str, fields: str, list_kw: Dict[str, object]) -> List[dict]:
        results: List[dict] = []
        page_token: Optional[str] = None
        while True:
            try:
                req = self.service.files().list(
                    q=query,
                    fields=fields,
                    pageToken=page_token,
                    pageSize=1000,
                    **list_kw,
                )
                resp = req.execute()
            except HttpError as e:
                if _is_drive_method_blocked_403(e) and not self._creds:
                    raise RuntimeError(
                        "Google Drive повернув 403: методи files.list / files.get заборонені "
                        "для вашого GOOGLE_DRIVE_API_KEY. Варіанти: 1) у Cloud Console виправити API restrictions "
                        "ключа; 2) додати GOOGLE_DRIVE_SERVICE_ACCOUNT=шлях/до/JSON service account; "
                        "3) додати GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS=… (запуск з браузером один раз)."
                    ) from e
                raise
            results.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def _list_pages(self, query: str, fields: str) -> List[dict]:
        """Пагінація files.list. Team drive: corpora + driveId; інакше allDrives."""
        d_id = self._list_drive_id
        if d_id:
            list_kw: Dict[str, object] = {
                **_DRIVE_LIST_FILE_KW,
                "corpora": "drive",
                "driveId": d_id,
            }
        else:
            list_kw = {**_DRIVE_LIST_FILE_KW, "corpora": "allDrives"}
        try:
            return self._list_pages_paginate(query, fields, list_kw)
        except HttpError as e:
            if e.resp.status == 400 and not d_id and list_kw.get("corpora") == "allDrives":
                _log.warning(
                    "Drive: files.list (corpora=allDrives) → HTTP 400, повтор без corpora (лише supportsAllDrives+includeAll)"
                )
                return self._list_pages_paginate(query, fields, {**_DRIVE_LIST_FILE_KW})
            raise

    def _list_recursive(self, folder_id: str, folder_path: str) -> List[dict]:
        results = []
        items = self._list_pages(
            f"'{folder_id}' in parents and trashed=false",
            "nextPageToken, files(id, name, mimeType)",
        )
        for item in items:
            m = item.get("mimeType", "")
            if m == FOLDER_MIME:
                sub_path = f"{folder_path}/{item['name']}" if folder_path else item["name"]
                results.extend(self._list_recursive(item["id"], sub_path))
            elif m == SHORTCUT_MIME:
                st = self._get_shortcut_target_info(item["id"])
                if not st:
                    continue
                t_id, t_mime, t_name = st
                if t_mime == FOLDER_MIME:
                    sub_path = f"{folder_path}/{item['name']}" if folder_path else item["name"]
                    results.extend(self._list_recursive(t_id, sub_path))
                elif t_name and self._is_indexable_drive_image(t_name, t_mime):
                    results.append(
                        {
                            "id": t_id,
                            "name": t_name,
                            "folder": _folder_column_value(folder_path, self._root_label),
                            "mimeType": t_mime,
                        }
                    )
            elif self._is_indexable_drive_image(item.get("name", ""), m):
                results.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "folder": _folder_column_value(folder_path, self._root_label),
                        "mimeType": m,
                    }
                )
        return results

    def _list_flat(self, folder_id: str, folder_path: str) -> List[dict]:
        items = self._list_pages(
            f"'{folder_id}' in parents and trashed=false and mimeType != '{FOLDER_MIME}'",
            "nextPageToken, files(id, name, mimeType)",
        )
        return [
            {
                "id": i["id"],
                "name": i["name"],
                "folder": _folder_column_value(folder_path, self._root_label),
                "mimeType": (i.get("mimeType") or ""),
            }
            for i in items
            if self._is_indexable_drive_image(i.get("name", ""), i.get("mimeType", ""))
        ]

    def _find_subfolder_id(self, parent_id: str, name: str) -> str:
        # Екранування лапок у query Drive API
        name_esc = name.replace("'", r"\'")
        items = self._list_pages(
            f"'{parent_id}' in parents and name='{name_esc}' and mimeType='{FOLDER_MIME}' and trashed=false",
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
        fn_esc = filename.replace("'", r"\'")
        items = self._list_pages(
            f"'{folder_id}' in parents and name='{fn_esc}' and trashed=false",
            "files(id, name, mimeType)",
        )
        if not items:
            raise ValueError(f"Файл '{filename}' не знайдено")
        meta = items[0]
        return [
            {
                "id": meta["id"],
                "name": filename,
                "folder": _folder_column_value(folder_path, self._root_label),
                "mimeType": (meta.get("mimeType") or ""),
            }
        ]
