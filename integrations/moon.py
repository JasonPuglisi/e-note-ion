# integrations/moon.py
#
# Moon phase integration — pure-math calculation, no API or external dependency.
#
# Computes the current lunar phase using a known reference new moon epoch and
# the mean synodic period. Returns a 3×5 grid of [W]/[K] color squares
# representing the illuminated portion of the moon, suitable for display on
# the Note (3 rows × 15 cols) with text anchored to the right.
#
# Grid convention: waxing phases are right-lit, waning phases are left-lit,
# matching the northern-hemisphere view. New Moon uses an outline shape to
# remain visible against the dark board background.
#
# No config.toml keys required.

import math
from datetime import datetime, timezone

# Known new moon: 2000-01-06 18:14 UTC (J2000.0-era reference)
_NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)

# Mean synodic period in days
_SYNODIC_PERIOD = 29.53059

# 3×5 [W]/[K] grids for each of the 8 named phases.
# Each entry is (row1, row2, row3).
_GRIDS: dict[str, tuple[str, str, str]] = {
  'NEW MOON': (
    '[K][W][W][W][K]',
    '[W][K][K][K][W]',
    '[K][W][W][W][K]',
  ),
  'WAXING CRESCENT': (
    '[K][K][W][K][K]',
    '[K][K][W][W][K]',
    '[K][K][W][K][K]',
  ),
  'FIRST QUARTER': (
    '[K][K][W][W][K]',
    '[K][K][W][W][K]',
    '[K][K][W][W][K]',
  ),
  'WAXING GIBBOUS': (
    '[K][W][W][W][K]',
    '[K][W][W][W][W]',
    '[K][W][W][W][K]',
  ),
  'FULL MOON': (
    '[K][W][W][W][K]',
    '[W][W][W][W][W]',
    '[K][W][W][W][K]',
  ),
  'WANING GIBBOUS': (
    '[K][W][W][W][K]',
    '[W][W][W][W][K]',
    '[K][W][W][W][K]',
  ),
  'LAST QUARTER': (
    '[K][W][W][K][K]',
    '[K][W][W][K][K]',
    '[K][W][W][K][K]',
  ),
  'WANING CRESCENT': (
    '[K][K][W][K][K]',
    '[K][W][W][K][K]',
    '[K][K][W][K][K]',
  ),
}


def _phase(now: datetime) -> tuple[str, float]:
  """Return (phase_name, illumination_fraction) for the given UTC datetime."""
  elapsed = (now - _NEW_MOON_EPOCH).total_seconds() / 86400
  age = elapsed % _SYNODIC_PERIOD  # days since last new moon, 0–29.53

  # Illumination fraction via cosine of phase angle
  phase_angle = 2 * math.pi * age / _SYNODIC_PERIOD
  illumination = (1 - math.cos(phase_angle)) / 2

  # 8 equal buckets of ~3.69 days each
  bucket = int(age / _SYNODIC_PERIOD * 8) % 8
  names = [
    'NEW MOON',
    'WAXING CRESCENT',
    'FIRST QUARTER',
    'WAXING GIBBOUS',
    'FULL MOON',
    'WANING GIBBOUS',
    'LAST QUARTER',
    'WANING CRESCENT',
  ]
  return names[bucket], illumination


def get_variables() -> dict[str, list[list[str]]]:
  now = datetime.now(tz=timezone.utc)
  phase_name, illumination = _phase(now)
  row1, row2, row3 = _GRIDS[phase_name]
  return {
    'moon_row1': [[row1]],
    'moon_row2': [[row2]],
    'moon_row3': [[row3]],
    'phase_name': [[phase_name]],
    'illumination': [[f'{round(illumination * 100)}%']],
  }
