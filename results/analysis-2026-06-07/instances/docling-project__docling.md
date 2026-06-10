# docling-project/docling

- DA pass-rate: 21% (18/86) | RAT pass-rate: 35% (34/97) | bucket: DA_LOSS
- DA build_success/test_success: true/false | error_breakdown: ModuleNotFoundError (68 occurrences during collection)

## Failure stage & category

**Stage:** test_execution (or test_collection)  
**Category:** missing_runtime_or_test_deps

## Root cause (why DA lost)

DA's synthesizer failed to identify and install critical **system-level dependencies** required by docling's optional OCR and document processing backends. RAT explicitly installed `libgl1-mesa-glx`, `tesseract-ocr` (with language packs), and `ffmpeg`, plus set `TESSDATA_PREFIX`. DA only ran `uv sync --dev --all-extras`, which installed Python packages but not the native system libraries that easyocr, tesserocr, and other optional extras depend on. This caused 65 pytest collection failures due to missing C/C++ libraries (ModuleNotFoundError during import). RAT also explicitly ran `pip install -e ".[all]"` to editable-install the repo itself, whereas DA did not verify this install occurred.

## What RAT did differently

RAT's outer_commands shows these explicit steps DA omitted:
- `apt-get install -y -qq libgl1-mesa-glx tesseract-ocr tesseract-ocr-fra tesseract-ocr-deu tesseract-ocr-spa tesseract-ocr-eng ffmpeg`
- `export TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`
- `/repo/.venv/bin/pip install -q -e ".[all]"` (explicit editable install of the repo with all extras)
- RAT used `uv sync --frozen --group dev --all-extras` (with --frozen and explicit group selection) vs DA's `uv sync --dev --all-extras` (less strict)

## Evidence

- **DA Dockerfile:** Only `RUN uv sync --dev --all-extras`, no system package installation, no apt-get calls for OpenGL/Tesseract
- **DA error breakdown:** 68 ModuleNotFoundErrors out of errors collected
- **DA pytest output:** `collected 21 items / 65 errors` (65 collection failures)
- **RAT outer_commands.json:** Lines show `apt-get install -y -qq libgl1-mesa-glx tesseract-ocr...` at rc=0
- **RAT outer_commands.json:** Line shows `/repo/.venv/bin/pip install -q -e ".[all]"` at rc=0
- **RAT pytest output:** `collected 37 items / 60 errors` (37 tests collected vs DA's 21)
- **File:** `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/docling-project/docling/run_pytest_results.json` confirms ModuleNotFoundError as dominant error type
- **File:** `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/docling-project/docling/outer_commands.json` documents RAT's system package and editable install commands

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Enhance synthesizer to detect system-level optional dependencies:** When the agent's instructions reference OCR engines (easyocr, tesserocr, rapidocr) or image processing libraries (PIL with GPU support), trigger automatic installation of required system packages:
   - For tesserocr: install `tesseract-ocr` and language-data packages; set `TESSDATA_PREFIX`
   - For easyocr/PIL graphics: install `libgl1-mesa-glx` (OpenGL) and `libglib2.0-0`
   - For video processing: install `ffmpeg`

2. **Always explicitly editable-install the repo after uv sync:** Add `pip install -q -e ".[all]"` or equivalent to the Dockerfile after `uv sync`, ensuring the package itself is installed alongside its dependencies (not just dependencies downloaded).

3. **Use strict uv sync flags:** Change `uv sync --dev --all-extras` to `uv sync --frozen --group dev --all-extras` to match lock file pinning and explicit group selection.

4. **Add system lib detection to the pre-build analysis:** When parsing pyproject.toml [project.optional-dependencies], flag which system packages are required (tesseract, ffmpeg, etc.) and add apt-get install steps to the Dockerfile *before* uv sync.
