# record_and_transcribe

Bash + Python pipeline that records a USB microphone in chunks and transcribes each chunk locally with faster-whisper.

## What it does

Three files do the work:

- `record_external_microphone.sh` — picks a PulseAudio/PipeWire source whose name
  starts with `alsa_input.usb-` (the default source if it matches, otherwise the
  first USB input found; `EXTERNAL_MIC_SOURCE` overrides this). It unmutes the
  source, sets its volume to 100%, makes it the default, then runs `ffmpeg -f pulse`
  with `-f segment` to write consecutive mono 48 kHz 16-bit WAV chunks. Chunk
  length defaults to 600 seconds (`RECORDING_CHUNK_SECONDS`). Chunks are written
  under a hidden `.raw_<session>_<timestamp>.wav` name while open; once a chunk is
  closed it is renamed to `DATE__start-TIME__end-TIME__external_microphone.wav`
  and handed to the Python pipeline. The still-open chunk is never touched.
  With no argument it records until Ctrl+C; with a numeric argument it records
  that many seconds.
- `process_all_recordings.sh` — a thin wrapper that execs
  `.transcription_venv/bin/python audio_pipeline.py --all "$@"`, so it reprocesses
  finalized recordings already sitting in the folder.
- `audio_pipeline.py` — for each input audio file it:
  1. Runs an ffmpeg filter chain (`highpass=80`, `lowpass=7600`, `afftdn` noise
     reduction, `agate`, two `equalizer` bands, `acompressor`, `silenceremove`,
     `loudnorm`, `alimiter`) to produce a 16 kHz speech-focused copy in
     `CLEANED_AUDIO_RECORDINGS/`.
  2. Transcribes both the raw and the cleaned file with a local `faster-whisper`
     `WhisperModel` (default model `small`, `device="cpu"`, `compute_type="int8"`,
     weights cached in `.speech_models/`). VAD filtering is on, `task="transcribe"`
     (so detected non-English speech stays in its own language rather than being
     translated), and language is auto-detected unless `--language` is given.
  3. Writes three files per transcript into
     `TRANSCRIPTS/RAW_AUDIO_RECORDINGS/` and `TRANSCRIPTS/CLEANED_AUDIO_RECORDINGS/`:
     a plain `.txt`, a timestamped `.srt`, and a `.json` with per-segment
     timings, `avg_logprob`, `no_speech_prob`, detected language and the
     confidence policy used.
  4. Drops low-confidence output from the TXT and SRT: segments are rejected if
     the detected language probability is below 0.50 or the segment average log
     probability is below -0.80. Rejected segments are still recorded in the
     JSON under `rejected_segments` with a reason, so nothing is silently lost.

Files still open for writing are detected and skipped, and outputs are not
rebuilt if they are newer than their source unless `--force` is passed.

Everything runs locally; no audio leaves the machine except the one-off model
weight download on first run.

## Tech stack

- Bash (the two recorder/batch scripts), Python 3.9+ (`audio_pipeline.py`, 504 lines)
- `faster-whisper==1.2.1` — the only pinned Python dependency
  (`requirements-transcription.txt`)
- System tools invoked as subprocesses: `ffmpeg`, `ffprobe`, `pactl`, plus
  `awk`, `date`, `find`, `sort`, `ps` (the recorder checks for all of these and
  exits if any is missing)
- PulseAudio or PipeWire (`pactl`) for source selection

There is no `setup.py`, `pyproject.toml`, Dockerfile, or test suite.

## Setup and running

System packages (Debian/Ubuntu):

```bash
sudo apt install ffmpeg pulseaudio-utils python3-venv
```

Python environment — the scripts hardcode the venv path `.transcription_venv/`
inside the repo and will refuse to run without it:

```bash
cd record_and_transcribe
python3 -m venv .transcription_venv
./.transcription_venv/bin/python -m pip install --upgrade pip
./.transcription_venv/bin/python -m pip install -r requirements-transcription.txt
```

Record (Ctrl+C to stop, or pass seconds):

```bash
./record_external_microphone.sh
./record_external_microphone.sh 30
```

Reprocess finalized recordings already in the folder:

```bash
./process_all_recordings.sh
./process_all_recordings.sh --force   # rebuild outputs that are already current
```

Call the pipeline directly for more control:

```bash
./.transcription_venv/bin/python audio_pipeline.py --audio some.wav \
  --model small --cpu-threads 4
```

Environment variables:

| Variable | Used by | Effect | Default |
| --- | --- | --- | --- |
| `RECORDING_CHUNK_SECONDS` | recorder | Seconds per autosaved chunk | `600` |
| `EXTERNAL_MIC_SOURCE` | recorder | Force a specific PulseAudio source name | first `alsa_input.usb-*` |
| `AUDIO_TRANSCRIPTION_MODEL` | pipeline | Default faster-whisper model | `small` |

First run needs network access so `faster-whisper` can download the model into
`.speech_models/` (roughly 900 MB for `small`). Later runs are offline.

`.gitignore` deliberately excludes `.transcription_venv/`, `.speech_models/`,
all audio extensions, `CLEANED_AUDIO_RECORDINGS/` and `TRANSCRIPTS/`, because
microphone captures are personal data.

## Status

Working. Single commit, 2026-09-10, so it is new rather than abandoned.

Verified here:

- `audio_pipeline.py` compiles and `--help` runs cleanly against an installed
  `faster_whisper`, so the imports and the argument parser are sound.
- Both shell scripts are internally consistent: the venv path, the pipeline
  path, and the output directory names all match what `audio_pipeline.py` uses.
- No TODO/FIXME markers anywhere in the code.

Not verified here:

- An actual end-to-end recording and transcription run. That needs a physically
  connected USB microphone and a PulseAudio/PipeWire session, neither of which
  was available. TODO: verify the capture, chunk-rotation and Ctrl+C paths on
  real hardware.
- The quality claims about the cleaning chain (noise, rumble, silence, music
  bleed). The filter chain is real and readable in the source, but its output
  has not been listened to. TODO: verify.
- Behaviour when a chunk boundary falls mid-sentence — the pipeline transcribes
  each chunk independently with `condition_on_previous_text=False`, so speech
  spanning a boundary is likely split across two transcripts. TODO: verify
  whether that matters for the intended use.

## Licence

**Proprietary software — all rights reserved.** Copyright © 2026 Joel Harold Onyango.

This repository is not open source. The full terms are in [LICENSE](LICENSE); in
summary, you may not copy, redistribute, modify, sublicense, publish, re-host or
commercially exploit this software, in whole or in part, without the prior
written permission of the copyright holder. Access to this repository does not
grant any licence beyond reading it.
