"""Клієнт OpenAI Chat Completions (vision + JSON)."""
import base64
import logging
from typing import Optional

from ai_clients import AIClient, _walk_exceptions, api_error_detail_for_log

_log = logging.getLogger(__name__)

# Моделі, для яких у цьому процесі вже отримали 400 на temperature — не надсилати знову.
_openai_models_skip_temperature: set[str] = set()


def _openai_temperature_unsupported_error(exc: BaseException) -> bool:
    """400 від OpenAI: модель приймає лише температуру за замовчуванням (без аргумента temperature)."""
    for e in _walk_exceptions(exc):
        if getattr(e, "status_code", None) != 400:
            continue
        body = getattr(e, "body", None)
        if isinstance(body, dict):
            inner = body.get("error")
            if isinstance(inner, dict):
                if inner.get("param") == "temperature":
                    return True
                msg = str(inner.get("message", "")).lower()
                if inner.get("code") == "unsupported_value" and "temperature" in msg:
                    return True
        low = str(e).lower()
        if "temperature" in low and (
            "unsupported" in low or "does not support" in low
        ):
            return True
    return False


def _openai_response_format_unsupported_error(exc: BaseException) -> bool:
    """400: response_format / json_object не підтримується цією моделлю."""
    for e in _walk_exceptions(exc):
        if getattr(e, "status_code", None) != 400:
            continue
        body = getattr(e, "body", None)
        if isinstance(body, dict):
            inner = body.get("error")
            if isinstance(inner, dict):
                if inner.get("param") == "response_format":
                    return True
                msg = str(inner.get("message", "")).lower()
                if "response_format" in msg and (
                    "unsupported" in msg or "does not support" in msg or "not supported" in msg
                ):
                    return True
                if "json_object" in msg and (
                    "unsupported" in msg or "does not support" in msg or "not supported" in msg
                ):
                    return True
        low = str(e).lower()
        if ("response_format" in low or "json_object" in low) and (
            "unsupported" in low or "does not support" in low or "not supported" in low
        ):
            return True
    return False


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
        messages = [
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
        ]
        response_format = {"type": "json_object"}
        use_temperature = model_name not in _openai_models_skip_temperature
        use_response_format = True
        while True:
            kwargs = {
                "model": model_name,
                "messages": messages,
            }
            if use_response_format:
                kwargs["response_format"] = response_format
            if use_temperature:
                kwargs["temperature"] = temperature
            try:
                response = self._client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as exc:
                if use_temperature and _openai_temperature_unsupported_error(exc):
                    _openai_models_skip_temperature.add(model_name)
                    _log.info(
                        "OpenAI: модель не приймає задану temperature — повтор запиту без цього параметра; надалі для цієї моделі параметр не надсилається"
                    )
                    use_temperature = False
                    continue
                if use_response_format and _openai_response_format_unsupported_error(exc):
                    _log.info(
                        "OpenAI: модель не підтримує response_format json_object — повтор без нього"
                    )
                    use_response_format = False
                    continue
                _log.error(
                    "OpenAI chat/completions відхилено (temperature=%s, response_format=%s): %s",
                    "так" if use_temperature else "ні",
                    "json_object" if use_response_format else "ні",
                    api_error_detail_for_log(exc),
                )
                raise

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
