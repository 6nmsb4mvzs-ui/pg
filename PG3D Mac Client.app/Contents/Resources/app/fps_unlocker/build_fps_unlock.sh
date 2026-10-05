#!/bin/sh
# Builds build/PG3DFPSUnlock.dylib (x86_64, like GameAssembly) without the macOS SDK.
# Needs zig (pip install ziglang; then use "python3 -m ziglang" as ZIG). The client
# ad-hoc signs the result on the Mac when it copies it into Application Support.
# build.sh is the Xcode build that also includes the Metal presentation probe.
set -eu
cd "$(dirname "$0")"
ZIG=${ZIG:-zig}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
$ZIG cc -target x86_64-macos.10.13 -Wl,-headerpad,0x4000 -dynamiclib -fblocks -O2 -Wall -Wextra -Werror \
  -Wl,-undefined,dynamic_lookup -Wl,-install_name,@rpath/PG3DFPSUnlock.dylib \
  src/pg3d_fps_unlock.c src/presentation_stub.c -o "$tmp/PG3DFPSUnlock.dylib"
mv -f "$tmp/PG3DFPSUnlock.dylib" build/PG3DFPSUnlock.dylib
echo "Built build/PG3DFPSUnlock.dylib"
