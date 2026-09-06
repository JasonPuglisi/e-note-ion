# integrations/http.py
#
# Shared HTTP utilities for integrations.
#
# fetch_with_retry: wraps requests.request with exponential backoff on
# transient failures (5xx responses and network-level errors).

import importlib.metadata
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

_ua_cache: str | None = None

# Matches a URL well enough to redact it. Deliberately greedy about what counts
# as a URL and conservative about what survives: over-redacting a log line is
# cheap, leaking a credential is not.
_URL_RE = re.compile(r'\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\'"<>\\]+')


def _redact_url(match: re.Match[str]) -> str:
  raw = match.group(0)
  try:
    parts = urlsplit(raw)
  except ValueError:
    return '<redacted-url>'
  host = parts.hostname or ''
  if not host:
    return '<redacted-url>'
  if parts.port:
    host = f'{host}:{parts.port}'
  # hostname drops any user:password@ prefix for us.
  tail = '/...' if (parts.path not in ('', '/') or parts.query) else ''
  return f'{parts.scheme}://{host}{tail}'


# requests reports the *path* separately from the host: "Max retries exceeded
# with url: /api/etd.aspx?cmd=etd&key=SECRET". The absolute-URL pattern above
# never sees that, and BART puts its API key in exactly that query string.
_BARE_PATH_RE = re.compile(r'(?i)\b(url:\s*)(/[^\s\'"<>\\]*)')

# Anything left that looks like a query string, wherever it appears.
_QUERY_RE = re.compile(r'\?[A-Za-z0-9_.\-]+=[^\s\'"<>\\]*')


def redact(text: str) -> str:
  """Reduce every URL in *text* to scheme://host, dropping path, query and userinfo.

  Exception text from requests embeds the full request URL, and several of our
  URLs *are* credentials: a private iCloud or Google ICS feed is a bearer token
  in path form, and BART takes its API key as a query parameter. That text
  reaches record_error(), which writes it to data/health.jsonl and serves it
  from /health as last_error_message.

  Host and scheme are kept because "which service failed" is the whole
  diagnostic value; everything that could identify or authenticate is dropped.
  """
  text = _URL_RE.sub(_redact_url, text)
  text = _BARE_PATH_RE.sub(r'\1/...', text)
  return _QUERY_RE.sub('?...', text)


def user_agent() -> str:
  """Return the User-Agent string for outbound requests.

  Returns 'e-note-ion/{version}', falling back to 'e-note-ion/dev' when the
  package metadata is unavailable (e.g. source install without pip install -e).
  The result is cached after the first call.
  """
  global _ua_cache
  if _ua_cache is None:
    try:
      version = importlib.metadata.version('e-note-ion')
    except importlib.metadata.PackageNotFoundError:
      version = 'dev'
    _ua_cache = f'e-note-ion/{version}'
  return _ua_cache


def fetch_with_retry(
  method: str,
  url: str,
  *,
  retries: int = 3,
  backoff: float = 1.0,
  retry_on: frozenset[int] = frozenset(),
  **kwargs: Any,
) -> requests.Response:
  """Send an HTTP request, retrying on transient failures.

  Retries on 5xx HTTP responses and network-level errors (Timeout,
  ConnectionError). Does not retry on 4xx — those are client errors that
  retrying will not resolve — unless the status code appears in *retry_on*.
  Raises on the final attempt like requests would.

  Args:
    method:   HTTP method string ('GET', 'POST', etc.).
    url:      Request URL.
    retries:  Maximum number of attempts (default 3 — one initial + two retries).
    backoff:  Base delay in seconds; actual delay is backoff * 2**attempt
              (0s before attempt 0, 1s before attempt 1, 2s before attempt 2).
    retry_on: Extra status codes to treat as retryable (e.g. YouTube RSS uses
              404 as a rate-limit signal).
    **kwargs: Passed through to requests.request (e.g. params, headers, timeout).
  """
  last_exc: Exception | None = None

  for attempt in range(retries):
    if attempt > 0:
      delay = backoff * 2 ** (attempt - 1)
      logger.debug('retry attempt %d/%d for %s %s (backoff=%.1fs)', attempt + 1, retries, method, url, delay)
      time.sleep(delay)
    try:
      r = requests.request(method, url, **kwargs)
      if r.status_code >= 500 or r.status_code in retry_on:
        last_exc = requests.HTTPError(f'HTTP {r.status_code} {r.reason}', response=r)
        continue
      return r
    except (requests.Timeout, requests.ConnectionError) as e:
      last_exc = e
      continue

  raise last_exc  # type: ignore[misc]


@dataclass
class CacheEntry:
  """A timestamped cache entry for integration variables."""

  value: dict[str, list[list[str]]]
  cached_at: float = field(default_factory=time.monotonic)

  def is_valid(self, ttl: float) -> bool:
    """Return True if the entry is within the given TTL (seconds)."""
    return time.monotonic() - self.cached_at <= ttl
