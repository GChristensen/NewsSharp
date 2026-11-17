#!/bin/sh
# Starts NewsSharp from this directory (newssharp.sh): `manage.py serve` applies migrations and runs the web
# and worker processes. Arguments are passed to serve.
cd "$(dirname "$0")" || exit 1
PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
exec "$PYTHON" manage.py serve "$@"
