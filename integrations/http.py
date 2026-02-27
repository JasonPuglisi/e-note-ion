# integrations/http.py
#
# Shared HTTP utilities for integrations.
#
# fetch_with_retry: wraps requests.request with exponential backoff on
# transient failures (5xx responses and network-level errors).

import time
from dataclasses import dataclass, field
from typing import Any

import requests


def fetch_with_retry(
  method: str,
  url: str,
  *,
  retries: int = 3,
  backoff: float = 1.0,
  **kwargs: Any,
) -> requests.Response:
  """Send an HTTP request, retrying on transient failures.

  Retries on 5xx HTTP responses and network-level errors (Timeout,
  ConnectionError). Does not retry on 4xx — those are client errors that
  retrying will not resolve. Raises on the final attempt like requests would.

  Args:
    method:  HTTP method string ('GET', 'POST', etc.).
    url:     Request URL.
    retries: Maximum number of attempts (default 3 — one initial + two retries).
    backoff: Base delay in seconds; actual delay is backoff * 2**attempt
             (0s before attempt 0, 1s before attempt 1, 2s before attempt 2).
    **kwargs: Passed through to requests.request (e.g. params, headers, timeout).
  """
  last_exc: Exception | None = None

  for attempt in range(retries):
    if attempt > 0:
      time.sleep(backoff * 2 ** (attempt - 1))
    try:
      r = requests.request(method, url, **kwargs)
      if r.status_code >= 500:
        last_exc = requests.HTTPError(f'HTTP {r.status_code} {r.reason}', response=r)
        continue
      return r
    except (requests.Timeout, requests.ConnectionError) as e:
      last_exc = e
      continue

  if isinstance(last_exc, requests.HTTPError):
    raise last_exc
  raise last_exc  # type: ignore[misc]


@dataclass
class CacheEntry:
  """A timestamped cache entry for integration variables."""

  value: dict[str, list[list[str]]]
  cached_at: float = field(default_factory=time.monotonic)

  def is_valid(self, ttl: float) -> bool:
    """Return True if the entry is within the given TTL (seconds)."""
    return time.monotonic() - self.cached_at <= ttl
