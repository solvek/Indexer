"""Клієнт Google Gemini (generateContent, JSON)."""
from typing import Optional

from ai_clients import AIClient, _walk_exceptions


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
