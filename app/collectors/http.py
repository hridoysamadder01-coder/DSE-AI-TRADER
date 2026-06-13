from __future__ import annotations

import ssl

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_settings


def _build_ssl_context() -> ssl.SSLContext:
    """Prefer the OS trust store (Windows / macOS keychain) via `truststore`.

    Falls back to certifi (httpx default) if truststore is unavailable. DSE/CSE
    certs aren't always present in certifi's Mozilla bundle, but they're
    universally trusted by the OS root store.
    """
    try:
        import truststore  # type: ignore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


_SSL_CTX = _build_ssl_context()


class FetchError(RuntimeError):
    pass


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.HTTPError, FetchError)),
)
def fetch_html(url: str, *, timeout: int | None = None) -> str:
    """GET a page and return text. Polite UA, exponential backoff, 3 attempts."""
    settings = get_settings()
    headers = {
        "User-Agent": settings.http_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    timeout_s = timeout or settings.http_timeout_seconds
    logger.debug(f"GET {url}")
    with httpx.Client(
        timeout=timeout_s,
        follow_redirects=True,
        headers=headers,
        verify=_SSL_CTX,
    ) as client:
        r = client.get(url)
        if r.status_code >= 500:
            raise FetchError(f"upstream {r.status_code} for {url}")
        r.raise_for_status()
        return r.text
