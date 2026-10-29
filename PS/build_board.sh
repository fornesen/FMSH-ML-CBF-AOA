#!/bin/sh
# Build the PS application on the ARM64 board; do not replace a running binary.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$base"
[ "$(uname -m)" = aarch64 ] || { echo 'Run on the ARM64 board with Icraft 3.36.1' >&2; exit 2; }
compiler=${CXX:-g++}
mkdir -p build/obj build/bin
for name in main app_config fft_capture capture_batch direction localization export aoa_ml3 pl_fft_input tcp_receiver; do
    echo "PS $name"
    "$compiler" --param ggc-min-expand=5 --param ggc-min-heapsize=16384 \
        -std=gnu++17 -O2 -Wall -Wextra -Werror -Isrc \
        -c "src/$name.cpp" -o "build/obj/$name.o"
done
"$compiler" build/obj/main.o build/obj/app_config.o build/obj/fft_capture.o \
    build/obj/capture_batch.o build/obj/direction.o build/obj/localization.o \
    build/obj/export.o build/obj/aoa_ml3.o build/obj/pl_fft_input.o build/obj/tcp_receiver.o \
    -pthread -lrt -licraft_zg330backend -licraft_xrt -licraft_xir -licraft_utils \
    -o build/bin/icraft_aoa_ml3
sha256sum build/bin/icraft_aoa_ml3
echo 'Built build/bin/icraft_aoa_ml3; the running deployment has not changed.'
