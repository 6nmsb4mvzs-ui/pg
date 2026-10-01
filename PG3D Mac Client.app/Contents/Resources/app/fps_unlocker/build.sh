#!/bin/zsh

set -euo pipefail

script_dir=${0:A:h}
output_dir="$script_dir/build"
output_file="$output_dir/PG3DFPSUnlock.dylib"

mkdir -p "$output_dir"
temporary_dir=$(mktemp -d "$output_dir/.build.XXXXXX")
temporary_output="$temporary_dir/PG3DFPSUnlock.dylib"
cleanup() {
  [[ ! -f "$temporary_output" ]] || rm -f -- "$temporary_output"
  rmdir -- "$temporary_dir" 2>/dev/null || true
}
trap cleanup EXIT

clang \
  -arch x86_64 \
  -dynamiclib \
  -install_name @rpath/PG3DFPSUnlock.dylib \
  -fPIC \
  -fblocks \
  -fobjc-arc \
  -O2 \
  -Wall \
  -Wextra \
  -Werror \
  -mmacosx-version-min=10.13 \
  "$script_dir/src/pg3d_fps_unlock.c" \
  "$script_dir/src/presentation_probe.m" \
  -framework AppKit -framework QuartzCore -framework Metal \
  -o "$temporary_output"

codesign --force --sign - "$temporary_output" >/dev/null
# Rename a newly built file, so a running game can keep its old mapped inode.
mv -f -- "$temporary_output" "$output_file"
clang -arch x86_64 -arch arm64 -O2 -Wall -Wextra -Werror \
  "$script_dir/src/launch_game.c" -o "$temporary_dir/launch-game"
codesign --force --sign - "$temporary_dir/launch-game" >/dev/null
mv -f -- "$temporary_dir/launch-game" "$output_dir/launch-game"

echo "Built $output_file"
file "$output_file"
