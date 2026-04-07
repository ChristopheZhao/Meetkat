from __future__ import annotations


class ProviderError(RuntimeError):
    pass


class ProviderHTTPError(ProviderError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderNetworkError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass
