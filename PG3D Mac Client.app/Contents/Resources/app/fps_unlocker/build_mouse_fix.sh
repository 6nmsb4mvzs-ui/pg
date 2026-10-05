#!/bin/sh
# Builds build/PG3DMouseFix.dylib (x86_64 + arm64) without the macOS SDK.
# Needs zig (pip install ziglang; then use "python3 -m ziglang" as ZIG). The client
# ad-hoc signs the result on the Mac when it copies it into Application Support.
set -eu
cd "$(dirname "$0")"
ZIG=${ZIG:-zig}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for arch in x86_64 aarch64; do
  $ZIG cc -target "$arch-macos.11.0" -dynamiclib -fblocks -O2 -Wall -Wextra -Werror \
    -Wl,-undefined,dynamic_lookup -Wl,-install_name,@rpath/PG3DMouseFix.dylib \
    src/pg3d_mouse_fix.c -o "$tmp/$arch.dylib"
done
python3 tools/make_fat.py build/PG3DMouseFix.dylib "$tmp/x86_64.dylib" "$tmp/aarch64.dylib"
echo "Built build/PG3DMouseFix.dylib"
