#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$base/python.sh" "$base/runtime/service.py" status
