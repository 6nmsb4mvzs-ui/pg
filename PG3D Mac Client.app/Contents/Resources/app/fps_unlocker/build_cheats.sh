#!/bin/sh
# Builds build/PG3DCheats.dylib (x86_64) without the macOS SDK.
# Same toolchain as build_fps_unlock.sh — needs zig (pip install ziglang).
set -eu
cd "$(dirname "$0")"
ZIG=${ZIG:-zig}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
$ZIG cc -target x86_64-macos.10.13 -Wl,-headerpad,0x4000 -dynamiclib -fblocks -O2 -Wall -Wextra \
  -Wno-unused-function \
  -Wl,-undefined,dynamic_lookup -Wl,-install_name,@rpath/PG3DCheats.dylib \
  src/pg3d_cheats.c -lm -o "$tmp/PG3DCheats.dylib"
mv -f "$tmp/PG3DCheats.dylib" build/PG3DCheats.dylib
echo "Built build/PG3DCheats.dylib"
