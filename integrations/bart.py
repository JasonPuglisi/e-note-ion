# integrations/bart.py
#
# BART real-time departure integration.
#
# Fetches upcoming departure estimates from the BART API and returns them as
# a variables dict for use with content templates. Line colors are derived
# dynamically from the BART routes API on first call and cached for the
# process lifetime.
#
# Required config.toml keys ([bart]):
#   api_key    — API key (free at https://api.bart.gov/api/register.aspx)
#   station    — Originating station code (e.g. MLPT for Milpitas)
#   line1_dest — Destination abbreviation code for line 1 (e.g. DALY for Daly City)
#
# Optional config.toml keys:
#   line2_dest — Second destination abbreviation code (optional)

from typing import Any

import requests

import integrations.vestaboard as vestaboard
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry

_API_BASE = 'https://api.bart.gov/api'

# Maps BART API color names to Vestaboard color tags.
_LINE_COLOR_TAG: dict[str, str] = {
  'RED': '[R]',
  'ORANGE': '[O]',
  'YELLOW': '[Y]',
  'GREEN': '[G]',
  'BLUE': '[B]',
  'PURPLE': '[V]',
  'WHITE': '[W]',
  'BEIGE': '[W]',  # no beige on Vestaboard; white is closest
}

# Module-level route color cache: dest_abbr → [color_tags].
# None = not yet populated; {} = populated but empty (or failed).
_dest_color_cache: dict[str, list[str]] | None = None

# Last-known-good cache for departure data. Served on transient API failures
# if the entry is within _DEPARTURES_CACHE_TTL seconds of its fetch time.
_departures_cache: CacheEntry | None = None
_DEPARTURES_CACHE_TTL = 5 * 60  # 5 minutes


def _fetch_dest_colors(api_key: str, origin: str) -> dict[str, list[str]]:
  """Build a dest_abbr → [color_tags] map using the BART routes API.

  Calls /route.aspx?cmd=routes once, then /route.aspx?cmd=routeinfo for each
  route. Only routes that serve the origin station are included. Routes are
  processed in ascending number order so multi-color destinations have a
  deterministic tag order (lowest route number first).

  Raises requests.HTTPError on API failure; the caller handles degradation.
  """
  r = fetch_with_retry(
    'GET',
    f'{_API_BASE}/route.aspx',
    params={'cmd': 'routes', 'key': api_key, 'json': 'y'},
    timeout=10,
  )
  r.raise_for_status()
  raw_routes = r.json()['root']['routes']['route']
  if isinstance(raw_routes, dict):
    raw_routes = [raw_routes]

  routes = sorted(raw_routes, key=lambda rt: int(rt.get('number', 0)))
  origin_upper = origin.upper()
  color_map: dict[str, list[str]] = {}

  for route in routes:
    tag = _LINE_COLOR_TAG.get(route.get('color', '').upper())
    if not tag:
      continue
    ri = fetch_with_retry(
      'GET',
      f'{_API_BASE}/route.aspx',
      params={'cmd': 'routeinfo', 'route': route['number'], 'key': api_key, 'json': 'y'},
      timeout=10,
    )
    ri.raise_for_status()
    route_info = ri.json()['root']['routes']['route']

    stations = route_info.get('config', {}).get('station', [])
    if isinstance(stations, str):
      stations = [stations]
    if origin_upper not in [s.upper() for s in stations]:
      continue

    dest = route_info.get('destination', '').upper()
    if dest:
      if dest not in color_map:
        color_map[dest] = []
      if tag not in color_map[dest]:
        color_map[dest].append(tag)

  return color_map


def _no_service_line(dest_abbr: str, color_map: dict[str, list[str]]) -> str:
  """Return a no-service display line for the given destination abbreviation.

  Looks up a color tag from the dynamic color map, producing e.g.
  '[G] NO SERVICE'. Falls back to 'NO SERVICE' if the destination is unknown.
  """
  tags = color_map.get(dest_abbr.upper(), [])
  return f'{tags[0]} NO SERVICE' if tags else 'NO SERVICE'


def _format_minutes(mins: str) -> str:
  """Convert a BART API minutes string to a short display string.

  All times are zero-padded to 2 digits so departure columns always align
  regardless of how many single-digit times appear on a line. Arriving trains
  ('Leaving' or '0') are shown as '00'. E.g. '00', '05', '12'.
  """
  if mins == 'Leaving':
    return '00'
  try:
    return f'{int(mins):02}'
  except ValueError:
    return mins


def _build_line(color_tag: str, estimates: list[dict[str, Any]]) -> str:
  """Build a departure line like '[G] 8 14 31' fitting within model.cols."""
  base = color_tag + ' '
  parts: list[str] = []
  for est in estimates:
    t = _format_minutes(est['minutes'])
    if vestaboard.display_len(base + ' '.join(parts + [t])) > vestaboard.model.cols:
      break
    parts.append(t)
  return base + (' '.join(parts) if parts else '--')


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch BART departures and return a variables dict for template rendering.

  Returns keys: 'station', 'line1', and optionally 'line2'. Each value is a
  single-option list (no randomness — departure times are always current).

  The route color cache is populated lazily on first call. If the routes API
  fails, no-service lines degrade to colorless 'NO SERVICE'.

  On transient API failure, returns the last-known-good departures if within
  _DEPARTURES_CACHE_TTL. Raises IntegrationDataUnavailableError otherwise.
  """
  global _dest_color_cache, _departures_cache

  import config as _config_mod

  api_key = _config_mod.get('bart', 'api_key')
  station = _config_mod.get('bart', 'station')
  dest1 = _config_mod.get('bart', 'line1_dest')
  dest2 = _config_mod.get_optional('bart', 'line2_dest')
  dest_filters = [d for d in (dest1, dest2) if d]

  if _dest_color_cache is None:
    try:
      _dest_color_cache = _fetch_dest_colors(api_key, station)
    except Exception as e:  # noqa: BLE001
      print(f'Warning: could not build BART color cache: {e}')
      _dest_color_cache = {}

  try:
    r = fetch_with_retry(
      'GET',
      f'{_API_BASE}/etd.aspx',
      params={'cmd': 'etd', 'orig': station, 'key': api_key, 'json': 'y'},
      timeout=10,
    )
    r.raise_for_status()
  except requests.RequestException as e:
    if isinstance(e, requests.HTTPError):
      msg = f'BART API error: {e.response.status_code} {e.response.reason}'
    else:
      msg = str(e)
    print(f'BART: departures request failed — {msg}')
    if _departures_cache is not None and _departures_cache.is_valid(_DEPARTURES_CACHE_TTL):
      return _departures_cache.value
    raise IntegrationDataUnavailableError(f'BART: departures request failed — {msg}') from None
  data = r.json()

  station_data = data['root']['station'][0]
  station_name: str = station_data['name']
  etds: list[dict[str, Any]] = station_data.get('etd', [])

  variables: dict[str, list[list[str]]] = {
    'station': [[station_name]],
  }

  for i, dest_code in enumerate(dest_filters, 1):
    line_value = _no_service_line(dest_code, _dest_color_cache)
    for etd in etds:
      if dest_code.upper() == etd.get('abbreviation', '').upper():
        estimates = etd.get('estimate', [])
        if estimates:
          color_tag = _LINE_COLOR_TAG.get(estimates[0].get('color', ''), '[ ]')
          line_value = _build_line(color_tag, estimates)
        break
    variables[f'line{i}'] = [[line_value]]

  _departures_cache = CacheEntry(variables)
  return variables
