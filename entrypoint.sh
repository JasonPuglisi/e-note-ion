#!/bin/sh
# Translates FLAGSHIP and PUBLIC environment variables into CLI flags so that
# Unraid's template UI (which works with env vars) can control runtime mode.
set -e

args=''
[ "${FLAGSHIP:-}" = 'true' ] && args="$args --flagship"
[ "${PUBLIC:-}" = 'true' ]   && args="$args --public"

# shellcheck disable=SC2086
exec python e-note-ion.py $args
