#!/usr/bin/env bash

# Clean and transcribe every finalized raw recording. An audio file still open
# for writing is reported and safely skipped; run this script again after it is
# closed. Pass --force to rebuild outputs that are already current.

set -u
set -o pipefail

recordings_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null && pwd -P)"
python_path="$recordings_dir/.transcription_venv/bin/python"
pipeline_path="$recordings_dir/audio_pipeline.py"

if [[ ! -x "$python_path" ]]; then
    printf 'Error: the local transcription environment is missing: %s\n' "$python_path" >&2
    exit 1
fi

exec "$python_path" "$pipeline_path" --all "$@"
