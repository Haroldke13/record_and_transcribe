# External microphone recording and speech transcription

Records the connected external USB microphone in continuous chunks, keeps every
raw capture, saves a speech-cleaned copy of each one, and transcribes both with
a local multilingual `faster-whisper` model. Nothing is sent to a remote service:
the model runs on the CPU of the machine doing the recording.

## Requirements

- Linux with PulseAudio or PipeWire (`pactl`)
- `ffmpeg` and `ffprobe`
- Python 3.9 or newer (`faster-whisper` requires it)
- An external USB microphone

On Debian or Ubuntu:

```bash
sudo apt install ffmpeg pulseaudio-utils python3-venv
```

## Setup

The Python environment and the model weights are not part of this repository —
the environment is tied to one machine and one Python version, and the weights
are roughly 900 MB. Create them once after cloning:

```bash
python3 -m venv .transcription_venv
./.transcription_venv/bin/python -m pip install --upgrade pip
./.transcription_venv/bin/python -m pip install -r requirements-transcription.txt
```

The speech model downloads itself into `.speech_models` the first time a
transcription runs, so leave the first run online. Later runs work offline.

If the recorder reports that transcription dependencies are unavailable after a
system Python upgrade, delete `.transcription_venv` and repeat the steps above.

## Recording

Run `./record_external_microphone.sh` to record until you press Ctrl+C. The
recorder uses the connected external USB microphone and continuously closes one
file every 10 minutes, so cleanup and transcription do not interrupt capture.
Pass a number of seconds to record for a fixed duration instead:

```bash
./record_external_microphone.sh 30
```

For every finalized chunk, the timestamped raw WAV remains in this folder. An
identically named, 16 kHz speech-focused WAV is saved under
`CLEANED_AUDIO_RECORDINGS`. Transcripts of both versions retain the same
timestamped basename and are saved as readable TXT, timestamped SRT, and
structured JSON files under:

- `TRANSCRIPTS/RAW_AUDIO_RECORDINGS`
- `TRANSCRIPTS/CLEANED_AUDIO_RECORDINGS`

The local multilingual `faster-whisper` model automatically detects English,
Swahili, and other supported spoken languages and transcribes in the detected
language rather than translating to English. Voice cleanup reduces steady noise,
rumble, long silence, and low-level background audio. Loud music that directly
overlaps a voice cannot always be perfectly separated, so the untouched raw WAV
is always retained. Low-confidence model guesses are excluded from TXT and SRT
instead of being presented as speech; their confidence details remain recorded
in the JSON metadata for audit.

## Processing existing recordings

To process finalized recordings already in this folder, run:

```bash
./process_all_recordings.sh
```

An audio file still open for writing is skipped safely. Run the command again
after that recording has been stopped and finalized. Use
`./process_all_recordings.sh --force` only when you intentionally want to rebuild
all cleaned audio and transcript files.

## Layout

| Path | Purpose | Tracked in git |
| --- | --- | --- |
| `record_external_microphone.sh` | Chunked capture from the USB microphone | yes |
| `process_all_recordings.sh` | Batch cleanup and transcription | yes |
| `audio_pipeline.py` | Cleaning filter chain and transcription | yes |
| `requirements-transcription.txt` | Pinned Python dependency | yes |
| `.transcription_venv/` | Local Python environment | no — rebuilt locally |
| `.speech_models/` | Downloaded model weights | no — fetched on first run |
| `*.wav`, `CLEANED_AUDIO_RECORDINGS/`, `TRANSCRIPTS/` | Recordings and their output | no — kept local |

Recordings and transcripts are deliberately excluded from version control.
Audio captured from a live microphone is personal data, and committing it would
publish it along with the code.

## Configuration

| Variable | Effect | Default |
| --- | --- | --- |
| `RECORDING_CHUNK_SECONDS` | Length of each autosaved chunk | `600` |
| `EXTERNAL_MIC_SOURCE` | Force a specific PulseAudio source | first USB input |
