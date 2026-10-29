#!/bin/sh
# Build the PS-side NPU consumer without replacing any running deployment.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$base/.." && pwd)
[ "$(uname -m)" = aarch64 ] || { echo 'Run on ARM64 with Icraft 3.36.1 development libraries' >&2; exit 2; }
if [ -n "${ICRAFT_ROOT:-}" ]; then
    set -- "-DCMAKE_PREFIX_PATH=$ICRAFT_ROOT" "$@"
fi
cmake -S "$base" -B "$repo/build/npu" -DCMAKE_BUILD_TYPE=Release "$@"
cmake --build "$repo/build/npu" --target icraft_npu_yolo --parallel "${JOBS:-1}"
sha256sum "$repo/build/npu/icraft_npu_yolo"
echo 'Built build/npu/icraft_npu_yolo; the running deployment has not changed.'
