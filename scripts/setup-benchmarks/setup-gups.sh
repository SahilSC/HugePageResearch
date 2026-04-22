#!/bin/bash

set -o errexit
set -o nounset
set -o pipefail

GUPS_BENCHMARK_NAME="gups"
BENCHMARK_DIR_NAME="kernmlops-benchmark"

BENCHMARK_DIR="${BENCHMARK_DIR:-$HOME/$BENCHMARK_DIR_NAME}"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SOURCE_DIR="$REPO_ROOT/benchmark/$GUPS_BENCHMARK_NAME"
GUPS_BENCHMARK_DIR="$BENCHMARK_DIR/$GUPS_BENCHMARK_NAME"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "Missing benchmark source directory: $SOURCE_DIR" >&2
    exit 1
fi

mkdir -p "$GUPS_BENCHMARK_DIR"
cp -a "$SOURCE_DIR/." "$GUPS_BENCHMARK_DIR/"
make -C "$GUPS_BENCHMARK_DIR" clean
make -C "$GUPS_BENCHMARK_DIR"

echo "Installed GUPS benchmark at: $GUPS_BENCHMARK_DIR"
