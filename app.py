"""Automatic hybrid AI + stock-footage music video maker.
Upload a song and topic/story; fetch Pixabay footage and generate clips with fal.ai.
No captions, lyrics, subtitles, or text overlays are added.
"""
from __future__ import annotations
import json, math, re, shutil, subprocess, tempfile, time
from pathlib import Path
from urllib.parse import urlparse
import requests
import streamlit as st

st.set_page_config(page_title="AI Music Video Studio", page_icon="🎬", layout="centered")
FORMATS = {
    "YouTube widescreen (16:9)": ((1280, 720), "16:9", "landscape"),
    "Vertical video (9:16)": ((720, 1280), "9:16", "portrait"),
    "Square video (1:1)": ((900, 900), "1:1", "square"),
}
AUDIO_TYPES = ["mp3", "wav", "m4a", "aac", "ogg", "flac"]
PIXABAY_VIDEO_SEARCH = "https://pixabay.com/api/videos/"
FAL_ENDPOINT = "fal-ai/wan/v2.2-5b/text-to-video"
NEGATIVE_PROMPT = "subtitles, captions, text, letters, logos, watermarks, still image, static frame, low quality, blurry, distorted anatomy"

def get_secret(name: str, label: str, help_text: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value or st.text_input(label, type="password", help=help_text)).strip()

def run_command(command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or "FFmpeg command failed.")[-4000:])

def probe_duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError("Could not read media duration.")
    try:
        return max(0.1, float(result.stdout.strip()))
    except ValueError as exc:
        raise RuntimeError("FFprobe returned an invalid duration.") from exc

def make_scenes(topic: str, story: str, count: int) -> list[str]:
    chunks = re.split(r"(?<=[.!?।])\s+|\n+", (story or "").strip())
    chunks = [re.sub(r"\s+", " ", x).strip(" -•\t") for x in chunks if x.strip()]
    if not chunks:
        chunks = [
            f"Opening establishing shot about {topic}",
            f"A person taking the first step in a story about {topic}",
            f"A challenge or turning point related to {topic}",
            f"Progress and change related to {topic}",
            f"An inspiring final scene about {topic}",
        ]
    stages = ["wide establishing shot", "medium shot showing action", "close-up of meaningful details",
              "dynamic movement shot", "wide emotional closing shot"]
    scenes = []
    for i in range(count):
        seed = chunks[i % len(chunks)]
        if len(chunks) == 1 and count > 1:
            seed = f"{stages[i % len(stages)]}: {seed}"
        scenes.append(
            f"{seed}. Cinematic photorealistic live-action footage, coherent visual story, "
            "natural human movement, expressive composition, beautiful practical lighting, "
            "smooth camera movement, no text, no subtitles, no logos."
        )
    return scenes

def download_https(url: str, target: Path, max_bytes: int = 250_000_000) -> None:
    if urlparse(url).scheme != "https":
        raise RuntimeError("Media provider returned a non-HTTPS URL.")
    with requests.get(url, stream=True, timeout=(20, 120)) as response:
        response.raise_for_status()
        total = 0
        with target.open("wb") as out:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("A downloaded clip exceeded the 250 MB limit.")
                out.write(chunk)
    if not target.exists() or target.stat().st_size < 1024:
        raise RuntimeError("Media download was empty or incomplete.")

def search_pixabay(query: str, api_key: str) -> list[dict]:
    response = requests.get(PIXABAY_VIDEO_SEARCH,
        params={"key": api_key, "q": query[:100], "per_page": 12, "safesearch": "true",
                "order": "popular"}, timeout=30)
    if response.status_code in (401, 403):
        raise RuntimeError("Pixabay rejected the API key. Check PIXABAY_API_KEY.")
    response.raise_for_status()
    return response.json().get("hits", [])

def choose_pixabay_file(video: dict, target_size: tuple[int, int]) -> str | None:
    files = video.get("videos", {})
    candidates = []
    for quality in ("large", "medium", "small", "tiny"):
        item = files.get(quality, {})
        if item.get("url"):
            candidates.append(item)
    if not candidates:
        return None
    tw, th = target_size
    def score(item: dict) -> tuple:
        w, h = int(item.get("width") or 0), int(item.get("height") or 0)
        return (abs(w / h - tw / th) if h else 10, 0 if w >= min(tw, th) else 1, -(w * h))
    return sorted(candidates, key=score)[0].get("url")

def get_stock_clip(scene: str, api_key: str, orientation: str, size: tuple[int, int],
                   target: Path, used_ids: set[int]) -> tuple[str, str]:
    queries = list(dict.fromkeys([scene.split(".")[0][:90], " ".join(scene.split()[:6])]))
    for query in queries:
        videos = search_pixabay(query, api_key)
        candidates = [v for v in videos if int(v.get("id") or 0) not in used_ids] or videos
        for video in candidates:
            link = choose_pixabay_file(video, size)
            if not link:
                continue
            try:
                download_https(link, target)
                used_ids.add(int(video.get("id") or 0))
                creator = video.get("user", "Pixabay contributor")
                return f"Stock footage from Pixabay — {creator}", video.get("pageURL", "https://pixabay.com/videos/")
            except (requests.RequestException, RuntimeError):
                continue
    raise RuntimeError(f"No downloadable Pixabay clip found for scene: {queries[-1]}")

def generate_ai_clip(prompt: str, api_key: str, aspect_ratio: str, target: Path, status_line) -> None:
    base = f"https://queue.fal.run/{FAL_ENDPOINT}"
    headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "negative_prompt": NEGATIVE_PROMPT, "aspect_ratio": aspect_ratio,
               "resolution": "720p", "enable_safety_checker": True, "enable_prompt_expansion": True}
    response = requests.post(base, headers=headers, json=payload, timeout=60)
    if response.status_code in (401, 403):
        raise RuntimeError("fal.ai rejected the key. Check FAL_KEY and account access.")
    response.raise_for_status()
    data = response.json()
    request_id = data.get("request_id")
    status_url = data.get("status_url") or (f"{base}/requests/{request_id}/status" if request_id else "")
    result_url = data.get("response_url") or (f"{base}/requests/{request_id}" if request_id else "")
    if not request_id or not status_url or not result_url:
        raise RuntimeError("fal.ai did not return a valid queued request ID.")
    deadline = time.time() + 900
    while time.time() < deadline:
        check = requests.get(status_url, headers={"Authorization": f"Key {api_key}"}, timeout=30)
        check.raise_for_status()
        status_data = check.json()
        status = str(status_data.get("status", "")).upper()
        status_line(f"AI clip status: {status or 'waiting'}…")
        if status == "COMPLETED":
            break
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError("fal.ai generation failed: " + json.dumps(status_data)[:1000])
        time.sleep(3)
    else:
        raise RuntimeError("AI generation exceeded 15 minutes. Try fewer AI clips.")
    result_response = requests.get(result_url, headers={"Authorization": f"Key {api_key}"}, timeout=60)
    result_response.raise_for_status()
    video_url = (result_response.json().get("video") or {}).get("url")
    if not video_url:
        raise RuntimeError("fal.ai response did not include a video URL.")
    download_https(video_url, target)

def normalize_clip(source: Path, target: Path, size: tuple[int, int]) -> None:
    w, h = size
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps=30,format=yuv420p"
    run_command(["ffmpeg", "-y", "-i", str(source), "-an", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", str(target)])

def assemble_video(clips: list[Path], audio: Path, output: Path, work: Path,
                   size: tuple[int, int], progress) -> None:
    audio_duration = probe_duration(audio)
    total = sum(probe_duration(p) for p in clips)
    if total <= 0:
        raise RuntimeError("No usable clip duration was found.")
    repeats = max(1, math.ceil(audio_duration / total))
    playlist = work / "clips.txt"
    lines = []
    for _ in range(repeats):
        for path in clips:
            escaped = path.resolve().as_posix().replace("'", "'\\\\''")
            lines.append(f"file '{escaped}'")
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    progress.progress(0.86, text="Joining footage and adding your soundtrack…")
    w, h = size
    run_command(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(playlist),
        "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-t", f"{audio_duration:.3f}",
        "-vf", f"scale={w}:{h},setsar=1,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
        "-b:a", "192k", "-af", "aresample=async=1", "-shortest", "-movflags", "+faststart", str(output)])

st.title("🎬 AI Music Video Studio")
st.write("Upload a song and enter a topic/story. The app automatically combines original AI-generated clips with real stock footage.")
st.info("No captions, lyrics, subtitles, or text overlays. AI generation through fal.ai may incur charges; stock footage comes from Pixabay.")

with st.expander("API access setup", expanded=True):
    st.markdown(
        "Configure Streamlit secrets named FAL_KEY and PIXABAY_API_KEY, or enter keys below. "
        "Keep keys private. [Get a fal.ai key](https://fal.ai/dashboard/keys) · "
        "[Get a Pixabay API key](https://pixabay.com/api/docs/)"
    )
    fal_key = get_secret("FAL_KEY", "fal.ai API key (required when AI clips are selected)", "Used privately by the server to request AI-generated video.")
    pexels_key = get_secret("PEXELS_API_KEY", "Pixabay API key (required when stock clips are selected)", "Used privately to search and download stock video.")

topic = st.text_input("1. Video topic", placeholder="e.g. Never give up, Punjabi motivational song")
story = st.text_area("2. Story or scene guidance (optional)",
    placeholder="Write a short story or put one scene on each line.", height=120)
song = st.file_uploader("3. Upload your soundtrack", type=AUDIO_TYPES)
format_name = st.selectbox("4. Video format", list(FORMATS.keys()))
mix = st.slider("AI-generated footage in the mix (%)", 0, 100, 40, 20,
    help="Remaining scenes use real stock footage from Pexels. AI generation may be billed by the provider.")
scene_count = st.slider("Number of scenes", 2, 12, 6)
st.caption("Each AI scene is a short clip. The app repeats the scene sequence if needed to cover the song.")

if st.button("Generate music video", type="primary", disabled=not (topic.strip() and song)):
    size, aspect_ratio, orientation = FORMATS[format_name]
    ai_count = round(scene_count * mix / 100)
    stock_count = scene_count - ai_count
    if ai_count and not fal_key:
        st.error("Enter a fal.ai API key or configure FAL_KEY in Streamlit secrets.")
    elif stock_count and not pexels_key:
        st.error("Enter a Pixabay API key or configure PEXELS_API_KEY in Streamlit secrets.")
    elif not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        st.error("FFmpeg and FFprobe are required. Ensure ffmpeg is listed in packages.txt and the app has redeployed.")
    else:
        scenes = make_scenes(topic.strip(), story, scene_count)
        ai_slots = set()
        if ai_count:
            for j in range(ai_count):
                ai_slots.add(round(j * (scene_count - 1) / max(1, ai_count - 1)) if ai_count > 1 else scene_count // 2)
        while len(ai_slots) > ai_count:
            ai_slots.remove(max(ai_slots))
        while len(ai_slots) < ai_count:
            ai_slots.add(next(i for i in range(scene_count) if i not in ai_slots))
        progress = st.progress(0, text="Preparing your scenes…")
        credits = []
        used_ids: set[int] = set()
        try:
            with tempfile.TemporaryDirectory(prefix="ai_music_video_") as temp_dir:
                work = Path(temp_dir)
                audio = work / ("soundtrack" + (Path(song.name).suffix.lower() or ".mp3"))
                audio.write_bytes(song.getvalue())
                normalized_clips = []
                for index, scene in enumerate(scenes):
                    raw = work / f"raw_{index:03d}.mp4"
                    normalized = work / f"clip_{index:03d}.mp4"
                    is_ai = index in ai_slots
                    status = st.empty()
                    status.info(f"Scene {index + 1}/{scene_count}: {'Generating AI footage' if is_ai else 'Searching stock footage'}")
                    if is_ai:
                        generate_ai_clip(scene, fal_key, aspect_ratio, raw,
                            lambda message: status.info(f"Scene {index + 1}/{scene_count}: {message}"))
                        source_label, source_url = "AI-generated footage (fal.ai)", ""
                    else:
                        source_label, source_url = get_stock_clip(scene, pexels_key, orientation, size, raw, used_ids)
                    normalize_clip(raw, normalized, size)
                    normalized_clips.append(normalized)
                    credits.append({"scene": index + 1, "type": source_label, "source": source_url, "prompt": scene})
                    progress.progress(0.05 + 0.75 * (index + 1) / scene_count, text=f"Prepared scene {index + 1} of {scene_count}")
                    status.empty()
                output = work / "ai_music_video.mp4"
                assemble_video(normalized_clips, audio, output, work, size, progress)
                video_bytes = output.read_bytes()
            progress.progress(1.0, text="Video ready.")
            st.success("Your music video is ready — no captions or text overlays were added.")
            st.video(video_bytes)
            st.download_button("Download MP4", data=video_bytes, file_name="ai_music_video.mp4",
                mime="video/mp4", type="primary")
            with st.expander("Scene sources and credits"):
                for item in credits:
                    st.markdown(f"**Scene {item['scene']} — {item['type']}**")
                    if item["source"]:
                        st.markdown(f"[View source clip]({item['source']})")
                    st.caption(item["prompt"])
                st.markdown("Stock footage provided by [Pexels](https://pixabay.com/).")
        except Exception as exc:
            st.error("The video could not be created.")
            st.code(str(exc))
            st.caption("Check API keys, provider credits/limits, internet access, and FFmpeg deployment logs.")

st.caption("Use AI provider services according to their terms. Pixabay footage is subject to the Pexels license and API guidelines.")
