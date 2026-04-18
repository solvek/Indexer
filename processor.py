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
_PROMPT_FILE = _PROMPTS_DIR / "document_extraction.txt"
_prompt_template_cache: Optional[str] = None

# Для --description: якщо значення збігається з цим шаблоном, шукаємо файл
# prompts/<значення>.txt (специфіка запуску), інакше — довільний текст (як раніше).
_RUN_SPEC_DESCRIPTION_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _load_prompt_template() -> str:
    global _prompt_template_cache
    if _prompt_template_cache is None:
        _prompt_template_cache = _PROMPT_FILE.read_text(encoding="utf-8")
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
#  Промпт для моделі (текст у prompts/document_extraction.txt)        #
# ------------------------------------------------------------------ #


def _build_prompt(description: Optional[str]) -> str:
    extra = ""
    if description:
        desc = description.strip()
        if desc:
            if _RUN_SPEC_DESCRIPTION_RE.fullmatch(desc):
                run_spec_path = (_PROMPTS_DIR / f"{desc}.txt").resolve()
                if run_spec_path.is_file():
                    extra = "\n" + run_spec_path.read_text(encoding="utf-8")
                else:
                    extra = f"\nДодатковий контекст: {desc}"
            else:
                extra = f"\nДодатковий контекст: {desc}"
    return _load_prompt_template().format(extra=extra)


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
    description: Optional[str],
) -> list:
    """
    Відправляє зображення в Gemini і повертає список знайдених людей.
    Кожна особа — словник з ключами: name, surname, father, yob, location.
    """
    if _client is None:
        raise RuntimeError("Gemini клієнт не ініціалізовано. Викличте init_client() спочатку.")

    ext = Path(local_path).suffix.lower()
    mime_type = _MIME_MAP.get(ext, "image/jpeg")

    with open(local_path, "rb") as f:
        image_bytes = f.read()

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        _build_prompt(description),
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
            return _parse_response(response.text)
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


def _parse_response(text: str) -> list:
    """Парсить JSON відповідь моделі, стійко до markdown обгорток."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    data = json.loads(text)

    if not isinstance(data, list):
        raise ValueError(f"Очікувався JSON-масив, отримано: {type(data)}")

    persons = []
    for item in data:
        if not isinstance(item, dict):
            continue
        persons.append({
            "name": _clean_str(item.get("name")),
            "surname": _clean_str(item.get("surname")),
            "father": _clean_str(item.get("father")),
            "yob": _clean_int(item.get("yob")),
            "location": _clean_str(item.get("location")),
        })
    return persons


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
