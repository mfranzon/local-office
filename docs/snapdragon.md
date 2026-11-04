# Optional Windows ARM64 Snapdragon support

This checkout includes a native Windows ARM64 configuration for Snapdragon X Elite.
Open **Start Local Office.cmd** to launch the desktop app. For a fresh setup, run
`powershell -ExecutionPolicy Bypass -File scripts\setup_snapdragon.ps1` first.
Setup requires ARM64 Python 3.12, uv, and Node.js 22.12+; it downloads the model assets once.
Validated on Windows with a Snapdragon X Elite X1E80100 and 32 GB RAM;
other Qualcomm generations and fresh-machine installs have not been validated.
The Windows shell uses ARM64 Electron with an isolated renderer, a native folder
picker, and a private loopback Python service that stops when the app exits.

| Task | Model | Runtime |
|---|---|---|
| Chat, cited answers, wiki and skills | Qwen3-4B-Instruct-2507 | GenieX QAIRT, NPU |
| Scans and document OCR | LightOnOCR-2-1B Q8_0 + F16 projector | GenieX llama.cpp, NPU |
| Document search embeddings | Nomic-Embed-Text v1.5, 512 dimensions | Qualcomm AI Hub ONNX export, QNN NPU |
| Images and charts | Qwen3-VL-4B-Instruct Q4_0 + F16 projector | GenieX llama.cpp, NPU |

The app uses the verified GenieX 0.10.0 runtime, with `GGML_HEXAGON_DEVICES=HTP0`.
Nomic stays loaded in its own worker alongside one generative model. Inference
calls take turns under a shared lock; OCR/indexing and skill searches reuse both
workers instead of reloading the weights at every embedding call. Generative
role changes still replace the generative worker. Workers stop after idle time
or a fatal native failure; recoverable request errors retain the loaded model.
Host preprocessing, sampling and auxiliary vision operators may still use the CPU.
The Nomic session requires NPU placement with CPU fallback disabled.
The regular app keeps llama-server as its default on every platform. The
Snapdragon launcher explicitly sets `LOCAL_OFFICE_BACKEND=snapdragon` after
the separate setup; there is no automatic backend switch based on architecture.

### How this differs from existing Windows x64 support

| | Existing Windows x64 | Windows ARM64 Snapdragon |
|---|---|---|
| Desktop | pywebview / WebView2; existing Inno Setup installer | Electron ARM64; source-checkout launcher |
| Inference transport | OpenAI-compatible HTTP to a `llama-server.exe` per role | JSONL to isolated Python workers using native runtime APIs |
| Runtime | Official llama.cpp build; CUDA 12.4 by default, CPU fallback; optional Vulkan/CPU builds | GenieX QAIRT for the assistant, GenieX llama.cpp Hexagon for OCR/vision, ONNX Runtime QNN for embeddings |
| Scheduling | Servers start on demand and can stay loaded together | Resident embedder plus one generative model; serialized inference |
| Model roles | Assistant (also handles images), OCR, embeddings | Assistant, OCR, embeddings, separate general vision role |

The Qualcomm path does not launch `llama-server.exe`. OCR and general vision
still use llama.cpp through GenieX's native API. The existing Windows x64 build
scripts, WebView2 entry point, llama-server download/launch arguments and HTTP
inference path are preserved. GenieX/QNN dependencies are an optional
`snapdragon` extra; the x64 installer continues to use the base dependencies.

Regression tests exercise Windows x64/ARM64 backend selection, the ARM64 llama
opt-out, existing saved model compatibility, llama-server supervision routing,
and HTTP completion, image streaming and embeddings. HTTP responses and x64
architecture detection are simulated on the ARM64 test machine. A Windows x64
installer launch and NVIDIA/CUDA execution have not been retested on x64 hardware.
The Snapdragon launch scripts explicitly select the Qualcomm backend; use the
regular entry point without this flag for llama-server. Keep each backend's data directory
separate because their embedding models and index dimensions differ.

Data is kept in `data/snapdragon/`, model downloads in `models/` and the GenieX
cache, and runtime files in `build/geniex/`. These are excluded from Git. Documents
stay on the computer. Setup and model downloads require an internet connection;
normal inference uses local files. The preinstalled GenieX CLI is not modified.

The runtime ZIP and AI Hub Nomic ZIP are checked against SHA-256 constants
committed in `backend/app/provision.py` before extraction. The runtime pin is
the v0.10.0 GitHub release asset digest; the Nomic pin covers the v0.65.0 ONNX
archive. Setup revalidates older installs lacking the verification marker.
Failed downloads cancel queued chunks and retain resumable progress.

Windows ARM64 uses exact NumPy cosine search alongside SQLite FTS5 because
sqlite-vec does not publish an ARM64 Windows wheel. This is suitable for small
office libraries; very large indexes will benefit from a native vector extension.

To run the API and UI without the desktop window:

```powershell
scripts\start_snapdragon.ps1 -ServerOnly -Port 8765
# Open http://127.0.0.1:8765
```

Validation commands:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm run build
```

`scripts/check_snapdragon.py` exercises the actual installed models;
`scripts/smoke_app.py` checks OCR ingestion, cited answers, wiki extraction and
an Excel export against a running server using a labeled synthetic invoice.
The model-check script selects the Snapdragon backend and defaults `MODELS_DIR`
to this checkout's `models` directory.
Image understanding remains probabilistic: use the OCR/document workflow for
exact transcription and verify cited source pages for important facts.
Length-limited OCR is reported as an error rather than storing a partial page;
split a dense page into smaller images and retry if it exceeds the model context.
Other responses also signal length/context exhaustion. The requested output
budget is bounded by available context, including image tokens. System prompts
and tool schemas are protected; older tool results are shortened first. Invalid
tool calls and schema output get at most two repair attempts with alternating
assistant/user turns. OCR and table extraction use a neutral repetition penalty.

## Scope of the optional platform addition

The Mac app remains the primary application. Its README introduction, model
catalog, native entry point, packaging scripts and frontend dependency manifests
are preserved. Electron is installed only from `desktop/package.json` by the
Snapdragon setup script; normal frontend builds do not install it. NumPy's new
direct dependency is limited to Windows ARM64. GenieX/QNN are installed only
with the `snapdragon` Python extra and are loaded in worker processes on demand.

Shared application modules contain the routing hooks needed to select the extra
model provider and expose its fourth role. The native sqlite-vec query path,
default wiki prompts/windowing and shared UI styles keep their existing behavior;
ARM64 vector search, shorter NPU wiki windows and Electron layout adjustments
are confined to their corresponding platform paths. No Mac runtime or installer
is replaced by the Snapdragon launcher.
