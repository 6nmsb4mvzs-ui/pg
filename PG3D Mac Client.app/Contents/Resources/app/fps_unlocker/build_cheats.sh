#!/bin/sh
# Builds build/PG3DCheats.dylib (x86_64) from PG3DAimAutoFire.mm.
# Requires macOS with Xcode or Command Line Tools (xcrun clang++).
# The pre-built dylib is shipped for users without Xcode; this script
# rebuilds it from source when the .mm file changes.
set -eu
cd "$(dirname "$0")"
xcrun clang++ -std=c++17 -arch x86_64 -mmacosx-version-min=11.0 \
  -dynamiclib -fblocks -fobjc-arc -O2 -Wall -Wextra -Werror \
  -framework AppKit -framework ApplicationServices \
  src/PG3DAimAutoFire.mm -o build/PG3DCheats.dylib.tmp
codesign --force --sign - build/PG3DCheats.dylib.tmp
mv -f build/PG3DCheats.dylib.tmp build/PG3DCheats.dylib
echo "Built build/PG3DCheats.dylib"
