"""
Обробка зображень через AI-модель: витягування інформації про людей.
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from ai_clients import AIClient, api_error_detail_for_log, create_ai_client


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_BASE_PROMPT_FILE = _PROMPTS_DIR / "_base.txt"
_prompt_template_cache: Optional[str] = None

# У prompts/_base.txt блок «дефолтний формат JSON» між маркерами; при розширеному промпті його прибираємо
# (формат задає сам файл розширеного промпта).
_DEFAULT_FORMAT_BEGIN = "<!--BEGIN_DEFAULT_FORMAT-->"
_DEFAULT_FORMAT_END = "<!--END_DEFAULT_FORMAT-->"
_DEFAULT_FORMAT_BLOCK_RE = re.compile(
    re.escape(_DEFAULT_FORMAT_BEGIN) + r"\s*.*?\s*" + re.escape(_DEFAULT_FORMAT_END),
    flags=re.DOTALL,
)

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
#  Базовий промпт: prompts/_base.txt (+ опційний блок prompts/<stem>.txt).  #
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
    raw = _load_prompt_template().replace("{extra}", extra)
    if _extended_prompt_active(extended_prompt):
        raw = _DEFAULT_FORMAT_BLOCK_RE.sub("", raw)
    else:
        raw = raw.replace(_DEFAULT_FORMAT_BEGIN, "").replace(_DEFAULT_FORMAT_END, "")
    return raw


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
_client: Optional[AIClient] = None

_log = logging.getLogger(__name__)


def init_client(provider: str, api_key: Optional[str] = None):
    """Ініціалізує AI-клієнт. Для старого виклику init_client(key) лишається Gemini."""
    global _client
    if api_key is None:
        api_key = provider
        provider = "gemini"
    _client = create_ai_client(provider, api_key)


def process_image(
    local_path: str,
    model_name: str,
    temperature: float,
    extended_prompt: Optional[str],
) -> tuple:
    """
    Відправляє зображення в AI-модель. Повертає (persons, scan_meta):
    persons — список осіб з name, surname, meta (усі інші поля з JSON особи);
    scan_meta — dict для scans.meta або None (дата документа в режимі без розширеного промпта).
    """
    if _client is None:
        raise RuntimeError("AI-клієнт не ініціалізовано. Викличте init_client() спочатку.")

    ext = Path(local_path).suffix.lower()
    mime_type = _MIME_MAP.get(ext, "image/jpeg")

    with open(local_path, "rb") as f:
        image_bytes = f.read()

    prompt = _build_prompt(extended_prompt)

    _log.info(
        "  Запит до моделі (%s, %s)…",
        _client.provider_name,
        model_name,
    )

    last_exc: Optional[BaseException] = None
    for attempt in range(1, _client.retry_max_attempts + 1):
        try:
            response_text = _client.generate_json_from_image(
                model_name=model_name,
                image_bytes=image_bytes,
                mime_type=mime_type,
                prompt=prompt,
                temperature=temperature,
            )
            return _parse_response(
                response_text,
                extended_used=_extended_prompt_active(extended_prompt),
            )
        except Exception as e:
            last_exc = e
            if attempt >= _client.retry_max_attempts or not _client.is_retryable_error(e):
                break
            status = _client.http_status_from_exception(e)
            delay = _client.retry_delay_seconds(e, attempt)
            kind = (
                f"HTTP {status}"
                if status is not None
                else "мережа"
            )
            _log.warning(
                "Тимчасова помилка %s (%s, спроба %s/%s): %s — повтор через %.1f с",
                _client.provider_name,
                kind,
                attempt,
                _client.retry_max_attempts,
                e,
                delay,
            )
            time.sleep(delay)

    assert last_exc is not None
    _log.error(
        "Помилка моделі без успішного повтору: %s",
        api_error_detail_for_log(last_exc),
    )
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
