"""Real video clip music-video maker.
Uploads real video clips, joins them in upload order, and adds a soundtrack.
No captions, lyric overlays, or generated illustrations are added.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Real Clip Music Video Studio", page_icon="🎬", layout="centered")

FORMATS = {
    "YouTube widescreen (16:9)": (1280, 720),
    "Vertical video (9:16)": (720, 1280),
    "Square video (1:1)": (900, 900),
}
VIDEO_TYPES = ["mp4", "mov", "m4v", "webm", "mkv", "avi"]
AUDIO_TYPES = ["mp3", "wav", "m4a", "aac", "ogg", "flac"]


def run_command(command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        detail = result.stderr[-5000:] if result.stderr else "Unknown FFmpeg error."
        raise RuntimeError(detail)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if result.returncode:
        raise RuntimeError("Could not read the duration of one of the uploaded media files.")
    try:
        return max(0.1, float(result.stdout.strip()))
    except ValueError as exc:
        raise RuntimeError("Could not determine the media duration.") from exc


def escape_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def build_video(clips, soundtrack, size, output: Path, work: Path, progress) -> None:
    width, height = size
    normalized: list[Path] = []
    for index, clip in enumerate(clips):
        source = work / f"source_{index:03d}{Path(clip.name).suffix.lower()}"
        source.write_bytes(clip.getvalue())
        target = work / f"normalized_{index:03d}.mp4"
        # Fit real footage to the chosen frame by center-cropping; do not add text.
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps=30,format=yuv420p"
        )
        run_command([
            "ffmpeg", "-y", "-i", str(source), "-an", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-movflags", "+faststart", str(target),
        ])
        normalized.append(target)
        progress.progress(0.1 + 0.55 * (index + 1) / len(clips), text=f"Preparing clip {index + 1} of {len(clips)}…")

    audio_path = work / f"soundtrack{Path(soundtrack.name).suffix.lower()}"
    audio_path.write_bytes(soundtrack.getvalue())
    audio_duration = probe_duration(audio_path)
    clip_durations = [probe_duration(p) for p in normalized]
    total_sequence = sum(clip_durations)
    repeats = max(1, math.ceil(audio_duration / total_sequence))
    playlist = work / "clips.txt"
    lines = [f"file '{escape_concat_path(p)}'" for _ in range(repeats) for p in normalized]
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    progress.progress(0.72, text="Joining clips and matching the soundtrack…")
    run_command([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(playlist),
        "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
        "-t", f"{audio_duration:.3f}", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-c:a", "aac", "-b:a", "192k", "-af", "aresample=async=1",
        "-shortest", "-movflags", "+faststart", str(output),
    ])
    progress.progress(1.0, text="Video ready.")


st.title("🎬 Real Clip Music Video Studio")
st.write("Upload real video clips and a soundtrack. Clips are joined in the order you select them.")
st.info("This version uses your actual video footage. It does not generate illustrations or add captions, lyrics, or text overlays.")

format_name = st.selectbox("Video format", list(FORMATS.keys()))
clips = st.file_uploader(
    "1. Upload video clips (select them in the sequence you want)",
    type=VIDEO_TYPES, accept_multiple_files=True,
    help="The uploaded clips are joined in their listed order. Use footage you own or have permission to use.",
)
soundtrack = st.file_uploader("2. Upload soundtrack", type=AUDIO_TYPES)

if clips:
    st.caption(f"{len(clips)} video clip(s) selected.")
    for i, clip in enumerate(clips, start=1):
        st.write(f"{i}. {clip.name}")
if soundtrack:
    st.caption(f"Soundtrack: {soundtrack.name}")

if st.button("Create video", type="primary", disabled=not (clips and soundtrack)):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        st.error("FFmpeg and FFprobe are required. Ensure ffmpeg is listed in packages.txt and the app has redeployed.")
    else:
        progress = st.progress(0, text="Starting…")
        try:
            with tempfile.TemporaryDirectory(prefix="real_clip_video_") as temp_dir:
                work = Path(temp_dir)
                output = work / "music_video.mp4"
                build_video(clips, soundtrack, FORMATS[format_name], output, work, progress)
                video_bytes = output.read_bytes()
            st.success("Your real-clip video is ready. No captions or text overlays were added.")
            st.video(video_bytes)
            st.download_button(
                "Download MP4", data=video_bytes, file_name="real_clip_music_video.mp4",
                mime="video/mp4", type="primary",
            )
        except Exception as exc:
            st.error("The video could not be created.")
            st.code(str(exc))

st.caption("Tip: Download only video clips you own or are licensed to use. This app does not download videos from YouTube.")
