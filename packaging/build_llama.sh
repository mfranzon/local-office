#!/usr/bin/env bash
# Builds a self-contained llama-server (static libs, Metal shaders embedded) for the app bundle.
# Pinned to the llama.cpp build the models were tested with.
set -euo pipefail
TAG="${LLAMA_TAG:-b11960}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/build/llama.cpp"
OUT="$ROOT/build/bin"

if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 --branch "$TAG" https://github.com/ggml-org/llama.cpp.git "$SRC"
fi
cmake -S "$SRC" -B "$SRC/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0 \
  -DBUILD_SHARED_LIBS=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_NATIVE=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DCMAKE_C_FLAGS="-ffile-prefix-map=$SRC=llama.cpp" -DCMAKE_CXX_FLAGS="-ffile-prefix-map=$SRC=llama.cpp"  # no local paths in the binary
cmake --build "$SRC/build" --target llama-server -j
mkdir -p "$OUT"
cp "$SRC/build/bin/llama-server" "$OUT/"
if otool -L "$OUT/llama-server" | tail -n +2 | grep -v -E "/System/|/usr/lib/"; then
  echo "error: llama-server links non-system libraries (would break outside this Mac)"; exit 1
fi
echo "built $OUT/llama-server"
