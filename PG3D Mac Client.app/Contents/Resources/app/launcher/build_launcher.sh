#!/bin/sh
# Builds the app's main executable, Contents/MacOS/PG3DMacClient (x86_64 + arm64),
# without the macOS SDK. Needs zig (pip install ziglang; then "python3 -m ziglang" as ZIG).
set -eu
cd "$(dirname "$0")"
ZIG=${ZIG:-zig}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for arch in x86_64 aarch64; do
  $ZIG cc -target "$arch-macos.11.0" -Wl,-headerpad,0x4000 -O2 -Wall -Wextra -Werror src/pg3d_launcher.c -o "$tmp/$arch"
done
out=../../../MacOS/PG3DMacClient
python3 ../fps_unlocker/tools/make_fat.py "$out" "$tmp/x86_64" "$tmp/aarch64"
chmod 755 "$out"
echo "Built $out"
