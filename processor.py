"""
Обробка зображень через Gemini: витягування інформації про людей.
"""
import errno
import json
import logging
import random
import re
import socket
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_BASE_PROMPT_FILE = _PROMPTS_DIR / "base_prompt.txt"
_prompt_template_cache: Optional[str] = None

# Без --extended-prompt: один об'єкт scan + persons; з розширеним — зазвичай масив осіб (див. текст).
_JSON_SHAPE_DEFAULT = """
Формат відповіді (розширений промпт не заданий — працюєш у цьому режимі):

Поверни один JSON-об'єкт (не масив на верхньому рівні):
{
  "scan": {
    "document_year": <ціле число або null> — календарний рік документа (дата акту, запису, метрики тощо),
                 якщо його видно в тексті; мінімально важливо витягнути рік, коли це можливо;
    "document_date": <рядок або null> — дата документа лише **ISO 8601** (YYYY-MM-DD), якщо є в тексті точніше, ніж лише рік; інакше null і використовуй document_year
  },
  "persons": [
    {
      "surname": "...",
      "name": "...",
      "father": "... або null — ім'я батька з по батькові або контексту (див. правила нижче)",
      "location": "... або null — місцевість, сучасна українська",
      "yob": <ціле або null> — рік народження особи, лише якщо явно з тексту",
      "birth_date": <рядок або null> — дата народження лише **ISO 8601** (YYYY-MM-DD), якщо зазначена повністю; якщо відомий лише рік — null тут і yob з роком
    }
  ]
}

Правила для полів людини (у масиві persons):
  "surname"  — прізвище, сучасна українська (найважливіше!)
  "name"     — ім'я, сучасна українська
  "father"   — ім'я батька з по батькові або контексту; приклади: "Іванович" → "Іван", "Петрівна" → "Петро"; якщо невідомо — null

Якщо рік або дату документа встановити неможливо — у scan став null. Не вигадуй дати.

Приклад:
{"scan": {"document_year": 1912, "document_date": "1912-08-14"}, "persons": [{"surname": "Коваленко", "name": "Іван", "father": "Петро", "yob": 1854, "birth_date": "1854-06-12", "location": "Київ"}]}
"""

_JSON_SHAPE_EXTENDED = """
Формат відповіді (задано розширений промпт або додатковий контекст вище):

Дотримуйся інструкцій з блоку «Додатковий контекст» / файлу розширеного промпта щодо набору полів і змісту.
• Якщо там не сказано інакше — поверни JSON-масив об'єктів осіб; у кожного об'єкта щонайменше "surname" та "name", інші поля — як указано для типу документа.
• Допустимо також повернути об'єкт { "scan": { ... }, "persons": [ ... ] }, якщо це прямо відповідає розширеному промпту.

Приклад масиву (якщо об'єкт-обгортка не потрібен):
[
  {"surname": "Коваленко", "name": "Іван", "father": "Петро", "yob": 1854, "location": "Київ"}
]
"""

# Для --extended-prompt: якщо значення збігається з цим шаблоном, шукаємо файл
# prompts/<значення>.txt (розширений промпт), інакше — довільний текст.
_EXTENDED_PROMPT_FILE_STEM_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _load_prompt_template() -> str:
    global _prompt_template_cache
    if _prompt_template_cache is None:
        _prompt_template_cache = _BASE_PROMPT_FILE.read_text(encoding="utf-8")
    return _prompt_template_cache


# ------------------------------------------------------------------ #
#  Витягування номеру з імені файлу                                   #
# ------------------------------------------------------------------ #

def extract_number(filename: str) -> Optional[int]:
    """
    Витягує номер зі стовбця файлу. Правила:
      - "00023.jpg"      → 23
      - "scan_00645.jpg" → 645  (береться останній числовий блок)
      - "abc.jpg"        → None
    """
    stem = Path(filename).stem
    matches = re.findall(r"\d+", stem)
    if not matches:
        return None
    return int(matches[-1])  # знімає ведучі нулі автоматично


# ------------------------------------------------------------------ #
#  Базовий промпт: prompts/base_prompt.txt; розширений — опційно (див. _build_prompt)  #
# ------------------------------------------------------------------ #


def _extended_prompt_active(extended_prompt: Optional[str]) -> bool:
    return bool(extended_prompt and str(extended_prompt).strip())


def _build_prompt(extended_prompt: Optional[str]) -> str:
    extra = ""
    if extended_prompt:
        raw = extended_prompt.strip()
        if raw:
            if _EXTENDED_PROMPT_FILE_STEM_RE.fullmatch(raw):
                extended_path = (_PROMPTS_DIR / f"{raw}.txt").resolve()
                if extended_path.is_file():
                    extra = "\n" + extended_path.read_text(encoding="utf-8")
                else:
                    extra = f"\nДодатковий контекст: {raw}"
            else:
                extra = f"\nДодатковий контекст: {raw}"
    json_shape = _JSON_SHAPE_EXTENDED if _extended_prompt_active(extended_prompt) else _JSON_SHAPE_DEFAULT
    return _load_prompt_template().format(extra=extra, json_shape=json_shape)


# ------------------------------------------------------------------ #
#  Виклик моделі                                                       #
# ------------------------------------------------------------------ #

_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heic",
}

# Клієнт ініціалізується один раз через init_client()
_client: Optional[genai.Client] = None

_log = logging.getLogger(__name__)

# Повтори при тимчасових збоях мережі та при перевантаженні / лімітах API (503, 429…)
_GEMINI_RETRY_MAX_ATTEMPTS = 8
_GEMINI_RETRY_BASE_DELAY_S = 2.0
_GEMINI_RETRY_MAX_DELAY_S = 90.0
# HTTP-коди, за яких має сенс повторити запит (не 4xx крім 408/429)
_GEMINI_RETRY_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def _walk_exceptions(exc: BaseException):
    """__cause__ / __context__ — ланцюг як у httpx → httpcore → gaierror."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if e is None:
            continue
        eid = id(e)
        if eid in seen:
            continue
        seen.add(eid)
        yield e
        stack.append(getattr(e, "__cause__", None))
        stack.append(getattr(e, "__context__", None))


def _is_transient_transport_error(exc: BaseException) -> bool:
    for e in _walk_exceptions(exc):
        if isinstance(e, (TimeoutError, BrokenPipeError)):
            return True
        if isinstance(e, ConnectionError):
            return True
        if isinstance(e, socket.gaierror):
            return True
        if isinstance(e, OSError):
            no = e.errno
            eai_again = getattr(socket, "EAI_AGAIN", None)
            if eai_again is not None and no == eai_again:
                return True
            if no in (
                errno.ETIMEDOUT,
                errno.ECONNRESET,
                errno.EPIPE,
                errno.ENETUNREACH,
                errno.EHOSTUNREACH,
            ):
                return True
        try:
            import httpx
        except ImportError:
            pass
        else:
            if isinstance(
                e,
                (
                    httpx.ConnectError,
                    httpx.ReadTimeout,
                    httpx.WriteTimeout,
                    httpx.PoolTimeout,
                ),
            ):
                return True
        try:
            import httpcore
        except ImportError:
            pass
        else:
            if isinstance(
                e,
                (
                    httpcore.ConnectError,
                    httpcore.ReadTimeout,
                    httpcore.WriteTimeout,
                    httpcore.PoolTimeout,
                ),
            ):
                return True
    return False


def _http_status_from_exception(exc: BaseException) -> Optional[int]:
    """Код відповіді API, якщо помилка з HTTP-шару або google.genai.errors.APIError."""
    for e in _walk_exceptions(exc):
        if isinstance(e, genai_errors.APIError):
            code = getattr(e, "code", None)
            if isinstance(code, int):
                return code
        try:
            import httpx
        except ImportError:
            pass
        else:
            if isinstance(e, httpx.HTTPStatusError):
                return e.response.status_code
    return None


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Заголовок Retry-After (секунди), якщо є."""
    for e in _walk_exceptions(exc):
        if isinstance(e, genai_errors.APIError):
            resp = getattr(e, "response", None)
            if resp is not None and hasattr(resp, "headers"):
                ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                if ra is not None:
                    try:
                        return float(ra)
                    except ValueError:
                        pass
    return None


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    if _is_transient_transport_error(exc):
        return True
    status = _http_status_from_exception(exc)
    return status is not None and status in _GEMINI_RETRY_HTTP_STATUSES


def init_client(api_key: str):
    global _client
    _client = genai.Client(api_key=api_key)


def process_image(
    local_path: str,
    model_name: str,
    temperature: float,
    extended_prompt: Optional[str],
) -> tuple:
    """
    Відправляє зображення в Gemini. Повертає (persons, scan_meta):
    persons — список осіб з name, surname, meta (усі інші поля з JSON особи);
    scan_meta — dict для scans.meta або None (дата документа в режимі без розширеного промпта).
    """
    if _client is None:
        raise RuntimeError("Gemini клієнт не ініціалізовано. Викличте init_client() спочатку.")

    ext = Path(local_path).suffix.lower()
    mime_type = _MIME_MAP.get(ext, "image/jpeg")

    with open(local_path, "rb") as f:
        image_bytes = f.read()

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        _build_prompt(extended_prompt),
    ]
    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
    )

    last_exc: Optional[BaseException] = None
    for attempt in range(1, _GEMINI_RETRY_MAX_ATTEMPTS + 1):
        try:
            response = _client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
            return _parse_response(
                response.text,
                extended_used=_extended_prompt_active(extended_prompt),
            )
        except Exception as e:
            last_exc = e
            if attempt >= _GEMINI_RETRY_MAX_ATTEMPTS or not _is_retryable_gemini_error(e):
                break
            delay = min(
                _GEMINI_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)),
                _GEMINI_RETRY_MAX_DELAY_S,
            )
            ra = _retry_after_seconds(e)
            if ra is not None:
                delay = max(delay, min(ra, _GEMINI_RETRY_MAX_DELAY_S))
            # невеликий джиттер, щоб одночасні клієнти не били в API одним фронтом
            delay *= 1.0 + random.uniform(0.0, 0.12)
            status = _http_status_from_exception(e)
            kind = (
                f"HTTP {status}"
                if status is not None
                else "мережа"
            )
            _log.warning(
                "Тимчасова помилка Gemini (%s, спроба %s/%s): %s — повтор через %.1f с",
                kind,
                attempt,
                _GEMINI_RETRY_MAX_ATTEMPTS,
                e,
                delay,
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


def _flat_dict_to_scan_meta(d: dict) -> dict:
    """Плоскі поля (scan або scan.meta) → очищений dict для scans.meta."""
    out: dict = {}
    if "document_year" in d:
        out["document_year"] = _clean_int(d.get("document_year"))
    if "document_date" in d:
        out["document_date"] = _clean_str(d.get("document_date"))
    for k, v in d.items():
        if k in ("document_year", "document_date"):
            continue
        if v is None or isinstance(v, (dict, list)):
            continue
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, int):
            out[k] = v
        elif isinstance(v, float):
            out[k] = int(v) if v == int(v) else v
        else:
            out[k] = _clean_str(v)
    return out


def _scan_meta_from_block(block: dict) -> Optional[dict]:
    """
    Об'єкт scan → JSON для scans.meta.
    Підтримка: плоскі поля; або вкладений block['meta'] (має пріоритет при збігу ключів).
    """
    out: dict = {}
    top = {k: v for k, v in block.items() if k != "meta"}
    out.update(_flat_dict_to_scan_meta(top))
    if isinstance(block.get("meta"), dict):
        out.update(_flat_dict_to_scan_meta(block["meta"]))
    return out if any(x is not None for x in out.values()) else None


def _parse_response(text: str, *, extended_used: bool) -> tuple:
    """Парсить JSON: масив осіб або об'єкт з persons (+ опційно scan). Повертає (persons, scan_meta)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    data = json.loads(text)

    scan_meta: Optional[dict] = None
    raw_list: Optional[list] = None

    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        raw_list = data.get("persons")
        scan_block = data.get("scan")
        if isinstance(scan_block, dict):
            scan_meta = _scan_meta_from_block(scan_block)
        if raw_list is None:
            raise ValueError(
                "Очікувався JSON-масив осіб або об'єкт з ключем 'persons' (масив осіб)"
            )
    else:
        raise ValueError(f"Очікувався JSON-масив або об'єкт, отримано: {type(data)}")

    persons = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        meta = _person_meta_from_item(item)
        persons.append({
            "name": _clean_str(item.get("name")),
            "surname": _clean_str(item.get("surname")),
            "meta": meta if meta else None,
        })
    return persons, scan_meta


# Цілі поля в meta особи — нормалізуємо через _clean_int.
_META_INT_KEYS = frozenset({"yob", "children_count", "marriage_ordinal"})


def _meta_fields_from_flat_dict(d: dict) -> dict:
    """Плоскі поля meta (без name/surname на верхньому рівні особи)."""
    out: dict = {}
    for k, v in d.items():
        if k in ("name", "surname"):
            continue
        if k in _META_INT_KEYS:
            out[k] = _clean_int(v)
            continue
        if isinstance(v, dict) or isinstance(v, list):
            continue
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, int):
            out[k] = v
        elif isinstance(v, float):
            out[k] = int(v) if v == int(v) else v
        else:
            out[k] = _clean_str(v)
    return out


def _person_meta_from_item(item: dict) -> dict:
    """
    Якщо є вкладений item['meta'] — лише звідти (поля name/surname у meta ігноруємо).
    Інакше — усі поля крім name/surname з кореня об'єкта (плоский формат).
    """
    if isinstance(item.get("meta"), dict):
        return _meta_fields_from_flat_dict(item["meta"])
    return _meta_fields_from_flat_dict(item)


def _clean_str(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _clean_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
