from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.request

from .base import GenerateRequest, GenerateResponse
from .errors import ProviderHTTPError, ProviderNetworkError, ProviderTimeoutError


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible chat.completions client without external deps."""

    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 45) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        payload = {
            "model": req.model,
            "temperature": req.temperature,
            "messages": [
                {"role": "system", "content": req.system_prompt},
                {"role": "user", "content": req.user_prompt},
            ],
        }

        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ProviderHTTPError(
                f"provider http error: {exc.code}; model={req.model}; url={url}; detail={detail}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderNetworkError(
                f"provider network error: model={req.model}; url={url}; reason={exc.reason}"
            ) from exc
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError) as exc:
            raise ProviderNetworkError(
                f"provider network error: model={req.model}; url={url}; reason={exc}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError(
                f"provider timeout: model={req.model}; url={url}; timeout_sec={self.timeout_sec}; reason={exc}"
            ) from exc

        parsed = json.loads(raw)
        text = parsed["choices"][0]["message"]["content"]
        return GenerateResponse(text=text, raw_response=raw)
