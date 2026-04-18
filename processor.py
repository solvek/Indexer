"""
Обробка зображень через Gemini: витягування інформації про людей.
"""
import json
import re
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types


_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "document_extraction.txt"
_prompt_template_cache: Optional[str] = None


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
    extra = f"\nДодатковий контекст: {description}" if description else ""
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

    response = _client.models.generate_content(
        model=model_name,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            _build_prompt(description),
        ],
        config=types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
        ),
    )

    return _parse_response(response.text)


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
