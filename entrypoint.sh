#!/bin/sh
# Translates environment variables into CLI flags so that Unraid's template UI
# (which works with env vars) can control runtime mode.
set -e

set --
[ "${FLAGSHIP:-}" = 'true' ]   && set -- "$@" --flagship
[ "${PUBLIC:-}" = 'true' ]     && set -- "$@" --public
[ -n "${CONTENT_ENABLED:-}" ]  && set -- "$@" --content-enabled "${CONTENT_ENABLED}"

exec python scheduler.py "$@"
