#!/usr/bin/env bash
# Builds a self-contained llama-server (static libs) for the app bundle: Metal on macOS, CUDA on Linux when nvcc is
# available (CPU otherwise).
# Pinned to the llama.cpp build the models were tested with.
set -euo pipefail
TAG="${LLAMA_TAG:-b11960}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/build/llama.cpp"
OUT="$ROOT/build/bin"

if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 --branch "$TAG" https://github.com/ggml-org/llama.cpp.git "$SRC"
fi
if [ "$(uname)" = Darwin ]; then
  PLATFORM=(-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0 -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON)
elif command -v nvcc >/dev/null; then
  PLATFORM=(-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHS:-native}")
  # older CUDA toolkits reject the newest gcc: use the newest host compiler they accept, when installed
  [ -n "${CUDAHOSTCXX:-}" ] || for v in 12 11; do command -v "g++-$v" >/dev/null && export CUDAHOSTCXX="g++-$v" && break; done
else
  PLATFORM=()
fi
cmake -S "$SRC" -B "$SRC/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release "${PLATFORM[@]}" \
  -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DCMAKE_C_FLAGS="-ffile-prefix-map=$SRC=llama.cpp" -DCMAKE_CXX_FLAGS="-ffile-prefix-map=$SRC=llama.cpp"  # no local paths in the binary
cmake --build "$SRC/build" --target llama-server -j
mkdir -p "$OUT"
cp "$SRC/build/bin/llama-server" "$OUT/"
if [ "$(uname)" = Darwin ] && otool -L "$OUT/llama-server" | tail -n +2 | grep -v -E "/System/|/usr/lib/"; then
  echo "error: llama-server links non-system libraries (would break outside this Mac)"; exit 1
fi
echo "built $OUT/llama-server"
