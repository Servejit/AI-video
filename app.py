"""Automatic hybrid AI + stock-footage music video maker.
Upload a song and topic/story; search public Internet Archive footage without a stock API key and generate clips with fal.ai.
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
ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA = "https://archive.org/metadata/"
FAL_ENDPOINT = "fal-ai/wan/v2.2-5b/text-to-video"
NEGATIVE_PROMPT = "subtitles, captions, text, letters, logos, watermarks, still image, static frame, low quality, blurry, distorted anatomy"

def get_secret(name: str, label: str, help_text: str) -> str:
    # A typed override must take precedence over Streamlit secrets, so users can
    # replace a stale/rejected secret without editing deployment settings first.
    try:
        saved_value = str(st.secrets.get(name, "") or "").strip()
    except Exception:
        saved_value = ""
    if saved_value:
        st.caption(f"{name} is configured in Streamlit Secrets. You can enter a replacement below if that saved key is rejected.")
        override = st.text_input(
            f"{label} — optional replacement",
            type="password",
            help=help_text,
            key=f"{name}_override",
        ).strip()
        return override or saved_value
    return st.text_input(label, type="password", help=help_text, key=f"{name}_input").strip()

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

def search_archive(query: str) -> list[dict]:
    # Public Internet Archive search endpoint; no API key is required.
    terms = re.sub(r"[^A-Za-z0-9 ]", " ", query).strip()
    terms = " ".join(terms.split()[:7])
    if not terms:
        return []
    response = requests.get(ARCHIVE_SEARCH, params={
        "q": f"mediatype:movies AND {terms}",
        "fl[]": ["identifier", "title", "description", "licenseurl"],
        "rows": 12, "page": 1, "output": "json"
    }, timeout=35)
    response.raise_for_status()
    return response.json().get("response", {}).get("docs", [])

def archive_metadata(identifier: str) -> dict:
    response = requests.get(f"{ARCHIVE_METADATA}{identifier}", timeout=35)
    response.raise_for_status()
    return response.json()

def archive_license_allowed(metadata: dict) -> bool:
    # Require an explicit public-domain or Creative Commons license in item metadata.
    license_text = " ".join(str(metadata.get(k, "")) for k in ("licenseurl", "rights", "license")).lower()
    return any(term in license_text for term in (
        "creativecommons.org/publicdomain", "creativecommons.org/licenses/by/",
        "creativecommons.org/licenses/by-sa/", "creativecommons.org/licenses/zero/"
    ))

def choose_archive_video(metadata: dict, size: tuple[int, int]) -> str | None:
    candidates = []
    for item in metadata.get("files", []):
        name = str(item.get("name", ""))
        fmt = str(item.get("format", "")).lower()
        if name.lower().endswith((".mp4", ".m4v", ".mov", ".webm")) or "mpeg4" in fmt or "webm" in fmt:
            try:
                file_size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                file_size = 0
            if file_size >= 200_000 and not name.lower().endswith(("_thumb.mp4", "_preview.mp4")):
                candidates.append((file_size, name))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (0 if x[1].lower().endswith(".mp4") else 1,
                                   abs(min(x[0], 150_000_000) - 35_000_000)))
    return candidates[0][1]

def get_stock_clip(scene: str, orientation: str, size: tuple[int, int],
                   target: Path, used_ids: set[str]) -> tuple[str, str]:
    queries = list(dict.fromkeys([scene.split(".")[0][:90], " ".join(scene.split()[:6]),
                                  " ".join(scene.split()[:3])]))
    for query in queries:
        for item in search_archive(query):
            identifier = str(item.get("identifier", ""))
            if not identifier or identifier in used_ids:
                continue
            try:
                data = archive_metadata(identifier)
                meta = data.get("metadata", {})
                if not archive_license_allowed(meta):
                    continue
                filename = choose_archive_video(data, size)
                if not filename:
                    continue
                media_url = f"https://archive.org/download/{identifier}/{requests.utils.quote(filename, safe='/')}"
                download_https(media_url, target, max_bytes=250_000_000)
                used_ids.add(identifier)
                title = meta.get("title") or item.get("title") or identifier
                return f"Internet Archive footage — {title} (CC/public domain)", f"https://archive.org/details/{identifier}"
            except (requests.RequestException, RuntimeError, ValueError):
                continue
    raise RuntimeError(
        f"No downloadable public-domain/Creative Commons video was found for: {queries[-1]}. "
        "Try a simpler topic (for example nature, mountains, city, ocean) or increase the AI footage percentage."
    )

def generate_ai_clip(prompt: str, endpoint: str, token: str, aspect_ratio: str, target: Path, status_line) -> None:
    """Request a clip from the user's temporary, self-hosted Wan 2.1 Colab runtime."""
    base = endpoint.strip().rstrip("/")
    if not base.startswith("https://"):
        raise RuntimeError("Colab endpoint must be the HTTPS trycloudflare.com URL printed by the notebook.")
    headers = {"X-API-Token": token}
    response = requests.post(
        f"{base}/generate",
        headers=headers,
        json={"prompt": prompt, "width": 832, "height": 480, "num_frames": 33, "num_inference_steps": 20},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Colab Wan server returned HTTP {response.status_code}: {response.text[:1000]}")
    job_id = response.json().get("job_id")
    if not job_id:
        raise RuntimeError("Colab Wan server did not return a job ID. Restart the Colab notebook and try again.")
    deadline = time.time() + 1200
    while time.time() < deadline:
        check = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=30)
        check.raise_for_status()
        info = check.json()
        status = str(info.get("status", "")).lower()
        status_line(f"Wan 2.1 on Colab: {status or 'waiting'}…")
        if status == "completed":
            video = requests.get(f"{base}/download/{job_id}", headers=headers, timeout=180)
            video.raise_for_status()
            target.write_bytes(video.content)
            if target.stat().st_size < 1024:
                raise RuntimeError("Colab returned an empty video. Check the notebook output for errors.")
            return
        if status == "failed":
            raise RuntimeError("Wan 2.1 generation failed in Colab: " + str(info.get("error", "Unknown error"))[:1200])
        time.sleep(4)
    raise RuntimeError("Wan 2.1 generation took over 20 minutes. The free Colab runtime may be overloaded or disconnected.")

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
st.write("Upload a song and enter a topic/story. Generate short clips with open-source Wan 2.1 running on your own Google Colab GPU session, then combine them with your soundtrack.")
st.info("No paid video API is used. Wan 2.1 is open source; Google Colab free GPU availability and session duration are controlled by Google. Stock footage can be used for scenes you do not generate with AI.")

with st.expander("Connect free open-source AI (Google Colab)", expanded=True):
    st.markdown(
        "1. Open the [Wan 2.1 Colab notebook in this GitHub repository](https://github.com/Servejit/AI-video/blob/main/wan_colab.ipynb). "
        "2. Open it in Google Colab and run the cells using **Runtime → Change runtime type → T4 GPU** (if available). "
        "3. Copy the HTTPS tunnel URL and access token printed by the final cell into the fields below. "
        "Keep the token private. The notebook must stay running while your Streamlit app generates clips."
    )
    colab_url = st.text_input("Colab HTTPS endpoint URL", placeholder="https://example.trycloudflare.com").strip().rstrip("/")
    colab_token = st.text_input("Colab access token", type="password", help="Paste the token printed by your Colab notebook.").strip()

topic = st.text_input("1. Video topic", placeholder="e.g. Never give up, Punjabi motivational song")
story = st.text_area("2. Story or scene guidance (optional)",
    placeholder="Write a short story or put one scene on each line.", height=120)
song = st.file_uploader("3. Upload your soundtrack", type=AUDIO_TYPES)
format_name = st.selectbox("4. Video format", list(FORMATS.keys()))
mix = st.slider("AI-generated footage in the mix (%)", 0, 100, 40, 20,
    help="Remaining scenes use real stock footage from Internet Archive. AI generation may be billed by the provider.")
scene_count = st.slider("Number of scenes", 2, 12, 6)
st.caption("Wan 2.1 generates short 480p clips. The app resizes them to your selected format and repeats the scene sequence if needed to cover the song. Vertical/square formats are cropped from the generated landscape clip.")

if st.button("Generate music video", type="primary", disabled=not (topic.strip() and song)):
    size, aspect_ratio, orientation = FORMATS[format_name]
    ai_count = round(scene_count * mix / 100)
    stock_count = scene_count - ai_count
    if ai_count and (not colab_url or not colab_token):
        st.error("For AI-generated scenes, start the Wan 2.1 Google Colab notebook and enter its HTTPS endpoint URL and access token. Set AI-generated footage to 0% to use only stock footage without connecting Colab.")
    elif ai_count and not colab_url.startswith("https://"):
        st.error("The Colab endpoint must start with https://.")
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
        used_ids: set[str] = set()
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
                    status.info(f"Scene {index + 1}/{scene_count}: {'Generating open-source Wan 2.1 footage on Colab' if is_ai else 'Searching stock footage'}")
                    if is_ai:
                        generate_ai_clip(scene, colab_url, colab_token, aspect_ratio, raw,
                            lambda message: status.info(f"Scene {index + 1}/{scene_count}: {message}"))
                        source_label, source_url = "AI-generated footage (Wan 2.1, Google Colab)", ""
                    else:
                        source_label, source_url = get_stock_clip(scene, orientation, size, raw, used_ids)
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
                st.markdown("Stock footage searched from [Internet Archive](https://archive.org/details/movies). Review each clip’s source page and license before publishing.")
        except Exception as exc:
            st.error("The video could not be created.")
            st.code(str(exc))
            st.caption("For Wan 2.1 errors, confirm the Colab runtime is still connected and the endpoint/token match. Free GPU access is not guaranteed. Check the Colab cell output for model or memory errors.")

st.caption("Wan 2.1 model: Apache-2.0 license. Internet Archive items have item-specific rights; review each source page and license before publishing.")
