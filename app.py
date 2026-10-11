"""
AI Music Video Studio — Streamlit
A standalone frontend/integration for an optional ComfyUI video-generation server.

Inspired by workflow concepts in:
https://github.com/GeekatplayStudio/ComfyUI-Music-to-Video

This application does not bundle the original custom nodes or AI model weights.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
import streamlit as st

try:
    import numpy as np
    import librosa
except Exception:
    np = None
    librosa = None

try:
    import whisper
except Exception:
    whisper = None

APP_DIR = Path("music_video_output")
APP_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_MB = 500

st.set_page_config(
    page_title="AI Music Video Studio",
    page_icon="🎬",
    layout="wide",
)

st.title("🎬 AI Music Video Studio")
st.write(
    "Upload a song, analyze or enter its lyrics, build a timed storyboard, "
    "prepare cinematic prompts, and optionally send a compatible API workflow "
    "to ComfyUI. You can also make a simple MP4 from still images."
)
st.warning(
    "AI video generation needs a separately running ComfyUI server, compatible "
    "custom nodes, models, and an API-format workflow. Streamlit Cloud by itself "
    "does not provide the GPU or video models."
)


def run_cmd(command: list[str], timeout: int = 300) -> tuple[bool, str]:
    """Run an installed system command safely without using a shell."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        if result.returncode:
            return False, (result.stderr or result.stdout or "Command failed")
        return True, result.stdout
    except FileNotFoundError:
        return False, f"Executable not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return False, "Command timed out."


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def audio_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    ok, output = run_cmd([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], timeout=60)
    if ok:
        try:
            return float(output.strip())
        except ValueError:
            pass
    return None


def analyze_song(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"duration_seconds": audio_duration(path)}
    if librosa is None or np is None:
        result["analysis_note"] = (
            "Install librosa and numpy to estimate tempo and audio energy."
        )
        return result
    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True)
        result["duration_seconds"] = float(librosa.get_duration(y=y, sr=sr))
        tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
        result["estimated_bpm"] = round(float(np.asarray(tempo).reshape(-1)[0]), 2)
        result["beat_times_seconds"] = [
            round(float(x), 3)
            for x in librosa.frames_to_time(frames, sr=sr).tolist()
        ]
        result["mean_energy"] = round(
            float(np.mean(librosa.feature.rms(y=y))), 5
        )
        result["analysis_note"] = "Audio analysis completed."
    except Exception as exc:
        result["analysis_note"] = f"Audio analysis unavailable: {exc}"
    return result


def transcribe(path: Path, model_name: str) -> tuple[str, list[dict[str, Any]], str | None]:
    if whisper is None:
        return "", [], (
            "Whisper is not installed. Enter lyrics manually, or install "
            "openai-whisper and FFmpeg for local transcription."
        )
    try:
        model = whisper.load_model(model_name)
        data = model.transcribe(str(path), fp16=False, verbose=False)
        segments = [
            {
                "start": float(s.get("start", 0)),
                "end": float(s.get("end", 0)),
                "text": str(s.get("text", "")).strip(),
            }
            for s in data.get("segments", [])
        ]
        return str(data.get("text", "")).strip(), segments, None
    except Exception as exc:
        return "", [], f"Transcription failed: {exc}"


def split_lyrics(lyrics: str, count: int) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n+|(?<=[.!?।])\s+", lyrics) if p.strip()]
    if not parts:
        parts = ["Instrumental music — tell the story visually."]
    return [
        parts[min(int(i * len(parts) / count), len(parts) - 1)]
        for i in range(count)
    ]


def make_prompts(
    lines: list[str], style: str, setting: str, character: str,
    ratio: str, mood: str, language: str
) -> list[str]:
    prompts = []
    for index, lyric in enumerate(lines, 1):
        prompts.append(
            f"Music video scene {index}. Visualize the meaning and emotion of "
            f"this lyric, without printing the lyric as text: {lyric}. "
            f"Art direction: {style}. Mood: {mood}. Setting: {setting}. "
            f"Character continuity: {character}. Language/context: {language}. "
            f"Composition for {ratio}. Cinematic lighting, clear storytelling, "
            "coherent subject, detailed environment, polished professional frame. "
            "Avoid logos, watermarks, garbled lettering, and inconsistent character design."
        )
    return prompts


def timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def build_timeline(lines: list[str], duration: float) -> list[dict[str, Any]]:
    interval = duration / max(len(lines), 1)
    rows = []
    for i, line in enumerate(lines):
        start = i * interval
        end = min((i + 1) * interval, duration)
        rows.append({
            "scene": i + 1,
            "start_seconds": round(start, 2),
            "end_seconds": round(end, 2),
            "start": timestamp(start),
            "end": timestamp(end),
            "lyrics_or_narrative": line,
        })
    return rows


def save_upload(uploaded_file, destination: Path) -> Path:
    size_mb = len(uploaded_file.getvalue()) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise ValueError(f"File exceeds the {MAX_UPLOAD_MB} MB app limit.")
    destination.write_bytes(uploaded_file.getvalue())
    return destination


def create_still_image_video(
    song_path: Path, image_paths: list[Path], output_path: Path,
    aspect: str, seconds_per_image: float
) -> Path:
    if not ffmpeg_available():
        raise RuntimeError("FFmpeg and FFprobe are required. See README.md.")
    if not image_paths:
        raise ValueError("Upload at least one image.")
    duration = audio_duration(song_path)
    if not duration or duration <= 0:
        raise RuntimeError("Could not read song duration using FFprobe.")

    concat_path = APP_DIR / "image_sequence.txt"
    with concat_path.open("w", encoding="utf-8") as file:
        for image_path in image_paths:
            safe = str(image_path.resolve()).replace("'", "'\\''")
            file.write(f"file '{safe}'\n")
            file.write(f"duration {float(seconds_per_image)}\n")
        safe = str(image_paths[-1].resolve()).replace("'", "'\\''")
        file.write(f"file '{safe}'\n")

    dimensions = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1080, 1080)}
    width, height = dimensions[aspect]
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24"
    )
    ok, error = run_cmd([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-i", str(song_path), "-vf", vf, "-t", str(duration),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(output_path)
    ], timeout=1800)
    if not ok:
        raise RuntimeError(error[-4000:])
    return output_path



# Platform export presets. These are practical encoding targets, not a guarantee
# that a platform will accept every file; platform limits can change.
PLATFORM_PRESETS: dict[str, dict[str, Any]] = {
    "Instagram Reels": {"size": (1080, 1920), "ratio": "9:16", "fps": 30},
    "Instagram Stories": {"size": (1080, 1920), "ratio": "9:16", "fps": 30},
    "Instagram Feed — Square": {"size": (1080, 1080), "ratio": "1:1", "fps": 30},
    "Instagram Feed — Portrait": {"size": (1080, 1350), "ratio": "4:5", "fps": 30},
    "Facebook Reels": {"size": (1080, 1920), "ratio": "9:16", "fps": 30},
    "Facebook Feed — Landscape": {"size": (1920, 1080), "ratio": "16:9", "fps": 30},
    "WhatsApp Status": {"size": (1080, 1920), "ratio": "9:16", "fps": 30},
    "YouTube — Standard": {"size": (1920, 1080), "ratio": "16:9", "fps": 30},
    "YouTube Shorts": {"size": (1080, 1920), "ratio": "9:16", "fps": 30},
    "TikTok": {"size": (1080, 1920), "ratio": "9:16", "fps": 30},
    "X / Twitter": {"size": (1920, 1080), "ratio": "16:9", "fps": 30},
    "LinkedIn": {"size": (1920, 1080), "ratio": "16:9", "fps": 30},
    "Pinterest Idea Pin": {"size": (1080, 1920), "ratio": "9:16", "fps": 30},
    "Universal — 720p Landscape": {"size": (1280, 720), "ratio": "16:9", "fps": 30},
}


def export_platform_video(
    input_path: Path,
    song_path: Path,
    output_path: Path,
    preset: dict[str, Any],
    fit_mode: str = "Crop to fill",
    replace_audio: bool = True,
) -> Path:
    """Transcode a rendered video into an upload-friendly MP4 profile."""
    if not ffmpeg_available():
        raise RuntimeError("FFmpeg and FFprobe are required for platform exports.")
    if not input_path.exists():
        raise FileNotFoundError(f"Rendered video not found: {input_path}")
    width, height = preset["size"]
    fps = int(preset["fps"])
    if fit_mode == "Fit with blurred background":
        vf = (
            f"split[fg][bg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:10[background];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[foreground];"
            f"[background][foreground]overlay=(W-w)/2:(H-h)/2,"
            f"fps={fps},setsar=1,format=yuv420p"
        )
    elif fit_mode == "Fit with black bars":
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fps},setsar=1,format=yuv420p"
        )
    else:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1,format=yuv420p"
        )

    command = ["ffmpeg", "-y", "-i", str(input_path)]
    if replace_audio:
        command += ["-i", str(song_path)]
        command += [
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
        ]
    else:
        command += [
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:a", "aac", "-b:a", "192k",
        ]
    command += [
        "-vf", vf, "-r", str(fps), "-c:v", "libx264",
        "-preset", "medium", "-crf", "20", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-max_muxing_queue_size", "2048", str(output_path),
    ]
    ok, error = run_cmd(command, timeout=7200)
    if not ok:
        raise RuntimeError(error[-4000:])
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create a valid output file.")
    return output_path

def queue_comfy_workflow(base_url: str, workflow: dict[str, Any]) -> str:
    response = requests.post(
        f"{base_url.rstrip('/')}/prompt",
        json={"prompt": workflow},
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("node_errors"):
        raise RuntimeError(f"ComfyUI node errors: {data['node_errors']}")
    if not data.get("prompt_id"):
        raise RuntimeError(f"ComfyUI returned no prompt_id: {data}")
    return str(data["prompt_id"])


def wait_for_comfy(base_url: str, prompt_id: str, timeout_seconds: int = 7200):
    start = time.time()
    progress = st.progress(0)
    status = st.empty()
    while time.time() - start < timeout_seconds:
        response = requests.get(
            f"{base_url.rstrip('/')}/history/{prompt_id}", timeout=30
        )
        response.raise_for_status()
        history = response.json()
        if prompt_id in history:
            progress.progress(100)
            status.success("ComfyUI workflow finished.")
            return history[prompt_id]
        elapsed = int(time.time() - start)
        progress.progress(min(95, int(elapsed / timeout_seconds * 100)))
        status.info(f"Waiting for ComfyUI render… {elapsed}s elapsed.")
        time.sleep(3)
    raise TimeoutError(
        "Timed out waiting for ComfyUI. The queued job may still be running."
    )


def collect_media_outputs(history_item: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for node_id, node_output in history_item.get("outputs", {}).items():
        for key in ("videos", "gifs", "images"):
            for item in node_output.get(key, []) or []:
                if item.get("filename"):
                    results.append({
                        "node_id": node_id,
                        "kind": key,
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    })
    return results


def download_comfy_output(base_url: str, item: dict[str, Any], destination: Path):
    response = requests.get(
        f"{base_url.rstrip('/')}/view",
        params={
            "filename": item["filename"],
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        },
        timeout=600,
    )
    response.raise_for_status()
    destination.write_bytes(response.content)
    return destination


with st.sidebar:
    st.header("Creative direction")
    style = st.selectbox("Visual style", [
        "Cinematic realistic", "Motivational film", "Anime",
        "3D animation", "Watercolor illustration", "Documentary",
        "Dreamlike fantasy", "Hand-drawn illustration",
    ])
    mood = st.selectbox("Mood", [
        "Hopeful and inspiring", "Emotional", "Energetic",
        "Peaceful", "Dramatic", "Triumphant", "Nostalgic",
    ])
    setting = st.text_input(
        "Setting / era", "Choose settings that fit the lyrics"
    )
    character = st.text_input(
        "Main character / continuity",
        "Keep the same main character, clothing and appearance in every scene",
    )
    language = st.text_input("Song language / cultural context", "Punjabi")
    aspect = st.selectbox("Final video aspect ratio", ["16:9", "9:16", "1:1"])
    scene_count = st.slider("Storyboard scene count", 3, 40, 10)
    whisper_model = st.selectbox("Whisper model (optional)", ["base", "small", "medium"])
    st.caption("Larger Whisper models use more RAM and take longer.")


uploaded_song = st.file_uploader(
    "1. Upload your song", type=["mp3", "wav", "m4a", "flac", "ogg"]
)

if uploaded_song:
    suffix = Path(uploaded_song.name).suffix.lower() or ".mp3"
    song_path = APP_DIR / f"source_song{suffix}"
    save_upload(uploaded_song, song_path)
    st.audio(uploaded_song.getvalue())

    st.subheader("2. Lyrics")
    manual_lyrics = st.text_area(
        "Paste lyrics (recommended for best scene matching)",
        height=160,
        placeholder="Paste lyrics here. If left empty, you can try automatic Whisper transcription.",
        key="manual_lyrics",
    )
    use_transcription = st.checkbox(
        "Transcribe lyrics automatically if the box above is empty", value=True
    )

    if st.button("Analyze song and create storyboard", type="primary"):
        with st.spinner("Analyzing song…"):
            analysis = analyze_song(song_path)
            lyrics = manual_lyrics.strip()
            transcript_segments = []
            transcript_error = None
            if not lyrics and use_transcription:
                lyrics, transcript_segments, transcript_error = transcribe(
                    song_path, whisper_model
                )
            if transcript_error:
                st.warning(transcript_error)
            if not lyrics:
                lyrics = "Instrumental music; create a visual story matching the mood."
            duration = analysis.get("duration_seconds")
            if not duration:
                duration = 180.0
                st.warning(
                    "Song duration could not be detected; using 3 minutes for the "
                    "storyboard only. Install FFmpeg for accurate duration."
                )
            lines = split_lyrics(lyrics, scene_count)
            prompts = make_prompts(
                lines, style, setting, character, aspect, mood, language
            )
            project = {
                "source_filename": uploaded_song.name,
                "duration_seconds": duration,
                "analysis": analysis,
                "lyrics": lyrics,
                "transcript_segments": transcript_segments,
                "timeline": build_timeline(lines, duration),
                "scene_prompts": prompts,
                "settings": {
                    "style": style, "mood": mood, "setting": setting,
                    "character": character, "language": language, "aspect": aspect,
                },
            }
            project_file = APP_DIR / "storyboard_project.json"
            project_file.write_text(
                json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            st.session_state["project"] = project
            st.session_state["song_path"] = str(song_path)
            st.session_state["project_json_path"] = str(project_file)

if "project" in st.session_state:
    project = st.session_state["project"]
    analysis = project.get("analysis", {})
    st.header("3. Analysis")
    c1, c2, c3 = st.columns(3)
    c1.metric("Duration", timestamp(project["duration_seconds"]))
    c2.metric("Estimated BPM", analysis.get("estimated_bpm", "Not available"))
    c3.metric("Mean energy", analysis.get("mean_energy", "Not available"))
    st.caption(analysis.get("analysis_note", ""))

    st.header("4. Storyboard")
    st.dataframe(project["timeline"], use_container_width=True, hide_index=True)
    for i, prompt in enumerate(project["scene_prompts"]):
        with st.expander(
            f"Scene {i + 1} · {project['timeline'][i]['start']}–{project['timeline'][i]['end']}",
            expanded=(i == 0),
        ):
            st.write(project["timeline"][i]["lyrics_or_narrative"])
            st.code(prompt, language="text")
            st.button(
                "Copy prompt",
                key=f"copy_prompt_{i}",
                on_click=lambda p=prompt: st.session_state.update(
                    {"prompt_to_copy": p}
                ),
            )
    if st.session_state.get("prompt_to_copy"):
        st.text_area("Selected prompt (copy it from here)", st.session_state["prompt_to_copy"])

    st.download_button(
        "Download storyboard JSON",
        data=json.dumps(project, indent=2, ensure_ascii=False),
        file_name="storyboard_project.json",
        mime="application/json",
    )
    st.download_button(
        "Download scene prompts TXT",
        data="\n\n".join(
            f"SCENE {i + 1}\n{p}" for i, p in enumerate(project["scene_prompts"])
        ),
        file_name="scene_prompts.txt",
        mime="text/plain",
    )

    st.header("5. Optional ComfyUI AI rendering")
    st.write(
        "Connect a running ComfyUI server and upload an API-format workflow. "
        "ComfyUI does not provide one universal audio-upload API for every custom "
        "workflow, so copy your song into the ComfyUI input folder first and enter "
        "its server-side filename below. Configure the exact node IDs and input keys."
    )
    comfy_url = st.text_input(
        "ComfyUI base URL", placeholder="https://your-secure-comfyui-server"
    )
    workflow_upload = st.file_uploader(
        "ComfyUI API workflow (.json)", type=["json"], key="comfy_workflow"
    )
    remote_audio_filename = st.text_input(
        "Song filename already in ComfyUI input folder (optional)",
        placeholder="source_song.mp3",
    )
    audio_node_id = st.text_input("Audio loader node ID (optional)")
    audio_input_key = st.text_input("Audio filename input field", value="audio")
    prompt_node_id = st.text_input("Text prompt node ID (optional)")
    prompt_input_key = st.text_input("Prompt text input field", value="text")
    run_one_workflow = st.checkbox(
        "Run the workflow once using Scene 1's prompt", value=True
    )
    st.caption(
        "Node IDs and field names vary by workflow. This runs one workflow job; "
        "it does not automatically make one separate clip per scene."
    )

    if st.button("Upload song and queue ComfyUI workflow"):
        if not comfy_url.strip():
            st.error("Enter your ComfyUI server URL.")
        elif not workflow_upload:
            st.error("Upload a ComfyUI API workflow JSON.")
        else:
            try:
                workflow = json.loads(workflow_upload.getvalue().decode("utf-8"))
                if not isinstance(workflow, dict):
                    raise ValueError("Workflow JSON must be an object keyed by node ID.")
                if audio_node_id.strip():
                    if not remote_audio_filename.strip():
                        raise ValueError(
                            "Copy the song into the ComfyUI input folder and enter "
                            "its filename, or leave the audio node ID empty if the "
                            "workflow does not use an audio loader."
                        )
                    if audio_node_id not in workflow:
                        raise ValueError(f"Audio node ID {audio_node_id} was not found.")
                    inputs = workflow[audio_node_id].setdefault("inputs", {})
                    if audio_input_key not in inputs:
                        raise ValueError(
                            f"Input field '{audio_input_key}' not found in audio node. "
                            "Check your workflow JSON and enter the exact input key."
                        )
                    inputs[audio_input_key] = remote_audio_filename.strip()
                if prompt_node_id.strip() and run_one_workflow:
                    if prompt_node_id not in workflow:
                        raise ValueError(f"Prompt node ID {prompt_node_id} was not found.")
                    inputs = workflow[prompt_node_id].setdefault("inputs", {})
                    if prompt_input_key not in inputs:
                        raise ValueError(
                            f"Prompt field '{prompt_input_key}' not found. "
                            "Check the exact input key in your workflow."
                        )
                    inputs[prompt_input_key] = project["scene_prompts"][0]
                with st.spinner("Queueing workflow…"):
                    prompt_id = queue_comfy_workflow(comfy_url, workflow)
                st.success(f"ComfyUI job queued. Prompt ID: {prompt_id}")
                st.session_state["comfy_job"] = {
                    "url": comfy_url, "prompt_id": prompt_id
                }
            except Exception as exc:
                st.error(f"Could not queue ComfyUI workflow: {exc}")

    job = st.session_state.get("comfy_job")
    if job and st.button("Check job and download generated outputs"):
        try:
            with st.spinner("Checking ComfyUI job…"):
                item = wait_for_comfy(job["url"], job["prompt_id"])
            media = collect_media_outputs(item)
            if not media:
                st.warning(
                    "The job finished, but no video/image output was found in its "
                    "history. Inspect the workflow's output nodes and ComfyUI logs."
                )
                st.json(item.get("outputs", {}))
            else:
                for index, entry in enumerate(media, 1):
                    safe_name = Path(entry["filename"]).name
                    target = APP_DIR / f"comfy_output_{index}_{safe_name}"
                    download_comfy_output(job["url"], entry, target)
                    st.write(f"Output: {safe_name} ({entry['kind']})")
                    if target.suffix.lower() in {".mp4", ".webm", ".mov"}:
                        st.session_state["latest_video_path"] = str(target)
                        st.session_state["latest_video_name"] = safe_name
                        st.video(target.read_bytes())
                    st.download_button(
                        f"Download {safe_name}",
                        data=target.read_bytes(),
                        file_name=safe_name,
                        key=f"download_comfy_{index}",
                    )
        except Exception as exc:
            st.error(f"Could not retrieve output: {exc}")

    st.header("6. Simple MP4 fallback using still images")
    st.write(
        "This mode does not generate AI imagery or motion. It sequences your images, "
        "fits them to the selected aspect ratio, and adds your original song."
    )
    images = st.file_uploader(
        "Upload images in the order they should appear",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="still_images",
    )
    seconds_per_image = st.slider(
        "Seconds per image", min_value=2, max_value=20, value=5
    )
    if st.button("Create MP4 from images + original song"):
        try:
            if not images:
                raise ValueError("Upload at least one image.")
            if not ffmpeg_available():
                raise RuntimeError(
                    "FFmpeg is not installed. See README.md for installation."
                )
            paths = []
            for index, image in enumerate(images, 1):
                suffix = Path(image.name).suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                    suffix = ".png"
                destination = APP_DIR / f"still_{index:03d}{suffix}"
                destination.write_bytes(image.getvalue())
                paths.append(destination)
            output = APP_DIR / "music_video.mp4"
            with st.spinner("Rendering MP4…"):
                create_still_image_video(
                    Path(st.session_state["song_path"]), paths, output,
                    aspect, seconds_per_image
                )
            video_bytes = output.read_bytes()
            st.session_state["latest_video_path"] = str(output)
            st.session_state["latest_video_name"] = output.name
            st.success("MP4 created.")
            st.video(video_bytes)
            st.download_button(
                "Download final MP4",
                data=video_bytes,
                file_name="music_video.mp4",
                mime="video/mp4",
            )
        except Exception as exc:
            st.error(f"MP4 creation failed: {exc}")


    st.header("7. Download for your platform")
    st.write(
        "Choose a destination platform to export a compatible MP4. The app "
        "resizes/crops or fits the video, converts it to H.264 video + AAC audio, "
        "and enables fast-start playback. The original uploaded song can replace "
        "the video's audio track."
    )
    if not ffmpeg_available():
        st.warning("FFmpeg/FFprobe is required for platform conversion.")
    latest_path_value = st.session_state.get("latest_video_path")
    if not latest_path_value or not Path(latest_path_value).exists():
        st.info(
            "First create an MP4 from still images or render a video with ComfyUI. "
            "When a video output is available, it will appear here for platform export."
        )
    else:
        latest_video_path = Path(latest_path_value)
        platform_name = st.selectbox(
            "Platform / format",
            list(PLATFORM_PRESETS.keys()),
            key="platform_export_target",
        )
        preset = PLATFORM_PRESETS[platform_name]
        st.caption(
            f"Export target: {preset['size'][0]} × {preset['size'][1]} px, "
            f"{preset['ratio']}, {preset['fps']} fps, MP4 (H.264 + AAC)."
        )
        fit_mode = st.selectbox(
            "How should the video fit?",
            ["Crop to fill", "Fit with blurred background", "Fit with black bars"],
            help=(
                "Crop to fill may cut off the sides/top. Blurred background preserves "
                "the full frame. Black bars preserve the full frame on black."
            ),
            key="platform_fit_mode",
        )
        replace_audio = st.checkbox(
            "Use the original uploaded song as the final audio track",
            value=True,
            help="Recommended when the generated clip has silent, incorrect, or temporary audio.",
            key="platform_replace_audio",
        )
        if st.button("Convert video for selected platform", type="primary"):
            try:
                if not ffmpeg_available():
                    raise RuntimeError("FFmpeg is unavailable on this host.")
                source_song_value = st.session_state.get("song_path")
                source_song = Path(source_song_value) if source_song_value else None
                if replace_audio and (source_song is None or not source_song.is_file()):
                    raise FileNotFoundError("The original uploaded song is not available in this session.")
                safe_platform = re.sub(r"[^A-Za-z0-9]+", "_", platform_name).strip("_").lower()
                export_path = APP_DIR / f"{safe_platform}_ready.mp4"
                with st.spinner(f"Preparing {platform_name} MP4…"):
                    export_platform_video(
                        latest_video_path,
                        source_song if source_song is not None and source_song.is_file() else latest_video_path,
                        export_path,
                        preset,
                        fit_mode=fit_mode,
                        replace_audio=replace_audio,
                    )
                export_bytes = export_path.read_bytes()
                st.success(f"{platform_name} export is ready.")
                st.video(export_bytes)
                st.download_button(
                    f"Download {platform_name} MP4",
                    data=export_bytes,
                    file_name=f"{safe_platform}_ready.mp4",
                    mime="video/mp4",
                    key=f"download_platform_{safe_platform}",
                )
            except Exception as exc:
                st.error(f"Platform export failed: {exc}")

st.divider()
st.caption(
    "Integration project; not affiliated with Geekatplay Studio. "
    "Upstream reference: https://github.com/GeekatplayStudio/ComfyUI-Music-to-Video. "
    "See README.md for licensing, setup, limitations and deployment notes."
)
