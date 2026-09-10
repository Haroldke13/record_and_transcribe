#!/usr/bin/env python3
"""Create speech-focused audio and multilingual transcripts from raw recordings."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parent
CLEANED_DIR = ROOT / "CLEANED_AUDIO_RECORDINGS"
TRANSCRIPTS_DIR = ROOT / "TRANSCRIPTS"
RAW_TRANSCRIPTS_DIR = TRANSCRIPTS_DIR / "RAW_AUDIO_RECORDINGS"
CLEANED_TRANSCRIPTS_DIR = TRANSCRIPTS_DIR / "CLEANED_AUDIO_RECORDINGS"
MODEL_DIR = ROOT / ".speech_models"
DEFAULT_MODEL = "small"
SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus"}
MIN_LANGUAGE_CONFIDENCE = 0.50
MIN_SEGMENT_LOG_PROBABILITY = -0.80

# This chain is deliberately speech-focused. It removes rumble and frequencies
# above the useful speech band, adaptively reduces steady noise, turns down
# low-level background sound, adds vocal presence, shortens long silent gaps,
# and produces even, non-clipping volume. Loud music overlapping speech cannot
# be perfectly separated by a general-purpose filter, but it is attenuated.
CLEANING_FILTER = ",".join(
    (
        "highpass=f=80",
        "lowpass=f=7600",
        "afftdn=nr=18:nf=-38:tn=1:gs=8",
        "agate=threshold=0.012:ratio=3:range=0.12:attack=15:release=250:detection=rms",
        "equalizer=f=220:t=q:w=1:g=-2",
        "equalizer=f=3000:t=q:w=1.2:g=3",
        "acompressor=threshold=0.08:ratio=3:attack=8:release=180:makeup=2:detection=rms",
        (
            "silenceremove="
            "start_periods=1:start_duration=0.08:start_threshold=-40dB:start_silence=0.10:"
            "stop_periods=-1:stop_duration=1.00:stop_threshold=-40dB:stop_silence=0.30:"
            "detection=rms:window=0.02"
        ),
        "loudnorm=I=-18:LRA=7:TP=-1.5",
        "alimiter=limit=0.891:attack=5:release=50:level=0",
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Keep raw recordings, make speech-cleaned copies, and transcribe both "
            "with automatic multilingual language detection."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--audio",
        action="append",
        type=Path,
        help="finalized raw audio file; may be supplied more than once",
    )
    source.add_argument(
        "--all",
        action="store_true",
        help="process every finalized audio file in the recordings folder",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild cleaned audio and transcripts even when current outputs exist",
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="create cleaned audio without loading the transcription model",
    )
    parser.add_argument(
        "--transcribe-only",
        action="store_true",
        help="do not rebuild cleaned audio; transcribe existing raw and cleaned files",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AUDIO_TRANSCRIPTION_MODEL", DEFAULT_MODEL),
        help=f"multilingual faster-whisper model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="optional ISO language code; omitted means automatic detection",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=min(4, max(1, os.cpu_count() or 1)),
        help="CPU threads used for transcription (default: up to 4)",
    )
    args = parser.parse_args()
    if args.clean_only and args.transcribe_only:
        parser.error("--clean-only and --transcribe-only cannot be used together")
    if args.cpu_threads < 1:
        parser.error("--cpu-threads must be at least 1")
    return args


def ensure_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError("missing required command(s): " + ", ".join(missing))


def ensure_output_directories() -> None:
    for directory in (
        CLEANED_DIR,
        RAW_TRANSCRIPTS_DIR,
        CLEANED_TRANSCRIPTS_DIR,
        MODEL_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def collect_audio(args: argparse.Namespace) -> list[Path]:
    if args.all:
        candidates = sorted(
            path
            for path in ROOT.iterdir()
            if path.is_file()
            and not path.name.startswith(".")
            and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        )
    else:
        candidates = [path.expanduser().resolve() for path in args.audio]

    result: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            print(f"ERROR: audio file does not exist: {resolved}", file=sys.stderr)
            continue
        if resolved.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            print(f"ERROR: unsupported audio type: {resolved}", file=sys.stderr)
            continue
        result.append(resolved)
    return result


def is_open_by_another_process(path: Path) -> bool:
    lsof = shutil.which("lsof")
    if lsof is None:
        return False
    result = subprocess.run(
        [lsof, "-t", "--", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def output_is_current(output: Path, source: Path) -> bool:
    return output.is_file() and output.stat().st_mtime_ns >= source.stat().st_mtime_ns


def make_short_silence(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "0.10",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(path),
        ],
        check=True,
    )


def clean_audio(raw_path: Path, cleaned_path: Path, force: bool) -> bool:
    if not force and output_is_current(cleaned_path, raw_path):
        print(f"Cleaned audio is current: {cleaned_path.name}", flush=True)
        return False

    temporary = cleaned_path.with_name(
        f".{cleaned_path.stem}.cleaning-{os.getpid()}{cleaned_path.suffix}"
    )
    temporary.unlink(missing_ok=True)
    print(f"Cleaning for speech: {raw_path.name}", flush=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "warning",
                "-i",
                str(raw_path),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-af",
                CLEANING_FILTER,
                "-c:a",
                "pcm_s16le",
                "-y",
                str(temporary),
            ],
            check=True,
        )
        if probe_duration(temporary) <= 0.02:
            temporary.unlink(missing_ok=True)
            make_short_silence(temporary)
        os.replace(temporary, cleaned_path)
    finally:
        temporary.unlink(missing_ok=True)

    print(f"Saved cleaned audio: {cleaned_path}", flush=True)
    return True


def transcript_paths(audio_path: Path, kind: str) -> tuple[Path, Path, Path]:
    directory = RAW_TRANSCRIPTS_DIR if kind == "raw" else CLEANED_TRANSCRIPTS_DIR
    stem = audio_path.stem
    return directory / f"{stem}.txt", directory / f"{stem}.srt", directory / f"{stem}.json"


def transcripts_are_current(audio_path: Path, kind: str) -> bool:
    return all(output_is_current(path, audio_path) for path in transcript_paths(audio_path, kind))


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.writing-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def transcribe_audio(
    model: WhisperModel,
    audio_path: Path,
    kind: str,
    model_name: str,
    language: str | None,
    force: bool,
) -> bool:
    if not force and transcripts_are_current(audio_path, kind):
        print(f"{kind.title()} transcripts are current: {audio_path.stem}", flush=True)
        return False

    print(f"Transcribing {kind} audio: {audio_path.name}", flush=True)
    segment_stream, info = model.transcribe(
        str(audio_path),
        language=language,
        task="transcribe",
        beam_size=5,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={
            "threshold": 0.5,
            "min_speech_duration_ms": 250,
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 250,
        },
        word_timestamps=False,
        language_detection_segments=3,
    )
    detected_language = getattr(info, "language", None)
    language_probability = float(getattr(info, "language_probability", 0.0))
    language_is_reliable = language is not None or language_probability >= MIN_LANGUAGE_CONFIDENCE
    segments = []
    rejected_segments = []
    for segment in segment_stream:
        text = segment.text.strip()
        if not text:
            continue
        segment_data = {
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": text,
            "average_log_probability": round(float(segment.avg_logprob), 6),
            "no_speech_probability": round(float(segment.no_speech_prob), 6),
        }
        rejection_reasons = []
        if not language_is_reliable:
            rejection_reasons.append("low_language_confidence")
        if float(segment.avg_logprob) < MIN_SEGMENT_LOG_PROBABILITY:
            rejection_reasons.append("low_transcript_confidence")
        if rejection_reasons:
            segment_data["rejection_reasons"] = rejection_reasons
            rejected_segments.append(segment_data)
        else:
            segments.append(segment_data)

    text_path, srt_path, json_path = transcript_paths(audio_path, kind)
    if segments:
        plain_text = "\n".join(segment["text"] for segment in segments) + "\n"
        srt_blocks = [
            (
                f"{index}\n"
                f"{srt_timestamp(segment['start'])} --> {srt_timestamp(segment['end'])}\n"
                f"{segment['text']}"
            )
            for index, segment in enumerate(segments, start=1)
        ]
        srt_text = "\n\n".join(srt_blocks) + "\n"
    else:
        no_speech_message = (
            "[No reliable speech detected]" if rejected_segments else "[No speech detected]"
        )
        plain_text = no_speech_message + "\n"
        srt_text = f"1\n00:00:00,000 --> 00:00:00,100\n{no_speech_message}\n"
        detected_language = None
        language_probability = 0.0

    metadata = {
        "schema_version": 1,
        "audio_kind": kind,
        "source_audio": audio_path.name,
        "source_modified_at": datetime.fromtimestamp(
            audio_path.stat().st_mtime
        ).astimezone().isoformat(timespec="seconds"),
        "transcribed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": model_name,
        "task": "transcribe",
        "language_requested": language,
        "detected_language": detected_language,
        "language_probability": round(language_probability, 6),
        "duration_seconds": round(float(getattr(info, "duration", probe_duration(audio_path))), 3),
        "confidence_policy": {
            "minimum_language_probability": MIN_LANGUAGE_CONFIDENCE,
            "minimum_segment_average_log_probability": MIN_SEGMENT_LOG_PROBABILITY,
        },
        "segments": segments,
        "rejected_segments": rejected_segments,
    }
    atomic_write_text(text_path, plain_text)
    atomic_write_text(srt_path, srt_text)
    atomic_write_text(json_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    language_message = detected_language or "no speech"
    print(
        f"Saved {kind} transcripts ({language_message}): {text_path.name}",
        flush=True,
    )
    return True


def load_model(model_name: str, cpu_threads: int) -> WhisperModel:
    print(
        f"Loading multilingual speech model '{model_name}' on CPU (int8, {cpu_threads} threads)...",
        flush=True,
    )
    return WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        num_workers=1,
        download_root=str(MODEL_DIR),
    )


def process_files(args: argparse.Namespace, audio_files: Iterable[Path]) -> int:
    failures = 0
    model: WhisperModel | None = None

    for raw_path in audio_files:
        if is_open_by_another_process(raw_path):
            print(f"SKIPPED open recording (still being written): {raw_path}", flush=True)
            continue

        if probe_duration(raw_path) <= 0:
            print(f"ERROR: invalid or empty audio file: {raw_path}", file=sys.stderr)
            failures += 1
            continue

        cleaned_path = CLEANED_DIR / raw_path.name
        clean_available = cleaned_path.is_file()
        if not args.transcribe_only:
            try:
                clean_audio(raw_path, cleaned_path, args.force)
                clean_available = cleaned_path.is_file()
            except (OSError, subprocess.SubprocessError) as error:
                print(f"ERROR cleaning {raw_path.name}: {error}", file=sys.stderr)
                failures += 1

        if args.clean_only:
            continue

        try:
            if model is None:
                model = load_model(args.model, args.cpu_threads)
            transcribe_audio(
                model,
                raw_path,
                "raw",
                args.model,
                args.language,
                args.force,
            )
            if clean_available:
                transcribe_audio(
                    model,
                    cleaned_path,
                    "cleaned",
                    args.model,
                    args.language,
                    args.force,
                )
            else:
                print(
                    f"ERROR: cleaned audio is unavailable for transcription: {cleaned_path}",
                    file=sys.stderr,
                )
                failures += 1
        except Exception as error:  # keep processing other finalized recordings
            print(f"ERROR transcribing {raw_path.name}: {error}", file=sys.stderr)
            failures += 1

    return failures


def main() -> int:
    args = parse_args()
    try:
        ensure_tools()
        ensure_output_directories()
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    audio_files = collect_audio(args)
    if not audio_files:
        print("No finalized audio files were found.", file=sys.stderr)
        return 1

    failures = process_files(args, audio_files)
    if failures:
        print(f"Finished with {failures} failed operation(s).", file=sys.stderr)
        return 1
    print("Audio cleanup and transcription finished successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
