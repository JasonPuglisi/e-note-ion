# integrations/bart.py
#
# BART real-time departure integration.
#
# Fetches upcoming departure estimates from the BART API and returns them as
# a variables dict for use with content templates. Line colors are read from
# the API response automatically — no manual color configuration needed.
#
# Required env vars:
#   BART_API_KEY    — API key (free at https://api.bart.gov/api/register.aspx)
#   BART_STATION    — Originating station code (e.g. MLPT for Milpitas)
#
# Optional env vars (configure 1–2 lines to display):
#   BART_LINE_1_DEST — Destination substring to match (e.g. "Daly City")
#   BART_LINE_2_DEST — Second destination substring (optional)

import os
from typing import Any

import requests

import integrations.vestaboard as vestaboard

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


def _format_minutes(mins: str) -> str:
  """Convert a BART API minutes string to a short display string."""
  if mins in ('Leaving', '0'):
    return 'Now'
  try:
    return str(int(mins))
  except ValueError:
    return mins


def _build_line(color_tag: str, estimates: list[dict[str, Any]]) -> str:
  """Build a departure line like '[G] 8 14 31' fitting within model.cols."""
  base = color_tag + ' '
  parts: list[str] = []
  for est in estimates:
    t = _format_minutes(est['minutes'])
    if vestaboard._display_len(base + ' '.join(parts + [t])) > vestaboard.model.cols:  # noqa: SLF001
      break
    parts.append(t)
  return base + (' '.join(parts) if parts else '--')


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch BART departures and return a variables dict for template rendering.

  Returns keys: 'station', 'line1', and optionally 'line2'. Each value is a
  single-option list (no randomness — departure times are always current).
  """
  api_key = os.environ.get('BART_API_KEY', '').strip()
  if not api_key:
    raise RuntimeError('BART_API_KEY environment variable is not set')

  station_raw = os.environ.get('BART_STATION', '').strip()
  if not station_raw:
    raise RuntimeError('BART_STATION environment variable is not set')
  # Accept both raw codes (MLPT) and dropdown format (MLPT - Milpitas).
  station = station_raw.split()[0]

  dest_filters = [d for key in ('BART_LINE_1_DEST', 'BART_LINE_2_DEST') if (d := os.environ.get(key, '').strip())]
  if not dest_filters:
    raise RuntimeError('At least one of BART_LINE_1_DEST or BART_LINE_2_DEST must be set')

  r = requests.get(
    f'{_API_BASE}/etd.aspx',
    params={'cmd': 'etd', 'orig': station, 'key': api_key, 'json': 'y'},
    timeout=10,
  )
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    # Re-raise without the URL to avoid leaking the API key in logs.
    raise requests.HTTPError(f'BART API error: {e.response.status_code} {e.response.reason}') from None
  data = r.json()

  station_data = data['root']['station'][0]
  station_name: str = station_data['name']
  etds: list[dict[str, Any]] = station_data.get('etd', [])

  variables: dict[str, list[list[str]]] = {
    'station': [[station_name]],
  }

  for i, dest_filter in enumerate(dest_filters, 1):
    line_value = '--'
    for etd in etds:
      if dest_filter.lower() in etd['destination'].lower():
        estimates = etd.get('estimate', [])
        if estimates:
          color_tag = _LINE_COLOR_TAG.get(estimates[0].get('color', ''), '[ ]')
          line_value = _build_line(color_tag, estimates)
        break
    variables[f'line{i}'] = [[line_value]]

  return variables
