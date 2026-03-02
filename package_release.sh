#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT"
BUILD_DIR="${TMPDIR:-/tmp}/cce_audio_build"
DIST_DIR="dist"
ZIP_NAME="CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT_$(date +%Y%m%d).zip"

if [[ ! -d "$OUTPUT_DIR" ]]; then
  echo "Error: expected output directory '$OUTPUT_DIR' was not found." >&2
  exit 1
fi

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Copy only contents so archive root is flat (no nested top-level folder)
cp -a "$OUTPUT_DIR"/. "$BUILD_DIR"/
(
  cd "$BUILD_DIR"
  zip -r "$OLDPWD/$DIST_DIR/$ZIP_NAME" .
)

echo "Created $DIST_DIR/$ZIP_NAME from contents of $OUTPUT_DIR (flat archive root)."
