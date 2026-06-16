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


def _is_cert_error(exc: BaseException) -> bool:
    """True if exc (or its cause chain) is an SSL certificate-verification failure."""
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        text = str(cur).lower()
        if "certificate verify failed" in text or "certificate_verify_failed" in text:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _do_get(url: str, headers: dict, timeout_s: int, verify) -> str:
    with httpx.Client(
        timeout=timeout_s,
        follow_redirects=True,
        headers=headers,
        verify=verify,
    ) as client:
        r = client.get(url)
        if r.status_code >= 500:
            raise FetchError(f"upstream {r.status_code} for {url}")
        r.raise_for_status()
        return r.text


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.HTTPError, FetchError)),
)
def fetch_html(url: str, *, timeout: int | None = None) -> str:
    """GET a page and return text. Polite UA, exponential backoff, 3 attempts.

    DSE/CSE serve a broken TLS chain (the leaf's real intermediate is missing,
    a mismatched Sectigo root is sent instead). Browsers repair this via AIA
    fetching; Python's ssl cannot, so verification raises "unable to get local
    issuer certificate". These are public, credential-free data pages, so when —
    and only when — verification fails on a cert error, we retry the same GET
    with verification disabled. Every other failure mode keeps full verification.
    """
    settings = get_settings()
    headers = {
        "User-Agent": settings.http_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    timeout_s = timeout or settings.http_timeout_seconds
    logger.debug(f"GET {url}")
    try:
        return _do_get(url, headers, timeout_s, _SSL_CTX)
    except Exception as e:
        if _is_cert_error(e):
            logger.warning(
                f"TLS verification failed for {url} (broken upstream chain); "
                f"retrying without verification"
            )
            return _do_get(url, headers, timeout_s, False)
        raise
