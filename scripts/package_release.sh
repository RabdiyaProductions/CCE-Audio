#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:-CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT}"
OUTPUT_ZIP="${2:-CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT_$(date +%Y%m%d).zip}"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Error: source directory '$SOURCE_DIR' does not exist." >&2
  exit 1
fi

# Prepare a clean build directory
rm -rf build
mkdir -p build

# Copy everything from the final output folder into build/
cp -a "$SOURCE_DIR"/. build/

# Create the ZIP from inside build (no extra top-level folder)
(
  cd build
  zip -r "../$OUTPUT_ZIP" .
)

echo "Created $OUTPUT_ZIP from contents of $SOURCE_DIR (no nested top-level folder)."
