"""
Клієнти AI-провайдерів для розпізнавання зображень.

Як source.py + source_local / source_drive: тут абстракція AIClient, реєстр провайдерів
і фабрика; конкретні SDK — у ai_client_gemini.py та ai_client_openai.py.
"""
import errno
import random
import re
import socket
from abc import ABC, abstractmethod
from typing import Optional


def api_error_detail_for_log(exc: BaseException) -> str:
    """Короткий текст для логів: dict error з тіла відповіді API (OpenAI тощо), інакше str(exc)."""
    for e in _walk_exceptions(exc):
        body = getattr(e, "body", None)
        if isinstance(body, dict):
            inner = body.get("error")
            if inner is not None:
                return str(inner)
    return str(exc)


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


def _http_status_from_httpx(exc: BaseException) -> Optional[int]:
    for e in _walk_exceptions(exc):
        try:
            import httpx
        except ImportError:
            pass
        else:
            if isinstance(e, httpx.HTTPStatusError):
                return e.response.status_code
    return None


def _retry_after_from_headers(headers) -> Optional[float]:
    if not headers:
        return None
    for key in ("retry-after-ms", "Retry-After-Ms"):
        raw = headers.get(key)
        if raw is not None:
            try:
                return float(raw) / 1000.0
            except ValueError:
                pass
    for key in ("retry-after", "Retry-After"):
        raw = headers.get(key)
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                pass
    return None


class AIClient(ABC):
    """Спільний інтерфейс провайдера, який повертає JSON як текст."""

    provider_name: str
    retry_max_attempts = 8
    retry_base_delay_s = 2.0
    retry_max_delay_s = 90.0
    rate_limit_pause_s = 3600.0
    server_retry_base_delay_s = 300.0
    server_retry_max_delay_s = 7200.0
    retry_http_statuses = frozenset({408, 409, 429, 500, 502, 503, 504})
    server_backoff_statuses = frozenset({500, 502, 503, 504})

    @abstractmethod
    def generate_json_from_image(
        self,
        *,
        model_name: str,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        temperature: float,
    ) -> str:
        raise NotImplementedError

    def http_status_from_exception(self, exc: BaseException) -> Optional[int]:
        return _http_status_from_httpx(exc)

    def retry_after_seconds(self, exc: BaseException) -> Optional[float]:
        for e in _walk_exceptions(exc):
            response = getattr(e, "response", None)
            headers = getattr(response, "headers", None)
            retry_after = _retry_after_from_headers(headers)
            if retry_after is not None:
                return retry_after
        return None

    def is_retryable_error(self, exc: BaseException) -> bool:
        if _is_transient_transport_error(exc):
            return True
        status = self.http_status_from_exception(exc)
        return status is not None and status in self.retry_http_statuses

    def retry_delay_seconds(self, exc: BaseException, attempt: int) -> float:
        status = self.http_status_from_exception(exc)
        if status == 429:
            delay = self.rate_limit_pause_s
        elif status in self.server_backoff_statuses:
            delay = min(
                self.server_retry_base_delay_s * (2 ** (attempt - 1)),
                self.server_retry_max_delay_s,
            )
            retry_after = self.retry_after_seconds(exc)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self.server_retry_max_delay_s))
        else:
            delay = min(
                self.retry_base_delay_s * (2 ** (attempt - 1)),
                self.retry_max_delay_s,
            )
            retry_after = self.retry_after_seconds(exc)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self.retry_max_delay_s))
        return delay * (1.0 + random.uniform(0.0, 0.12))


# --- реєстр провайдерів і фабрика (лінивий імпорт реалізацій) ---

_PROVIDER_ALIASES = {
    "gemini": "gemini",
    "google": "gemini",
    "google-gemini": "gemini",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
}

_DEFAULT_MODELS = {
    "gemini": "gemini-3.1-flash-lite",
    "openai": "gpt-4o-mini",
}

_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def supported_providers() -> tuple[str, ...]:
    return tuple(_DEFAULT_MODELS.keys())


def normalize_provider(provider: str) -> str:
    key = (provider or "").strip().lower()
    normalized = _PROVIDER_ALIASES.get(key)
    if normalized is None:
        raise ValueError(
            "Невідомий AI-провайдер "
            f"{provider!r}. Підтримуються: {', '.join(supported_providers())}"
        )
    return normalized


def default_model_for_provider(provider: str) -> str:
    return _DEFAULT_MODELS[normalize_provider(provider)]


def provider_api_key_env(provider: str) -> str:
    return _API_KEY_ENV[normalize_provider(provider)]


_MODEL_OPENAI_LIKE = re.compile(r"^(gpt-|chatgpt|o\d)", re.IGNORECASE)
_MODEL_GEMINI_LIKE = re.compile(r"^gemini", re.IGNORECASE)


def infer_provider_from_model(model_name: str) -> Optional[str]:
    """
    Повертає 'openai' | 'gemini' за префіксом імені моделі, або None якщо не визначено.
    Використовується, коли --provider у CLI не вказано.
    """
    m = (model_name or "").strip()
    if not m:
        return None
    if _MODEL_GEMINI_LIKE.match(m):
        return "gemini"
    if _MODEL_OPENAI_LIKE.match(m):
        return "openai"
    return None


def model_provider_mismatch_message(provider: str, model_name: str) -> Optional[str]:
    """Якщо ім'я моделі очевидно не відповідає провайдеру — текст для parser.error."""
    p = normalize_provider(provider)
    m = (model_name or "").strip()
    if not m:
        return None
    if p == "gemini" and _MODEL_OPENAI_LIKE.match(m):
        return (
            f"модель {model_name!r} схожа на OpenAI, а обрано провайдера gemini. "
            "Приберіть --provider gemini або вкажіть --provider openai та OPENAI_API_KEY; "
            "або змініть --model на модель Gemini (наприклад gemini-3.1-flash-lite)."
        )
    if p == "openai" and _MODEL_GEMINI_LIKE.match(m):
        return (
            f"модель {model_name!r} схожа на Gemini, а обрано провайдера openai. "
            "Приберіть --provider openai або вкажіть --provider gemini та GEMINI_API_KEY; "
            "або змініть --model на модель OpenAI (наприклад gpt-4o-mini)."
        )
    return None


def create_ai_client(provider: str, api_key: str) -> AIClient:
    """Фабрика: Gemini або OpenAI (лінивий імпорт SDK відповідної реалізації)."""
    normalized = normalize_provider(provider)
    if normalized == "gemini":
        from ai_client_gemini import GeminiClient

        return GeminiClient(api_key)
    if normalized == "openai":
        from ai_client_openai import OpenAIClient

        return OpenAIClient(api_key)
    raise ValueError(f"Непідтримуваний AI-провайдер: {provider}")
