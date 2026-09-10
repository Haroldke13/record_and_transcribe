#!/usr/bin/env bash

# Record the connected external USB microphone to this script's folder.
# Every raw chunk is retained, speech-cleaned, and transcribed automatically.
# Run without an argument to record until Ctrl+C, or supply seconds, for example:
#   ./record_external_microphone.sh 30

set -u
set -o pipefail

recordings_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null && pwd -P)"
requested_duration="${1:-}"
chunk_seconds="${RECORDING_CHUNK_SECONDS:-600}"
pipeline_script="$recordings_dir/audio_pipeline.py"
pipeline_python="$recordings_dir/.transcription_venv/bin/python"

show_help() {
    printf '%s\n' \
        "Usage: $(basename -- "$0") [seconds]" \
        "" \
        "Records from the external USB microphone into:" \
        "  $recordings_dir" \
        "" \
        "Recordings are automatically saved in consecutive 10-minute chunks." \
        "Each filename begins with the date and includes its start and end times." \
        "" \
        "Every raw chunk is retained. A speech-cleaned copy is saved under:" \
        "  $recordings_dir/CLEANED_AUDIO_RECORDINGS" \
        "" \
        "Raw and cleaned transcripts are saved under:" \
        "  $recordings_dir/TRANSCRIPTS" \
        "" \
        "Long silent sections are shortened and voice volume is enhanced." \
        "English, Swahili, and other supported languages are auto-detected." \
        "" \
        "Without a duration, press Ctrl+C to stop and finalize the WAV file."
}

if [[ "$requested_duration" == "-h" || "$requested_duration" == "--help" ]]; then
    show_help
    exit 0
fi

if [[ -n "$requested_duration" && ! "$requested_duration" =~ ^([0-9]+([.][0-9]+)?|[.][0-9]+)$ ]]; then
    printf 'Error: duration must be a positive number of seconds.\n' >&2
    show_help >&2
    exit 2
fi

if [[ -n "$requested_duration" ]] && ! awk -v seconds="$requested_duration" 'BEGIN { exit !(seconds > 0) }'; then
    printf 'Error: duration must be greater than zero.\n' >&2
    exit 2
fi

if [[ ! "$chunk_seconds" =~ ^([0-9]+([.][0-9]+)?|[.][0-9]+)$ ]] ||
   ! awk -v seconds="$chunk_seconds" 'BEGIN { exit !(seconds > 0) }'; then
    printf 'Error: RECORDING_CHUNK_SECONDS must be greater than zero.\n' >&2
    exit 2
fi

for required_command in pactl ffmpeg ffprobe awk date find sort ps; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        printf 'Error: required command is missing: %s\n' "$required_command" >&2
        exit 1
    fi
done

if [[ ! -f "$pipeline_script" ]]; then
    printf 'Error: the audio cleanup/transcription pipeline is missing: %s\n' \
        "$pipeline_script" >&2
    exit 1
fi

if [[ ! -x "$pipeline_python" ]] ||
   ! "$pipeline_python" -c 'import faster_whisper' >/dev/null 2>&1; then
    printf 'Error: the local speech transcription dependencies are unavailable.\n' >&2
    printf 'Expected Python environment: %s\n' "$pipeline_python" >&2
    exit 1
fi

external_source="${EXTERNAL_MIC_SOURCE:-}"

if [[ -z "$external_source" ]]; then
    configured_source="$(pactl get-default-source 2>/dev/null || true)"
    if [[ "$configured_source" == alsa_input.usb-* ]]; then
        external_source="$configured_source"
    fi
fi

if [[ -z "$external_source" ]]; then
    external_source="$(pactl list sources short | awk '$2 ~ /^alsa_input[.]usb-/ { print $2; exit }')"
fi

if [[ -z "$external_source" ]]; then
    printf 'Error: no external USB microphone is connected.\n' >&2
    printf 'Connect the microphone, wait a few seconds, and run this script again.\n' >&2
    exit 1
fi

if ! pactl set-source-mute "$external_source" 0; then
    printf 'Error: the external microphone could not be unmuted.\n' >&2
    exit 1
fi

if ! pactl set-source-volume "$external_source" 100%; then
    printf 'Error: the external microphone volume could not be set.\n' >&2
    exit 1
fi

if ! pactl set-default-source "$external_source"; then
    printf 'Error: the external microphone could not be selected.\n' >&2
    exit 1
fi

session_tag="$(date '+%s')_${BASHPID}"
raw_prefix=".raw_${session_tag}_"
raw_pattern="$recordings_dir/${raw_prefix}%Y-%m-%d__start-%H-%M-%S.wav"
capture_pid=''
stop_requested=0
stop_wait_cycles=0
stop_signal_sent=0
processing_failed=0
available_path=''
parsed_start_date=''
parsed_start_time=''

request_stop() {
    if (( stop_requested == 0 )); then
        printf '\nStop requested. Finalizing the open chunk...\n'
    fi
    stop_requested=1
}

make_available_path() {
    local requested_path="$1"
    local path_without_extension
    local counter=2

    if [[ ! -e "$requested_path" ]]; then
        available_path="$requested_path"
        return
    fi

    path_without_extension="${requested_path%.wav}"
    while [[ -e "${path_without_extension}__${counter}.wav" ]]; do
        ((counter += 1))
    done
    available_path="${path_without_extension}__${counter}.wav"
}

parse_chunk_start() {
    local raw_file_name="${1##*/}"
    local timestamp_part="${raw_file_name#"$raw_prefix"}"

    parsed_start_date="${timestamp_part%%__start-*}"
    parsed_start_time="${timestamp_part#*__start-}"
    parsed_start_time="${parsed_start_time%.wav}"
}

process_raw_chunk() {
    local raw_file="$1"
    local end_date="$2"
    local end_time="$3"
    local final_name
    local final_path

    parse_chunk_start "$raw_file"

    if [[ "$parsed_start_date" == "$end_date" ]]; then
        final_name="${parsed_start_date}__start-${parsed_start_time}__end-${end_time}__external_microphone.wav"
    else
        final_name="${parsed_start_date}__start-${parsed_start_time}__end-${end_date}_${end_time}__external_microphone.wav"
    fi

    final_path="$recordings_dir/$final_name"
    make_available_path "$final_path"
    final_path="$available_path"
    mv -- "$raw_file" "$final_path"
    printf 'Saved raw chunk: %s\n' "$final_path"

    if (
        # Ctrl+C stops capture. A closed raw chunk remains safe while cleanup
        # and transcription finish, even if the same keypress reaches Python.
        trap '' INT
        exec "$pipeline_python" "$pipeline_script" --audio "$final_path"
    ); then
        return 0
    fi

    printf 'Warning: cleanup/transcription failed; retry with: %s\n' \
        "$recordings_dir/process_all_recordings.sh" >&2
    printf 'The untouched raw chunk remains at: %s\n' "$final_path" >&2
    return 1
}

process_chunks() {
    local include_open_chunk="$1"
    local -a raw_chunks=()
    local chunk_count
    local process_count
    local index
    local end_date
    local end_time

    mapfile -d '' -t raw_chunks < <(
        find "$recordings_dir" \
            -maxdepth 1 \
            -type f \
            -name "${raw_prefix}*.wav" \
            -print0 | sort -z
    )

    chunk_count="${#raw_chunks[@]}"
    process_count="$chunk_count"
    if (( include_open_chunk == 0 && process_count > 0 )); then
        ((process_count -= 1))
    fi

    for ((index = 0; index < process_count; index += 1)); do
        if (( index + 1 < chunk_count )); then
            parse_chunk_start "${raw_chunks[index + 1]}"
            end_date="$parsed_start_date"
            end_time="$parsed_start_time"
        else
            # Cleanup/transcription of earlier chunks may outlast capture. Use
            # the final WAV's real close time, not the later wall-clock time at
            # which this processing loop eventually reaches it.
            end_date="$(date -r "${raw_chunks[index]}" '+%Y-%m-%d')"
            end_time="$(date -r "${raw_chunks[index]}" '+%H-%M-%S')"
        fi

        if ! process_raw_chunk "${raw_chunks[index]}" "$end_date" "$end_time"; then
            processing_failed=1
        fi
    done
}

printf 'External microphone: %s\n' "$external_source"
printf 'Saving recordings in: %s\n' "$recordings_dir"
printf 'Autosave chunk length: %s seconds.\n' "$chunk_seconds"
printf 'Filename format: DATE__start-TIME__end-TIME__external_microphone.wav\n'
printf 'Raw retention: enabled.\n'
printf 'Speech cleanup: denoise, background attenuation, silence shortening, and leveling enabled.\n'
printf 'Transcription: raw and cleaned multilingual transcripts enabled.\n'

capture_arguments=(
    -hide_banner
    -nostdin
    -f pulse
)

if [[ -n "$requested_duration" ]]; then
    capture_arguments+=( -t "$requested_duration" )
else
    printf 'Recording now. Press Ctrl+C once to stop and save the file.\n'
fi

capture_arguments+=(
    -i "$external_source"
    -map 0:a:0
    -ac 1
    -ar 48000
    -c:a pcm_s16le
    -f segment
    -segment_time "$chunk_seconds"
    -segment_format wav
    -reset_timestamps 1
    -strftime 1
    -n
    "$raw_pattern"
)

trap request_stop INT TERM

(
    trap - INT TERM
    exec ffmpeg "${capture_arguments[@]}"
) &
capture_pid="$!"

while true; do
    process_chunks 0

    capture_state="$(ps -o stat= -p "$capture_pid" 2>/dev/null || true)"
    if [[ -z "$capture_state" || "$capture_state" == Z* ]]; then
        break
    fi

    if (( stop_requested )); then
        ((stop_wait_cycles += 1))
        if (( stop_wait_cycles >= 2 && stop_signal_sent == 0 )); then
            # A terminal Ctrl+C normally reaches FFmpeg directly. This delayed
            # fallback handles a signal sent only to the wrapper process while
            # avoiding a destructive second interrupt during WAV finalization.
            kill -INT "$capture_pid" 2>/dev/null || true
            stop_signal_sent=1
        fi
    fi

    sleep 1
done

wait "$capture_pid"
capture_status="$?"
capture_pid=''

process_chunks 1

trap - INT TERM

if (( capture_status != 0 && stop_requested == 0 )); then
    printf 'Error: microphone capture stopped unexpectedly with status %s.\n' \
        "$capture_status" >&2
    exit "$capture_status"
fi

if (( processing_failed )); then
    printf 'Recording stopped, but at least one chunk needs manual recovery.\n' >&2
    exit 1
fi

printf 'Recording finished. Raw, cleaned, and transcript outputs were saved.\n'
