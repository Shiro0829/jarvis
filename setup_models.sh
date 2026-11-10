#!/bin/zsh
# One-time download of the free local models: the Ollama brain and the Kokoro voice.
set -e
cd "${0:A:h}"
source .env 2>/dev/null || true
MODEL="${VA_OLLAMA_MODEL:-bonsai-2:27b}"

command -v ollama >/dev/null || brew install ollama
brew services start ollama >/dev/null 2>&1 || true   # runs in the background and starts at login
ollama pull "$MODEL"

mkdir -p models
BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
[ -f models/kokoro-v1.0.onnx ] || curl -fL -o models/kokoro-v1.0.onnx "$BASE/kokoro-v1.0.onnx"
[ -f models/voices-v1.0.bin ]  || curl -fL -o models/voices-v1.0.bin  "$BASE/voices-v1.0.bin"
echo "Done. Start Jarvis with ./run.sh"
