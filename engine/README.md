# Optional native transcription bridge

This directory contains the project-owned Windows C++23 bridge, not a bundled engine binary or model. Obtain muscriptor.cpp from https://github.com/DamRsn/muscriptor.cpp and follow its upstream build/dependency instructions. Place that checkout in vendor/muscriptor.cpp or pass MUSCRIPTOR_SOURCE_DIR to CMake.

With CMake >=3.24, Ninja and an appropriate Windows C++23 compiler:

```powershell
cmake -S engine -B build-engine -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=OFF
cmake --build build-engine
```

Select the resulting suno_engine.exe as the CPU engine in Settings. For a Vulkan build, use the upstream Vulkan build options and SDK, and configure it as the general engine. Upstream API changes may require matching the bridge to the upstream revision; a random upstream CLI executable is not interchangeable with this JSON-line bridge protocol. No downloads occur during app startup.

The bridge accepts model.gguf, mono 16 kHz float32 PCM, output.json and optional thread count. Model SHA-256 requirements are in core.py. Weights are obtained separately; upstream licensing remains applicable.
