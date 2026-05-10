"""
Клієнти AI-провайдерів для розпізнавання зображень.

Модуль ховає від processor.py відмінності SDK: Gemini та OpenAI мають
різні формати запиту, але назовні повертають текст JSON-відповіді.
"""
import base64
import errno
import random
import socket
from abc import ABC, abstractmethod
from typing import Optional


_PROVIDER_ALIASES = {
    "gemini": "gemini",
    "google": "gemini",
    "google-gemini": "gemini",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
}

_DEFAULT_MODELS = {
    "gemini": "gemini-2.0-flash-lite",
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


class GeminiClient(AIClient):
    provider_name = "gemini"

    def __init__(self, api_key: str):
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types

        self._client = genai.Client(api_key=api_key)
        self._errors = genai_errors
        self._types = types

    def generate_json_from_image(
        self,
        *,
        model_name: str,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        temperature: float,
    ) -> str:
        contents = [
            self._types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ]
        config = self._types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
        )
        response = self._client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
        return response.text or ""

    def http_status_from_exception(self, exc: BaseException) -> Optional[int]:
        for e in _walk_exceptions(exc):
            if isinstance(e, self._errors.APIError):
                code = getattr(e, "code", None)
                if isinstance(code, int):
                    return code
        return super().http_status_from_exception(exc)


class OpenAIClient(AIClient):
    provider_name = "openai"

    def __init__(self, api_key: str):
        import openai as openai_module
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._api_status_error = getattr(openai_module, "APIStatusError", None)
        retryable_names = (
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
        )
        self._retryable_errors = tuple(
            cls
            for cls in (getattr(openai_module, name, None) for name in retryable_names)
            if cls is not None
        )

    def generate_json_from_image(
        self,
        *,
        model_name: str,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        temperature: float,
    ) -> str:
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{image_b64}"
        response = self._client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                }
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    def http_status_from_exception(self, exc: BaseException) -> Optional[int]:
        for e in _walk_exceptions(exc):
            if self._api_status_error is not None and isinstance(e, self._api_status_error):
                status_code = getattr(e, "status_code", None)
                if isinstance(status_code, int):
                    return status_code
        return super().http_status_from_exception(exc)

    def is_retryable_error(self, exc: BaseException) -> bool:
        if self._retryable_errors and any(
            isinstance(e, self._retryable_errors) for e in _walk_exceptions(exc)
        ):
            return True
        return super().is_retryable_error(exc)


def create_ai_client(provider: str, api_key: str) -> AIClient:
    normalized = normalize_provider(provider)
    if normalized == "gemini":
        return GeminiClient(api_key)
    if normalized == "openai":
        return OpenAIClient(api_key)
    raise ValueError(f"Непідтримуваний AI-провайдер: {provider}")
