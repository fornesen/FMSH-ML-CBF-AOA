#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "$base/python/bin/python3" ] || [ ! -f "$base/lib/ld-linux-aarch64.so.1" ]; then
    echo 'V2.0 private ARM64 Python/libraries are incomplete; collect dependencies first.' >&2
    exit 2
fi
export PYTHONHOME="$base/python" PYTHONPATH="$base" PYTHONNOUSERSITE=1
exec "$base/lib/ld-linux-aarch64.so.1" --library-path "$base/lib" "$base/python/bin/python3" -B "$@"
