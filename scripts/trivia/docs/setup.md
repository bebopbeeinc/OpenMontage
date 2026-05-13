# Trivia Pipeline — Setup

What you need to install before the first `python scripts/trivia/openart_generate.py 17`
or `uvicorn web.server:app` succeeds. Aimed at fresh-clone /
fresh-machine setup. Read this once; subsequent runs reuse everything.

## TL;DR

```bash
# 1. system tooling (Python 3.11+, Node 20+, ffmpeg)
brew install python@3.11 node ffmpeg                # macOS; equivalent on linux

# 2. clone + Python deps
git clone <repo-url> && cd OpenMontage
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 3. Remotion deps (one-time per clone)
(cd remotion-composer && npm install)

# 4. Claude auth — pick ONE:
#    a) Claude Code CLI (uses your Claude subscription; no API key)
curl -fsSL https://claude.ai/install.sh | bash
claude    # one-time interactive login
#    b) Anthropic API key (paid, separate from subscription)
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env

# 5. Google service-account key (Sheets + Drive)
mkdir -p ~/.google
# Drop claude-sheets-sa.json into ~/.google/  (or override via $OPENMONTAGE_SA_PATH)

# 6. Piper TTS voice (default provider)
python scripts/piper_voices/fetch.py en_US-ryan-high

# 7. First-time OpenArt login (headed Chromium for manual sign-in)
python scripts/trivia/openart_generate.py <row> --segments reaction --variants 1
#    -> opens Chromium, log in, leave it open until the script proceeds.
#       After this run, .playwright/openart-state.json persists the session.

# verify
python -c "import sys; sys.path.insert(0, 'scripts/trivia'); \
  from post_row import build_sheets, read_post_row; \
  print(read_post_row(build_sheets(), 5))"
```

If those eight steps succeeded, you can run a full render via the web UI:

```bash
uvicorn web.server:app --port 8765 --reload
# open http://127.0.0.1:8765/trivia/
```

Sections below explain each step.

## 1. System tooling

| Tool | Version | Why |
|---|---|---|
| **Python** | 3.11+ (the repo's `.venv` is pinned to 3.11) | All scripts. faster-whisper + piper-tts wheels exist for 3.11–3.12. |
| **Node.js** | 20+ (24 is fine — what the maintainer runs) | Remotion CLI (`npx remotion render`) and the npm install in `remotion-composer/`. |
| **ffmpeg** | any recent | `assemble_modular.py` shells out for normalize / xfade-concat / mux. The Remotion render path also needs it. |
| **git** | any | for cloning |

macOS install via Homebrew:

```bash
brew install python@3.11 node ffmpeg
```

Linux equivalent (apt-based):

```bash
sudo apt install python3.11 python3.11-venv nodejs npm ffmpeg
```

## 2. Repository + Python venv

```bash
git clone <repo-url>
cd OpenMontage
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The `.venv/` is gitignored; create a fresh one per machine.

Then download Playwright's Chromium binary (the OpenArt driver runs against this exact build, not your system browser):

```bash
playwright install chromium
```

(`playwright install` without an arg pulls all three browsers — chromium is the only one we use.)

## 3. Remotion / Node deps

The Remotion compositions live under `remotion-composer/`. One-time `npm install`:

```bash
cd remotion-composer
npm install
cd ..
```

Verify with a dry-render:

```bash
cd remotion-composer
npx remotion compositions src/index-trivia.tsx
# expected output: TriviaWithBg ... 1080×1920 @ 30fps, 402 frames
```

## 4. Claude auth

Three scripts (`feedback_router.py`, `shorten_vo.py`, `pick_reactions_llm.py`)
call Claude for routing/rewriting decisions. They check the Anthropic SDK
first, then fall back to the local `claude` CLI. **Pick one** path —
either works.

### Option A — `claude` CLI (recommended, uses your subscription)

```bash
# Install (macOS / linux):
curl -fsSL https://claude.ai/install.sh | bash
# or via npm
npm install -g @anthropic-ai/claude-code

# One-time OAuth login (opens browser, no API key needed):
claude
# After logging in, exit the interactive session — the auth persists.
```

Verify:

```bash
claude --print "say hi"
# should print a one-line greeting
```

The pipeline uses the CLI in `--output-format json --json-schema ...` mode for
structured outputs (see `_call_via_cli` in `feedback_router.py` and
`pick_reactions_llm.py`). No special CLI configuration needed.

### Option B — Anthropic API key (separate billing)

Get a key at <https://console.anthropic.com/settings/keys>, then either:

```bash
# Export in your shell
export ANTHROPIC_API_KEY="sk-ant-..."

# Or drop into .env (NOT auto-loaded — the trivia scripts read os.environ directly,
# so either source .env into your shell or put the export in ~/.zshrc).
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
```

The SDK path bills the Anthropic API (small — typical feedback router call is a few cents).

### Both?

If both are configured, the scripts prefer the SDK (faster, supports `thinking={"type":"adaptive"}` for pick_reactions_llm). The CLI fallback only fires when `$ANTHROPIC_API_KEY` is empty.

## 5. Google service account (Sheets + Drive)

The Posts spreadsheet, Clips library, and Drive deliverable folder are all
accessed via a Google Cloud service account. The pipeline reads + writes
the sheet, and uploads/replaces rendered videos to Drive.

### Get the credentials JSON

The service account `claude-sheets-config@travel-crush.iam.gserviceaccount.com`
already exists for this project. Ask the maintainer for the JSON file.

Default path: `~/.google/claude-sheets-sa.json`

```bash
mkdir -p ~/.google
# Drop claude-sheets-sa.json into ~/.google/
```

Override the path with `$OPENMONTAGE_SA_PATH` if you keep credentials elsewhere
(e.g., 1Password CLI sourcing to a `~/.config/secrets/` directory):

```bash
export OPENMONTAGE_SA_PATH="/path/to/your/sa-key.json"
```

### Share access on the right resources

The service-account email must have:

| Resource | Permission |
|---|---|
| Posts sheet (`1EzucrS6yUPfodtt7WVuvW3PjZ1yhWUgfWUowPkMP6Eg`) | **Editor** (pipeline writes Final Status, Final Video Link, Reaction VLOOKUP formulas, Emphasis Override, Question rewrites) |
| Clips sheet (`1E19Pv9ur0KsgHxny65rX_CXsT-yHPkbyhqjZTEvJG_E`) | **Viewer** (read-only catalog) |
| Hooks sheet (`1lwnBldh_fMAKHWMxQbzRxJ7GQ9m6wf35GR9bRCann8I`) | **Viewer** |
| Drive deliverable folder (`1930CVitXd4d6BsZ39EleWyxmtsgaXVGY`) | **Editor** (publish.py uploads/replaces) |

If you're spinning up a fresh service account in your own GCP project,
enable the Sheets API and Drive API for it, then share each resource with
the new email.

### Verify

```bash
source .venv/bin/activate
python -c "
import sys
sys.path.insert(0, 'scripts/trivia')
from post_row import build_sheets, read_post_row
row = read_post_row(build_sheets(), 5)
print(f'row 5 ok — post: {row[\"post\"]!r}')
"
```

Should print something like `row 5 ok — post: 'australia-wider-than-moon'`.
If you see `HttpError 403`, the sheet isn't shared with the service account.

## 6. Piper TTS voice (optional but default)

The assemble step uses Piper for local TTS by default. The trivia default is
`en_US-ryan-high` — ~115 MB of `.onnx` weights, male broadcaster-style
narration. Gitignored, fetched per machine; piper_tts also auto-fetches on
first use, so this step is only needed if you want to pre-warm before going
offline.

```bash
python scripts/piper_voices/fetch.py en_US-ryan-high
```

Verify:

```bash
echo "hello world" | .venv/bin/python -m piper \
  --model .piper_voices/en_US-ryan-high.onnx \
  --output-file /tmp/piper-test.wav
afplay /tmp/piper-test.wav   # macOS; aplay on linux
```

Pick a different voice with `--piper-model` on the assemble step:

```bash
python scripts/trivia/assemble_modular.py ... \
  --piper-model .piper_voices/en_GB-alan-medium.onnx
```

Audition options first: `python scripts/piper_voices/fetch.py --list` shows
what's cached, and `scripts/piper_voices/sample_libritts.py` lets you audition
the 904 LibriTTS multi-speaker model by gender.

### Alternate — ElevenLabs

If you'd rather use ElevenLabs (cloud, paid), pass `--tts-provider elevenlabs`
to assemble and set:

```bash
export ELEVENLABS_API_KEY="..."
```

The voice ID is configurable via `--voice-id`; defaults are in `assemble_modular.py`.

## 7. OpenArt login (first run)

`scripts/trivia/openart_generate.py` (video clips) and
`scripts/trivia_images/openart_image_driver.py` (question images) drive
openart.ai via Playwright. The first run is headed: Chromium opens to the
sign-in page, you log in once, and the session persists in
`.playwright/openart-state.json` for all subsequent runs (including headless).

```bash
# Pick any prepared row and run with one segment as a smoke test:
python scripts/trivia/openart_generate.py <row> --segments reaction --variants 1
```

`.playwright/` is gitignored — the auth state is a credential (cookies +
localStorage). Don't commit it. Each new machine does its own first-run
login.

### Required OpenArt access

The workspace needs:

- **Seedance 2.0** model access (body + closer clips)
- **HappyHorse** model access (reaction selfie clips)
- **Nano Banana Pro** model access (for the `scripts/trivia_images/` pipeline if you use it)

These are tier-gated on OpenArt. If a model isn't visible to your
account, openart_generate will fail at the model-picker step.

## 8. Music library (optional)

`assemble_modular.py --with-music` reads from `music_library/` (gitignored).
Drop your royalty-free tracks there:

```bash
mkdir -p music_library
# Put MP3s/WAVs in music_library/, named by mode:
#   music_library/bed_facts.mp3
#   music_library/bed_choices.mp3
# Or override per-row via Posts!Z (music_track column).
```

If `music_library/` is empty, `--with-music` falls back to silence (with a warning).

## 9. SFX library

The repo ships `sfx_library/` with the trivia-specific cue files
(`whoosh.wav`, `slam_check.wav`, `tick_loop.wav`, etc.). Nothing to fetch —
`pip install -r requirements.txt` doesn't touch it; they're committed in
the repo.

## 10. Verification — render a row

Once steps 1–8 are done:

```bash
source .venv/bin/activate

# 1) Local pipeline run for an existing row with all assets in the library
python scripts/trivia/assemble_modular.py <row> <slug> \
  --with-vo --with-music --with-sfx --silent-hook
python scripts/common/transcribe.py <slug>
(cd remotion-composer && \
  npx remotion render src/index-trivia.tsx TriviaWithBg \
    ../projects/<slug>/renders/final_with_bg.mp4)

# 2) Or use the web UI for the full flow:
uvicorn web.server:app --port 8765 --reload
# open http://127.0.0.1:8765/trivia/
```

If the render lands at `projects/<slug>/renders/final_with_bg.mp4`, setup is good.

## Common gotchas

- **`ANTHROPIC_API_KEY not set — cannot call Claude`**: both auth paths failed. Either
  install the `claude` CLI (step 4a) or set `ANTHROPIC_API_KEY` (step 4b).
- **`Could not find 'Posts' tab` / `HttpError 403`**: service account isn't shared
  on the sheet. Add `claude-sheets-config@travel-crush.iam.gserviceaccount.com`
  as editor.
- **Drive upload 403/404**: same — share the Drive folder with the service account.
- **`playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist`**:
  ran `pip install` but skipped `playwright install chromium`.
- **Piper "model not found"**: voice files weren't downloaded — re-run step 6.
- **`Sign up to create for FREE` on OpenArt run**: the persisted login expired.
  Delete `.playwright/openart-state.json` and re-run a headed `openart_generate.py`
  to log in again.
- **`Sheet update 400 "exceeds grid limits"`**: the Posts tab doesn't have a row at the
  number you passed. Add a row in the sheet first, then re-run.

## Related docs

- `scripts/trivia/docs/trivia-video-workflow.md` — full operational walkthrough (idea → publish)
- `scripts/trivia/docs/openart-prompt-template.md` — the OpenArt prompt formula
- `pipeline_defs/trivia-short.yaml` — pipeline manifest (stages, success criteria)
- `skills/pipelines/trivia-short/` — per-stage director skills
- `AGENT_GUIDE.md` — overall OpenMontage operating guide
