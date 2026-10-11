"""MP3-only AI music-video generator using Replicate + FFmpeg."""
from __future__ import annotations
import math, os, re, shutil, subprocess, zipfile
from pathlib import Path
from typing import Any
import requests
import streamlit as st
try:
    import replicate
except Exception:
    replicate = None

OUT = Path("music_video_output")
OUT.mkdir(exist_ok=True)
MODEL = "minimax/video-01"
WHISPER = "openai/whisper"
MAX_MB = 100
PRESETS = {
    "Instagram Reels": (1080, 1920), "Instagram Stories": (1080, 1920),
    "Instagram Feed - Square": (1080, 1080), "Instagram Feed - Portrait": (1080, 1350),
    "Facebook Reels": (1080, 1920), "Facebook Feed - Landscape": (1920, 1080),
    "WhatsApp Status": (1080, 1920), "YouTube - Standard": (1920, 1080),
    "YouTube Shorts": (1080, 1920), "TikTok": (1080, 1920),
    "X - Twitter": (1920, 1080), "LinkedIn": (1920, 1080),
    "Pinterest Idea Pin": (1080, 1920), "Universal - 720p Landscape": (1280, 720),
}
st.set_page_config(page_title="MP3 to AI Music Video", page_icon="🎬", layout="centered")
st.title("🎬 MP3 → AI Music Video")
st.write("Upload one MP3, click Generate, and receive a finished AI music video plus platform-ready MP4 files.")
st.info("This uses Replicate's paid Minimax Video-01 model. Each scene is approximately 6 seconds and is billed by the provider. A 3-minute song can need about 30 video generations. Add your API token once in Streamlit Secrets.")

with st.expander("One-time setup"):
    st.markdown("1. Create an account at https://replicate.com/ and create an API token at https://replicate.com/account/api-tokens.\n2. In Streamlit Cloud, open App settings → Secrets and add the line below, using your real token:\n\n    REPLICATE_API_TOKEN = \"r8_your_token_here\"\n\n3. Save and reboot the app. Never put the token in GitHub.")

def cmd(args: list[str], timeout: int = 3600) -> tuple[bool, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode == 0, p.stdout if p.returncode == 0 else (p.stderr or p.stdout)
    except FileNotFoundError:
        return False, f"Missing executable: {args[0]}"
    except subprocess.TimeoutExpired:
        return False, "FFmpeg timed out."

def duration(path: Path) -> float:
    ok, out = cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], 60)
    if not ok:
        raise RuntimeError("Could not read MP3 duration. Check that FFmpeg/FFprobe is installed.")
    try:
        result = float(out.strip())
    except ValueError as exc:
        raise RuntimeError("Invalid MP3 duration.") from exc
    if result <= 0:
        raise RuntimeError("The MP3 appears to be empty.")
    return result

def token() -> str:
    try:
        value = st.secrets.get("REPLICATE_API_TOKEN", "")
    except Exception:
        value = ""
    return value or os.getenv("REPLICATE_API_TOKEN", "")

def save_model_file(output: Any, path: Path) -> Path:
    if isinstance(output, (list, tuple)):
        if not output:
            raise RuntimeError("The AI model returned no output.")
        output = output[0]
    if hasattr(output, "read"):
        path.write_bytes(output.read())
    elif isinstance(output, str) and output.startswith(("https://", "http://")):
        with requests.get(output, stream=True, timeout=600) as response:
            response.raise_for_status()
            with path.open("wb") as f:
                for block in response.iter_content(1024 * 1024):
                    if block:
                        f.write(block)
    else:
        raise RuntimeError("Unexpected AI model output type: " + type(output).__name__)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("AI model returned an empty video.")
    return path

def transcribe(song: Path) -> str:
    try:
        with song.open("rb") as f:
            result = replicate.run(WHISPER, input={"audio": f})
        if isinstance(result, dict):
            return str(result.get("transcription") or result.get("text") or "").strip()
        return str(result).strip()
    except Exception:
        return ""

def prompts_for(transcript: str, count: int, filename: str) -> list[str]:
    clean = re.sub(r"\s+", " ", transcript).strip()
    if clean:
        words = clean.split()
        size = max(1, math.ceil(len(words) / count))
        themes = [" ".join(words[i * size:(i + 1) * size]) or "instrumental emotional transition" for i in range(count)]
    else:
        arc = ["an evocative opening establishing shot", "a protagonist beginning a journey", "a moment of longing and emotional tension", "a dramatic obstacle and struggle", "hope and determination building", "an uplifting cinematic finale"]
        themes = [arc[i % len(arc)] for i in range(count)]
    return [
        "Create a polished cinematic moving music-video scene inspired by: " + theme + ". "
        "Keep the same fictional lead character, face, age and clothing throughout all scenes. "
        "Expressive acting, natural movement, dynamic camera motion, beautiful cinematic lighting, "
        "coherent action, detailed environment, professional film look. No text, captions, logos or watermark. "
        f"Scene {i+1} of {count}. Song context: {Path(filename).stem}. Do not create a still image."
        for i, theme in enumerate(themes)
    ]

def make_clip(prompt: str, path: Path) -> Path:
    result = replicate.run(MODEL, input={"prompt": prompt, "prompt_optimizer": True})
    return save_model_file(result, path)

def make_master(clips: list[Path], song: Path, target: Path) -> Path:
    norm = OUT / "normalized"
    norm.mkdir(exist_ok=True)
    normalized = []
    for i, clip in enumerate(clips, 1):
        p = norm / f"clip_{i:04d}.mp4"
        ok, err = cmd(["ffmpeg", "-y", "-i", str(clip), "-an", "-vf",
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=24,setsar=1,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-movflags", "+faststart", str(p)], 900)
        if not ok:
            raise RuntimeError(f"Could not normalize clip {i}: {err[-1000:]}")
        normalized.append(p)
    concat = norm / "concat.txt"
    with concat.open("w", encoding="utf-8") as f:
        for p in normalized:
            f.write("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n")
    silent = norm / "joined.mp4"
    ok, err = cmd(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an", str(silent)], 3600)
    if not ok:
        raise RuntimeError("Could not join AI scenes: " + err[-1200:])
    song_seconds = duration(song)
    ok, err = cmd(["ffmpeg", "-y", "-i", str(silent), "-i", str(song), "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(song_seconds), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)], 3600)
    if not ok:
        raise RuntimeError("Could not add original song audio: " + err[-1200:])
    return target

def platform_export(master: Path, song: Path, target: Path, size: tuple[int, int]) -> Path:
    w, h = size
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1,format=yuv420p"
    ok, err = cmd(["ffmpeg", "-y", "-i", str(master), "-i", str(song), "-map", "0:v:0", "-map", "1:a:0",
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(target)], 3600)
    if not ok:
        raise RuntimeError(f"Could not export {target.name}: {err[-1000:]}")
    return target

def make_all_exports(master: Path, song: Path, zip_path: Path, progress, message) -> list[Path]:
    files = []
    for i, (name, size) in enumerate(PRESETS.items(), 1):
        slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
        path = OUT / f"{slug}_ready.mp4"
        message.write(f"Exporting {i}/{len(PRESETS)}: {name}")
        platform_export(master, song, path, size)
        files.append(path)
        progress.progress(int(i * 100 / len(PRESETS)))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(master, "master_music_video.mp4")
        for path in files:
            z.write(path, path.name)
    return files

song_upload = st.file_uploader("Upload your MP3", type=["mp3"])
if song_upload:
    if song_upload.size > MAX_MB * 1024 * 1024:
        st.error(f"Maximum upload size is {MAX_MB} MB.")
        st.stop()
    song_path = OUT / "uploaded_song.mp3"
    song_path.write_bytes(song_upload.getvalue())
    st.audio(song_upload.getvalue(), format="audio/mp3")
    if not token():
        st.error("Add REPLICATE_API_TOKEN in Streamlit Cloud → App settings → Secrets. See One-time setup above.")
        st.stop()
    if replicate is None:
        st.error("Replicate package unavailable. Redeploy after checking requirements.txt.")
        st.stop()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        st.error("FFmpeg/FFprobe unavailable. Confirm packages.txt contains ffmpeg and redeploy.")
        st.stop()
    try:
        seconds = duration(song_path)
        count = math.ceil(seconds / 6)
        st.write(f"Song length: **{int(seconds//60)} min {int(seconds%60)} sec**")
        st.write(f"Automatic plan: **{count} AI-generated scenes** and {len(PRESETS)} platform export formats.")
        if count > 40:
            st.warning("This long song needs more than 40 paid video generations. Streamlit Cloud may time out; a server with longer job limits is recommended.")
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    if st.button("✨ Generate complete AI music video", type="primary", use_container_width=True):
        try:
            st.session_state.pop("master_path", None)
            st.session_state.pop("zip_path", None)
            status = st.status("Producing your music video…", expanded=True)
            status.write("1/4 · Transcribing vocals")
            transcript = transcribe(song_path)
            status.write("Lyrics detected." if transcript else "No reliable transcription; creating a cinematic visual storyline.")
            scene_prompts = prompts_for(transcript, count, song_upload.name)
            clips = []
            pbar = st.progress(0)
            for i, prompt in enumerate(scene_prompts, 1):
                status.write(f"2/4 · Generating AI scene {i}/{count}")
                clip = OUT / f"ai_scene_{i:04d}.mp4"
                make_clip(prompt, clip)
                clips.append(clip)
                pbar.progress(int(i * 100 / count))
            status.write("3/4 · Joining scenes and adding original song")
            master = OUT / "master_music_video.mp4"
            make_master(clips, song_path, master)
            st.session_state["master_path"] = str(master)
            status.write("4/4 · Exporting all platform versions")
            exbar = st.progress(0)
            exmsg = st.empty()
            zip_path = OUT / "music_video_all_platforms.zip"
            exports = make_all_exports(master, song_path, zip_path, exbar, exmsg)
            st.session_state["zip_path"] = str(zip_path)
            st.session_state["exports"] = [str(x) for x in exports]
            status.update(label="Video generation complete", state="complete", expanded=False)
            st.success("Your video and all platform versions are ready.")
        except Exception as exc:
            st.error(f"Generation failed: {exc}")
            st.info("Check the Replicate token and billing. Every generated scene is billable. Long songs may exceed Streamlit Cloud's execution limit; use a server with longer job limits if needed.")
            st.exception(exc)

master_value = st.session_state.get("master_path")
if master_value and Path(master_value).exists():
    master = Path(master_value)
    st.subheader("Finished music video")
    st.video(str(master))
    st.download_button("Download master MP4", master.read_bytes(), "master_music_video.mp4", "video/mp4", use_container_width=True)
    zip_value = st.session_state.get("zip_path")
    if zip_value and Path(zip_value).exists():
        zp = Path(zip_value)
        st.download_button("Download all platform MP4s (ZIP)", zp.read_bytes(), "music_video_all_platforms.zip", "application/zip", use_container_width=True)
    with st.expander("Individual platform downloads"):
        for item in st.session_state.get("exports", []):
            p = Path(item)
            if p.exists():
                st.download_button("Download " + p.name, p.read_bytes(), p.name, "video/mp4", key="download_" + p.name)

st.caption("Generated video quality and character consistency can vary. Platform presets crop to fill their target dimensions; preview before publishing. Hosted storage may be temporary, so download your files.")
