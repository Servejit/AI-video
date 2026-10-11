"""Free MP3-to-MP4 music video maker.
Uses explicitly licensed Internet Archive footage where available and animated
visualizer scenes as a fallback. No paid APIs, no Colab, no captions/lyrics/text overlays.
"""
from __future__ import annotations
import math, re, shutil, subprocess, tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import streamlit as st

st.set_page_config(page_title="Free AI Music Video Studio", page_icon="🎬", layout="centered")

FORMATS = {
    "YouTube widescreen (16:9)": (1280, 720),
    "Vertical video (9:16)": (720, 1280),
    "Square video (1:1)": (900, 900),
}
ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA = "https://archive.org/metadata/"
USER_AGENT = "FreeMusicVideoStudio/1.0 (open-source Streamlit app)"

def run_command(command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or "FFmpeg command failed.")[-3000:])

def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if result.returncode:
        raise RuntimeError("Could not read the MP3 duration. Please upload a valid MP3 file.")
    try:
        return max(0.1, float(result.stdout.strip()))
    except ValueError as exc:
        raise RuntimeError("The audio duration could not be read.") from exc

def make_scenes(topic: str, story: str, count: int) -> list[str]:
    lines = re.split(r"(?<=[.!?।])\s+|\n+", (story or "").strip())
    lines = [re.sub(r"\s+", " ", s).strip(" -•\t") for s in lines if s.strip()]
    if not lines:
        lines = [
            f"Opening and establishing imagery for {topic}",
            f"Beginning the journey in {topic}",
            f"Challenge, change, or discovery in {topic}",
            f"Progress and hope connected to {topic}",
            f"Emotional ending and resolution for {topic}",
        ]
    return [lines[i % len(lines)] for i in range(count)]

def keywords_for(scene: str, topic: str) -> list[str]:
    # Prefer concrete visual nouns over long prose for archive search.
    text = re.sub(r"[^A-Za-z0-9 ]", " ", scene + " " + topic).lower()
    words = [w for w in text.split() if len(w) > 3 and w not in {
        "with", "from", "that", "this", "into", "about", "scene", "imagery",
        "opening", "ending", "connected", "related", "story", "showing", "their",
        "there", "where", "while", "through", "emotional", "cinematic", "video",
    }]
    return list(dict.fromkeys(words))[:6]

def search_archive(query: str) -> list[dict]:
    response = requests.get(
        ARCHIVE_SEARCH,
        params={
            "q": f'mediatype:movies AND (title:("{query}") OR subject:("{query}"))',
            "fl[]": ["identifier", "title", "description", "licenseurl"],
            "rows": 15, "page": 1, "output": "json",
        },
        headers={"User-Agent": USER_AGENT}, timeout=(10, 30),
    )
    response.raise_for_status()
    return response.json().get("response", {}).get("docs", [])

def fetch_metadata(identifier: str) -> dict:
    response = requests.get(
        ARCHIVE_METADATA + quote(identifier, safe=""),
        headers={"User-Agent": USER_AGENT}, timeout=(10, 30),
    )
    response.raise_for_status()
    return response.json()

def license_is_open(meta: dict) -> bool:
    # Conservative allowlist. Items with no explicit license are not downloaded.
    values = " ".join(str(meta.get(k, "")) for k in ("licenseurl", "rights", "license")).lower()
    return any(x in values for x in (
        "creativecommons.org/publicdomain", "creativecommons.org/licenses/by/",
        "creativecommons.org/licenses/by-sa/", "creativecommons.org/licenses/zero/",
        "creativecommons.org/publicdomain/zero",
    )) or "public domain" in values

def select_video_file(files: list[dict]) -> str | None:
    candidates = []
    for item in files:
        name = str(item.get("name", ""))
        fmt = str(item.get("format", "")).lower()
        if not (name.lower().endswith((".mp4", ".m4v", ".mov", ".webm")) or
                "mpeg4" in fmt or "webm" in fmt):
            continue
        if any(x in name.lower() for x in ("thumb", "thumbnail", "preview", "sample")):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if 200_000 <= size <= 250_000_000:
            candidates.append((0 if name.lower().endswith(".mp4") else 1, abs(size - 25_000_000), name))
    candidates.sort()
    return candidates[0][2] if candidates else None

def download_media(url: str, destination: Path) -> None:
    if urlparse(url).scheme != "https":
        raise RuntimeError("The footage provider returned an unsafe URL.")
    with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=(15, 90)) as r:
        r.raise_for_status()
        total = 0
        with destination.open("wb") as out:
            for chunk in r.iter_content(1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 250_000_000:
                    raise RuntimeError("The downloaded stock clip exceeded 250 MB.")
                out.write(chunk)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("The downloaded footage was empty.")

def get_stock_clip(scene: str, topic: str, destination: Path, used: set[str]) -> tuple[str, str] | None:
    words = keywords_for(scene, topic)
    queries = [" ".join(words[:4]), " ".join(words[:2]), topic.strip()]
    for query in dict.fromkeys(q for q in queries if q):
        try:
            for item in search_archive(query):
                identifier = str(item.get("identifier", ""))
                if not identifier or identifier in used:
                    continue
                data = fetch_metadata(identifier)
                meta = data.get("metadata", {})
                if not license_is_open(meta):
                    continue
                filename = select_video_file(data.get("files", []))
                if not filename:
                    continue
                url = f"https://archive.org/download/{quote(identifier, safe='')}/{quote(filename, safe='/')}"
                try:
                    download_media(url, destination)
                except (requests.RequestException, RuntimeError):
                    continue
                used.add(identifier)
                title = str(meta.get("title") or item.get("title") or identifier)
                return title, f"https://archive.org/details/{quote(identifier, safe='')}"
        except (requests.RequestException, ValueError, KeyError):
            continue
    return None

def normalize_stock(source: Path, target: Path, size: tuple[int, int], seconds: float) -> None:
    w, h = size
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h},setsar=1,fps=30,format=yuv420p")
    run_command([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(source), "-t", f"{seconds:.3f}",
        "-an", "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "25", "-movflags", "+faststart", str(target),
    ])

def make_animated_scene(target: Path, size: tuple[int, int], seconds: float, seed: int) -> None:
    """Generate a moving abstract music-video background without any external service."""
    w, h = size
    palettes = [
        ("0x07152B", "0x1E6B83"), ("0x24113D", "0xB03A73"),
        ("0x081F17", "0x4E9A68"), ("0x321609", "0xD17A28"),
        ("0x11112E", "0x5265D8"), ("0x29101C", "0xC94D5A"),
    ]
    c1, c2 = palettes[seed % len(palettes)]
    # Animated flowing gradients, subtle pulses, and geometric rings; no text.
    vf = (
        f"geq=r='(1-Y/H)*255*0.03+{int(c1[2:4],16)}*(Y/H)+"
        f"{int(c2[2:4],16)}*(1-Y/H)*0.2':"
        f"g='(1-Y/H)*{int(c1[4:6],16)}+Y/H*{int(c2[4:6],16)}':"
        f"b='(1-Y/H)*{int(c1[6:8],16)}+Y/H*{int(c2[6:8],16)}',"
        f"drawbox=x='mod(t*{23+seed%13}+{seed*17},{w})':"
        f"y=0:w={max(12,w//90)}:h={h}:color=white@0.10:t=fill,"
        f"drawbox=x=0:y='mod(t*{17+seed%11}+{seed*31},{h})':"
        f"w={w}:h={max(8,h//120)}:color=white@0.06:t=fill,"
        f"eq=brightness='0.03*sin(2*PI*t/4)':saturation=1.25,"
        f"format=yuv420p"
    )
    # FFmpeg's geq color syntax varies across builds; use a robust color source
    # and animated light bands for maximum Streamlit Cloud compatibility.
    fallback_vf = (
        f"drawbox=x='mod(t*{max(20,w//30)}+{seed*19},{w})':y=0:"
        f"w={max(30,w//8)}:h={h}:color={c2}@0.32:t=fill,"
        f"drawbox=x=0:y='mod(t*{max(12,h//35)}+{seed*23},{h})':"
        f"w={w}:h={max(20,h//9)}:color=white@0.07:t=fill,"
        f"eq=brightness='0.04*sin(2*PI*t/5)':saturation=1.35,format=yuv420p"
    )
    try:
        run_command([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c={c1}:s={w}x{h}:r=24:d={seconds:.3f}",
            "-vf", fallback_vf, "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "25", "-pix_fmt", "yuv420p", str(target),
        ])
    except RuntimeError:
        # Basic fallback still produces a valid moving clip.
        run_command([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c={c1}:s={w}x{h}:r=24:d={seconds:.3f}",
            "-vf", "format=yuv420p", "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(target),
        ])

def build_mp4(clips: list[Path], audio: Path, output: Path, work: Path,
              size: tuple[int, int], audio_duration: float, progress) -> None:
    # Concatenate normalized clips and map the original uploaded soundtrack.
    playlist = work / "concat.txt"
    playlist.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in clips), encoding="utf-8")
    w, h = size
    progress.progress(0.88, text="Joining scenes and adding the original MP3…")
    run_command([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(playlist), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-t", f"{audio_duration:.3f}",
        "-vf", f"scale={w}:{h},setsar=1,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
        "-c:a", "aac", "-b:a", "192k", "-af", "aresample=async=1",
        "-shortest", "-movflags", "+faststart", str(output),
    ])

st.title("🎬 Free Music Video Studio")
st.write("**MP3 in → MP4 out.** Build a cinematic music video from your own AI-generated actor clips, free licensed stock footage, and animated scenes.")
st.info("Free-only workflow: no paid APIs and no API key. This hosted app does not have enough guaranteed GPU capacity to generate photorealistic AI actors itself. For realistic AI acting, generate clips with a free/open-source video model on compatible local hardware, then upload them below. The app edits those clips to your MP3; if you do not upload enough clips, it can use explicitly licensed stock footage or locally generated animated scenes.")

topic = st.text_input("1. Video topic", placeholder="e.g. Never give up, motivational song")
story = st.text_area("2. Story or scene guidance (optional)", placeholder="Write a short story or put one scene on each line.", height=120)
song = st.file_uploader("3. Upload your MP3", type=["mp3"])
ai_clips = st.file_uploader(
    "4. Optional: upload AI-generated actor scene clips",
    type=["mp4", "mov", "m4v", "webm"], accept_multiple_files=True,
    help="Upload short clips generated with a free/open-source video model. They are used in upload order and trimmed/looped to fit the soundtrack. No clip is generated by a paid API."
)
format_name = st.selectbox("5. Video format", list(FORMATS.keys()))
scene_count = st.slider("6. Number of scenes", 3, 12, 6)
stock_percent = st.slider("7. Stock footage preference (%)", 0, 100, 70, 10,
    help="Higher values try more free licensed footage. If suitable footage cannot be found, the app creates animated scenes instead.")
st.caption("No captions, lyrics, subtitles, or text overlays are added. Uploaded AI clips take priority, in upload order. Remaining scenes can use licensed stock footage or animated visuals. Scene durations are matched to the MP3.")

if st.button("Generate MP4", type="primary", disabled=not (topic.strip() and song)):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        st.error("FFmpeg/FFprobe is not installed. Check packages.txt in your Streamlit repository and redeploy.")
    elif len(song.getvalue()) > 100 * 1024 * 1024:
        st.error("The MP3 is larger than 100 MB. Please upload a smaller file.")
    else:
        size = FORMATS[format_name]
        progress = st.progress(0, text="Preparing your soundtrack and story…")
        credits: list[dict] = []
        used_ids: set[str] = set()
        try:
            with tempfile.TemporaryDirectory(prefix="free_music_video_") as temp:
                work = Path(temp)
                audio = work / "song.mp3"
                audio.write_bytes(song.getvalue())
                duration = probe_duration(audio)
                scenes = make_scenes(topic.strip(), story, scene_count)
                # Split total song duration evenly, ensuring no clip has a zero duration.
                scene_seconds = duration / scene_count
                clips: list[Path] = []
                preferred_stock_count = round(scene_count * stock_percent / 100)
                stock_slots = set()
                if preferred_stock_count:
                    stock_slots = {
                        round(i * (scene_count - 1) / max(1, preferred_stock_count - 1))
                        if preferred_stock_count > 1 else scene_count // 2
                        for i in range(preferred_stock_count)
                    }
                uploaded_clip_paths: list[Path] = []
                for upload_index, upload in enumerate(ai_clips or []):
                    clip_path = work / f"uploaded_ai_{upload_index:03d}{Path(upload.name).suffix.lower()}"
                    clip_path.write_bytes(upload.getvalue())
                    uploaded_clip_paths.append(clip_path)
                for index, scene in enumerate(scenes):
                    status = st.empty()
                    raw = work / f"raw_{index:03d}.mp4"
                    normalized = work / f"scene_{index:03d}.mp4"
                    source_title = ""
                    source_url = ""
                    use_stock = index in stock_slots
                    found = None
                    if uploaded_clip_paths:
                        status.info(f"Scene {index + 1}/{scene_count}: preparing uploaded AI actor clip…")
                        ai_source = uploaded_clip_paths[index % len(uploaded_clip_paths)]
                        normalize_stock(ai_source, normalized, size, scene_seconds)
                        source_type = "User-uploaded AI-generated scene clip"
                        source_title = ai_source.name
                    else:
                        status.info(f"Scene {index + 1}/{scene_count}: " +
                                    ("Searching for free licensed footage…" if use_stock else "Creating animated scene…"))
                        if use_stock:
                            found = get_stock_clip(scene, topic.strip(), raw, used_ids)
                        if found:
                            source_title, source_url = found
                            normalize_stock(raw, normalized, size, scene_seconds)
                            source_type = "Internet Archive stock footage (check source license)"
                        else:
                            make_animated_scene(normalized, size, scene_seconds, index + len(topic))
                            source_type = "Locally generated animated visual"
                            if use_stock:
                                source_title = "No suitable explicitly licensed stock clip found; animated scene used instead."
                    clips.append(normalized)
                    credits.append({"scene": index + 1, "description": scene,
                                    "type": source_type, "title": source_title, "url": source_url})
                    progress.progress(0.06 + 0.78 * (index + 1) / scene_count,
                                      text=f"Prepared scene {index + 1} of {scene_count}")
                    status.empty()
                output = work / "music_video.mp4"
                build_mp4(clips, audio, output, work, size, duration, progress)
                video_bytes = output.read_bytes()
            progress.progress(1.0, text="MP4 ready.")
            st.success("Your MP4 is ready with the original MP3 soundtrack.")
            st.video(video_bytes)
            st.download_button("Download MP4", data=video_bytes, file_name="music_video.mp4",
                               mime="video/mp4", type="primary")
            with st.expander("Scene details and footage credits"):
                for item in credits:
                    st.markdown(f"**Scene {item['scene']} — {item['type']}**")
                    st.write(item["description"])
                    if item["title"]:
                        st.caption(item["title"])
                    if item["url"]:
                        st.markdown(f"[View source and license]({item['url']})")
                st.markdown("Review each stock clip's source page and license before publishing or monetizing your finished video.")
        except Exception as exc:
            st.error("Video creation failed.")
            st.code(str(exc))
            st.caption("Try a shorter MP3 or fewer scenes. The app does not use paid services; processing capacity depends on the available Streamlit server resources.")

st.caption("Uses FFmpeg and the public Internet Archive metadata/search service. Stock footage is optional and item-specific licensing must be verified before reuse.")
