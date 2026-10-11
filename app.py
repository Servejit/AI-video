"""Free MP3-to-MP4 music video maker with best-effort public open-model video generation.
Uses a public Hugging Face LTX-2 Space for short AI-generated scenes when available,
then falls back to licensed footage and local FFmpeg animation. No paid APIs or Colab.
"""
from __future__ import annotations
import math, os, re, shutil, subprocess, tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import streamlit as st
from gradio_client import Client
import yt_dlp

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

def make_scenes(topic: str, story: str, lyrics: str, count: int) -> list[str]:
    """Build ordered scene beats from pasted lyrics first, then story guidance."""
    source = (lyrics or "").strip() or (story or "").strip()
    lines = re.split(r"\n+|(?<=[.!?।])\s+", source)
    lines = [re.sub(r"\s+", " ", s).strip(" -•\t") for s in lines if s.strip()]
    if not lines:
        lines = [
            "The lead character faces a difficult starting point and feels determined to change.",
            "Show a concrete setback that tests the lead character's resolve.",
            "The lead character takes action, works hard, and refuses to give up.",
            "Show visible progress and a meaningful moment of support or connection.",
            "The lead character reaches a hopeful resolution through their own effort.",
        ]
    # Divide the lyric/story beats into ordered groups so each scene covers its
    # own part of the song instead of repeating one unrelated generic prompt.
    groups = []
    for i in range(count):
        start = round(i * len(lines) / count)
        end = round((i + 1) * len(lines) / count)
        groups.append(" | ".join(lines[start:end]) if end > start else lines[min(start, len(lines)-1)])
    return [f"Song/story beat: {groups[i]}. Create a literal, visible action that communicates this meaning. Video scene {i+1} of {count}; progress the same story forward." for i in range(count)]

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
    # Always include human-action searches so an AI outage does not mean we only
    # search for scenery implied by the story text. These remain subject to the
    # explicit-license allowlist below.
    queries = [" ".join(words[:4]), " ".join(words[:2]), topic.strip(),
               "people walking", "man and woman talking", "people working", "people helping"]
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


def get_youtube_clip(scene: str, topic: str, destination: Path, used: set[str]) -> tuple[str, str] | None:
    """Search YouTube for scene-matched clips, using only clips marked Creative Commons."""
    words = keywords_for(scene, topic)
    queries = [" ".join(words[:4]), " ".join(words[:2]), topic.strip()]
    for query in dict.fromkeys(q for q in queries if q):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                                   "extract_flat": "in_playlist", "noplaylist": True,
                                   "socket_timeout": 15, "retries": 1}) as ydl:
                result = ydl.extract_info(f"ytsearch8:{query}", download=False)
            entries = (result or {}).get("entries") or []
        except Exception:
            continue
        for entry in entries:
            video_id = str((entry or {}).get("id") or "")
            if not video_id or video_id in used:
                continue
            video_url = str((entry or {}).get("url") or "")
            if not video_url.startswith("http"):
                video_url = f"https://www.youtube.com/watch?v={video_id}"
            try:
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                                       "noplaylist": True, "socket_timeout": 15, "retries": 1}) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                license_text = str((info or {}).get("license") or "").lower()
                if "creative commons" not in license_text and "creativecommons.org" not in license_text:
                    continue
                duration = float((info or {}).get("duration") or 0)
                if duration and duration < 3:
                    continue
                out_template = str(destination.with_suffix("")) + ".%(ext)s"
                opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                        "format": "best[ext=mp4][height<=1080]/best[height<=1080]/best",
                        "outtmpl": out_template, "max_filesize": 250_000_000,
                        "socket_timeout": 20, "retries": 1}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([video_url])
                downloaded = [p for p in destination.parent.glob(destination.stem + ".*")
                              if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
                if not downloaded:
                    continue
                source_file = max(downloaded, key=lambda p: p.stat().st_size)
                if source_file != destination:
                    shutil.move(str(source_file), str(destination))
                if not destination.exists() or destination.stat().st_size < 1000:
                    continue
                used.add(video_id)
                return str((info or {}).get("title") or video_id), video_url
            except Exception:
                continue
    return None

def make_ai_prompt(topic: str, scene: str, index: int, total: int,
                   performance_mode: str, action_intensity: str) -> str:
    """Create an action-led cinematic music-video prompt."""
    character_bible = (
        "Use the same fictional adult South Asian male lead in every shot: early 30s, short dark hair, "
        "subtle stubble, charcoal jacket and dark shirt. If needed, use the same adult South Asian female "
        "co-star with long dark hair and consistent wardrobe. Preserve faces, hair, clothing and relationships. "
        "Realistic anatomy, clear facial acting, believable weight and momentum."
    )
    modes = {
        "Cinematic story + dance": (
            "Blend literal story acting with dance performance. Act out the lyric as a real physical event; "
            "on refrains, confidence, struggle or victory, add visible full-body choreography, rhythmic footwork, "
            "arm movements, turns and expressive gestures. Never replace action with a static pose."
        ),
        "Cinematic story acting": (
            "Focus on dramatic narrative acting. Show the lead physically doing the lyric: exerting effort, "
            "overcoming an obstacle, stumbling, getting up, running, helping someone, reaching a goal or celebrating "
            "as appropriate to the exact words. Each shot has a clear action and outcome, not passive standing."
        ),
        "Dance and performance": (
            "Focus on energetic music-video dance. Show full-body adult performers with coordinated footwork, "
            "arm choreography, turns, torso movement, expressive gestures and confident facial performance. "
            "Keep the entire body and feet visible during dance; use an active tracking camera. No static posing."
        ),
    }
    intensity = {
        "Natural": "Use natural but clearly visible movement and acting.",
        "Strong": "Use strong readable body movement, decisive gestures, expressive reactions and active camera blocking.",
        "High-energy": "Use high-energy choreography, committed physical acting, dynamic tracking and dramatic reactions throughout.",
    }
    return (
        f"Premium photorealistic live-action feature-film music video, shot {index + 1} of {total}. "
        f"Theme: {topic}. EXACT LYRIC/STORY BEAT TO SHOW, in order: {scene}. {character_bible} "
        f"{modes[performance_mode]} {intensity[action_intensity]} "
        "ACTION RULE: the actor's face and body must visibly move from the start to the end of the shot. "
        "Act out the exact lyric literally; do not merely show a mood or a matching landscape. If the words describe "
        "struggle, show effort against a real obstacle; falling, show a setback; rising or refusing to stop, show "
        "getting up and continuing; support or love, show a real interaction; success, show a physical victory. "
        "These are examples only—follow the actual lyric. Cinematic shot design with readable medium/wide framing, "
        "motivated dolly-in or tracking/orbit movement, close-ups for real emotional reactions, practical dramatic "
        "lighting, realistic locations, depth of field, natural motion blur and polished film color grading. "
        "This must look like moving live-action footage, not a slideshow, portrait or landscape montage. "
        "No frozen posing, unrelated scenery, random smiling/walking, text, subtitles, logos, watermarks, collage or split screen. "
        "One continuous shot with believable physical motion."
    )

def generate_ai_clip(prompt: str, target: Path, size: tuple[int, int], seed: int) -> None:
    """Call a public open-model Hugging Face Space. Availability and free GPU quota are not guaranteed."""
    # The public LTX-2 Space exposes a Gradio endpoint. No paid API is used.
    # A free HF token can be added as Streamlit secret HF_TOKEN for account quota,
    # but the app also attempts unauthenticated access.
    token = os.environ.get("HF_TOKEN") or None
    client = Client("Lightricks/ltx-2", token=token, verbose=False)
    width, height = size
    # Keep generations small to fit shared/free GPU limits.
    width, height = (768, 512) if width >= height else (512, 768)
    result = client.predict(
        None, prompt, 3.0, True,
        "shaky, glitchy, low quality, deformed anatomy, distorted face, fused fingers, "
        "extra limbs, motion smear, static image, text, subtitles, watermark",
        int(seed % 2147483647), True, 8, 3.0, height, width,
        api_name="/generate_video",
    )
    video_value = result[0] if isinstance(result, (tuple, list)) else result
    if not video_value:
        raise RuntimeError("The free AI video Space returned no video file.")
    # gradio_client normally downloads returned video files to a local cache path.
    video_path = Path(str(video_value))
    if not video_path.is_file():
        raise RuntimeError(
            "The AI provider returned a video reference that was not downloaded locally. "
            "Try again later or use the hybrid fallback."
        )
    shutil.copy2(video_path, target)
    if target.stat().st_size < 1000:
        raise RuntimeError("The AI provider returned an empty or invalid clip.")

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
st.write("**MP3 in → MP4 out.** Build a lyric-led music video with cinematic acting, dance performance, or a combination, using your original song.")
st.info("Free-only best-effort generation: the app tries a public Hugging Face video model first. If that fails, it searches YouTube for clips matching each scene and only downloads clips whose metadata explicitly says Creative Commons, then tries explicitly licensed Internet Archive footage. YouTube metadata can be missing or inaccurate; verify each source's actual license and permissions before publishing or monetizing. No paid API is used.")

topic = st.text_input("1. Video topic", placeholder="e.g. Never give up, motivational song")
story = st.text_area("2. Story or scene guidance (optional)", placeholder="Describe the storyline, characters, setting, and ending.", height=100)
lyrics = st.text_area(
    "3. Paste the song lyrics (strongly recommended for matching scenes)",
    placeholder="Paste the lyrics in song order. Put each lyric line or verse on a new line. Punjabi, Hindi, or English are fine.",
    height=160,
    help="The app cannot reliably infer exact lyric meaning from an MP3 alone. Pasting lyrics lets it plan scenes around the words in order."
)
song = st.file_uploader("4. Upload your MP3", type=["mp3"])
ai_clips = st.file_uploader(
    "4. Optional: override automatic generation with your own AI clips",
    type=["mp4", "mov", "m4v", "webm"], accept_multiple_files=True,
    help="Optional only. If you do not upload clips, the app tries to generate photorealistic AI clips automatically using a public open-model Space. Uploaded clips take priority."
)
format_name = st.selectbox("5. Video format", list(FORMATS.keys()))
performance_mode = st.selectbox(
    "6. Performance style",
    ["Cinematic story + dance", "Cinematic story acting", "Dance and performance"],
    index=0,
    help="Choose story acting, full-body dance, or a combination."
)
action_intensity = st.selectbox(
    "7. Action intensity",
    ["Strong", "High-energy", "Natural"],
    index=0,
    help="Asks the model for more visible movement and expressive acting; results still depend on the free video model."
)
scene_count = st.slider("8. Number of scenes / AI generation attempts", 2, 8, 4)
stock_percent = st.slider("9. Stock footage preference (%)", 0, 100, 0, 10,
    help="Set to 0 to prioritize AI-generated actor scenes. Higher values try free licensed footage, but footage may not match your exact story.")
require_humans = st.checkbox("Prioritize real-looking male/female actors; stop rather than substitute abstract animation", value=True, help="Recommended for a movie-style result. If free AI generation and suitable footage both fail, the app will explain the failure instead of making an abstract video.")
st.caption("Paste lyrics in order for the best match. Prompts now emphasize visible acting, full-body dance, movement and cinematic camera work according to your chosen style. Free models may still miss actions or character continuity; this improves direction but cannot guarantee studio-film quality.")

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
                scenes = make_scenes(topic.strip(), story, lyrics, scene_count)
                # Make short music-video shots, then cycle the scene montage to cover the full song.
                # This avoids stretching one 3-second AI clip across a long section of audio.
                scene_seconds = min(4.0, duration / scene_count)
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
                        ai_error = ""
                        status.info(f"Scene {index + 1}/{scene_count}: trying public open-model AI video generation…")
                        try:
                            generate_ai_clip(
                                make_ai_prompt(topic.strip(), scene, index, scene_count, performance_mode, action_intensity),
                                raw, size, index + len(topic) * 17,
                            )
                            normalize_stock(raw, normalized, size, scene_seconds)
                            source_type = "AI-generated photorealistic scene (public LTX-2 Space)"
                            source_title = "Generated automatically; open-model public GPU availability is not guaranteed."
                        except Exception as exc:
                            ai_error = str(exc)[:500]
                            status.warning(
                                f"Free AI generation unavailable for scene {index + 1}; "
                                "trying the free footage/animation fallback."
                            )
                            # Stock preference controls preferred scenes, not recovery from an AI failure.
                            # In actor-first mode, always search for licensed live-action footage.
                            if use_stock or require_humans:
                                # Prefer scene-matched YouTube clips explicitly marked Creative Commons.
                                found = get_youtube_clip(scene, topic.strip(), raw, used_ids)
                                if not found:
                                    found = get_stock_clip(scene, topic.strip(), raw, used_ids)
                            if found:
                                source_title, source_url = found
                                normalize_stock(raw, normalized, size, scene_seconds)
                                source_type = (
                                    "YouTube clip marked Creative Commons (verify terms before publishing)"
                                    if "youtube.com/" in source_url or "youtu.be/" in source_url
                                    else "Internet Archive stock footage (check source license)"
                                )
                            else:
                                if require_humans:
                                    raise RuntimeError(
                                        f"Scene {index + 1} could not be generated with visible actors. "
                                        "The free AI video service may be unavailable or out of quota, and no suitable "
                                        "explicitly licensed human-actor clip was found. To keep movie quality, no abstract "
                                        "animation was substituted. Try again later, lower the scene count, increase stock "
                                        "footage preference, or upload your own AI-generated actor clips."
                                    )
                                make_animated_scene(normalized, size, scene_seconds, index + len(topic))
                                source_type = "Locally generated animated visual (fallback; not live-action)"
                                source_title = (
                                    "AI generation failed: " + ai_error
                                    if ai_error else
                                    "No suitable explicitly licensed stock clip found; animated scene used instead."
                                )
                    clips.append(normalized)
                    credits.append({"scene": index + 1, "description": scene,
                                    "type": source_type, "title": source_title, "url": source_url})
                    progress.progress(0.06 + 0.78 * (index + 1) / scene_count,
                                      text=f"Prepared scene {index + 1} of {scene_count}")
                    status.empty()
                if clips and scene_seconds > 0:
                    repeats = max(1, math.ceil(duration / (scene_seconds * len(clips))))
                    clips = clips * repeats
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

st.caption("Uses FFmpeg, yt-dlp to search YouTube for clips marked Creative Commons, the public Internet Archive metadata/search service, and best-effort access to the public Lightricks/LTX-2 Hugging Face Space. A Creative Commons label is not a substitute for checking actual rights and license terms. Respect platform terms and obtain permission where required.")


# ---------------------------------------------------------------------------
# Google Flow: one song-specific prompt + final soundtrack replacement
# This section is additive: the original automatic MP4 maker above remains.
# ---------------------------------------------------------------------------
st.divider()
st.header("🎞️ Google Flow — Full Video Prompt + Final Music Mix")
st.write(
    "Upload the song and create a language-aware prompt for a 30-second visual sequence with five unique scenes. "
    "If Google Flow cannot render 30 seconds in one generation, create the scenes separately and assemble them in order. "
    "The final MP4 uses your MP3 as its only audio track and matches the MP3 duration."
)

flow_song = st.file_uploader(
    "A. Upload the song for this Google Flow video (MP3)",
    type=["mp3"], key="flow_song_mp3"
)
flow_theme = st.text_input(
    "B. Song theme / visual direction (optional)",
    placeholder="e.g. Punjabi motivational song, persistence, setbacks, victory",
    key="flow_theme"
)
flow_lyrics = st.text_area(
    "C. Paste the lyrics (recommended for song-specific scenes)",
    placeholder="Paste lyrics in song order. Use line breaks between lyric lines or verses.",
    height=150,
    key="flow_lyrics",
    help=(
        "The app can measure the uploaded MP3's duration, but it cannot reliably know "
        "the exact words of a song from the audio file alone. Paste lyrics to make the "
        "scene sequence closely follow this particular song."
    )
)
flow_style = st.selectbox(
    "D. Visual style",
    [
        "Cinematic story with emotional acting",
        "Motivational cinematic story",
        "Punjabi celebration and dance",
        "Romantic cinematic story",
        "Inspirational documentary style",
        "Custom / follow the lyrics",
    ],
    key="flow_style"
)
flow_character = st.text_input(
    "E. Main character / continuity (optional)",
    placeholder="e.g. same fictional Punjabi man throughout; consistent face and clothes",
    key="flow_character"
)
st.caption("F. Google Flow clip: fixed at 30 seconds, with 5 unique scenes of approximately 6 seconds each.")

def make_single_flow_prompt(song_duration: float, theme: str, lyrics_text: str,
                            visual_style: str, character: str, target_scene_seconds: int) -> str:
    import math as _math
    import re as _re

    # Google Flow prompt is deliberately scoped to one 30-second clip.
    # Five six-second scenes provide a clear, unique visual arc without repeats.
    flow_clip_duration = 30.0
    total_scenes = 5
    scene_seconds = flow_clip_duration / total_scenes
    clean_lyrics = _re.sub(r"\s+", " ", (lyrics_text or "").strip())
    theme_text = (theme or "").strip() or "Let the song's lyrics and emotional progression define the theme"
    character_text = (character or "").strip() or (
        "Choose the lead character's identity, language, appearance, clothing, setting and social details "
        "to fit the language and cultural context of the supplied lyrics—not a random default. If lyrics are "
        "Punjabi (Gurmukhi or Roman Punjabi), use a believable Punjabi/South Asian adult lead and context; "
        "if Hindi, use a believable Indian/Hindi-speaking context; if Tamil, Telugu, Bengali, Marathi or another "
        "language, use a respectful, locally appropriate character and setting for that language. If lyrics are "
        "English, follow the user's theme and story rather than automatically assuming a Western character. "
        "Avoid caricatures and costumes that do not fit the lyrics. Keep the same fictional lead's face, age, "
        "skin tone, hairstyle and wardrobe consistent in every scene."
    )

    # Divide supplied lyrics into sequential, distinct story beats. If lyrics were
    # not supplied, direct Flow to create a coherent story from the song's musical
    # energy and the user's theme rather than pretending the lyrics were analyzed.
    if clean_lyrics:
        lyric_lines = [
            _re.sub(r"\s+", " ", line).strip(" -•")
            for line in _re.split(r"\n+|(?<=[.!?।])\s+", lyrics_text.strip())
            if line.strip()
        ]
        if not lyric_lines:
            lyric_lines = [clean_lyrics]
        beats = []
        for i in range(total_scenes):
            lyric_start = round(i * len(lyric_lines) / total_scenes)
            lyric_end = round((i + 1) * len(lyric_lines) / total_scenes)
            if lyric_end <= lyric_start:
                lyric_end = min(len(lyric_lines), lyric_start + 1)
            segment = " ".join(lyric_lines[lyric_start:lyric_end]).strip()
            if not segment:
                segment = lyric_lines[min(lyric_start, len(lyric_lines) - 1)]
            start_sec = flow_clip_duration * i / total_scenes
            end_sec = flow_clip_duration * (i + 1) / total_scenes
            start_stamp = f"{int(start_sec // 60):02d}:{start_sec % 60:05.2f}"
            end_stamp = f"{int(end_sec // 60):02d}:{end_sec % 60:05.2f}"
            beats.append(
                f"{i+1}. TIMECODE {start_stamp}–{end_stamp}: VISUALIZE THIS LYRIC BEAT: "
                f"“{segment}”. Show a concrete, literal action that expresses its meaning; "
                "advance the story and complete the transition by the end time."
            )
        beat_text = "\n".join(beats)
        lyric_instruction = (
            "Treat the supplied lyrics below as the story source and preserve their order. "
            "Interpret each beat visually, not with on-screen words. Do not invent a different song or change the lyric order."
        )
    else:
        beat_text = "\n".join(
            f"{i+1}. TIMECODE {int((flow_clip_duration * i / total_scenes) // 60):02d}:{(flow_clip_duration * i / total_scenes) % 60:05.2f}–"
            f"{int((flow_clip_duration * (i + 1) / total_scenes) // 60):02d}:{(flow_clip_duration * (i + 1) / total_scenes) % 60:05.2f}: "
            "Create a new story beat that advances the song's visual narrative, with a different action, "
            "location and composition from every other scene."
            for i in range(total_scenes)
        )
        lyric_instruction = (
            "No lyrics were supplied. Do not claim to know or quote the song's words. "
            "Build a coherent visual arc using the stated theme, the song's emotional energy, "
            "and a beginning, escalating challenge, turning point and satisfying ending."
        )

    return f"""Create a cohesive, lyric-led music video for the uploaded soundtrack.

IMPORTANT DURATION REALITY: The target finished Google Flow sequence is 30 seconds, from 00:00.00 to 00:30.00. Some Flow generation modes only produce a short clip per generation, so do not assume that one prompt guarantees a 30-second file. Create the five timed sections below as five separate, sequential clips if the current Flow mode cannot render all 30 seconds at once. Aim for about 6 seconds per section; if Flow offers a shorter fixed duration, generate the longest supported clip for each section and use Flow's extend/scene-assembly tools to reach 30 seconds. Do not accept a 0.08-second result as a completed clip: regenerate it. Never slow a tiny clip down or freeze one frame to fake a 30-second video.

THEME / CREATIVE DIRECTION: {theme_text}
VISUAL STYLE: {visual_style}
LANGUAGE AND CULTURAL MATCH: Identify the language from the supplied lyrics when possible. The lead character, dialogue-free performance, setting, clothing, gestures, architecture and cultural details must feel natural for that language and the lyric's meaning. For Punjabi lyrics, use a believable Punjabi/South Asian character and context; for Hindi lyrics, a believable Indian context; for other Indian languages, choose a respectful, locally appropriate context. For English lyrics, follow the theme and lyrics rather than assuming a particular ethnicity. Do not use stereotypes or mismatched cultural cues. The video is silent, so communicate through believable physical acting and expressions, not spoken words.
CHARACTER CONTINUITY: {character_text}

LYRIC AND MUSIC RULES:
{lyric_instruction}
Use visual pacing that responds to the song's emotional rise and fall. Let major musical changes motivate scene transitions and action intensity. Keep the visuals emotionally appropriate to the song. The MP3 will be added separately during final editing, so generate the video SILENT: no speech, no dialogue, no singing, no voice-over, no crowd voices, no sound effects, and no generated music.

ORDERED SCENE PLAN — every scene must be unique:
{beat_text}

NON-REPETITION RULES:
Every scene must introduce a new meaningful action and a visibly different setting, camera composition or story development. Never repeat, replay, loop, mirror, duplicate or recycle footage. Do not show the same action twice from another angle just to fill time. Avoid repetitive montages. Each scene must move the story forward; use fresh locations, props, actions, framing and camera movement. Do not create a slideshow.

CINEMATIC QUALITY:
Photorealistic, polished live-action cinema, expressive believable acting, natural body mechanics, meaningful movement from the beginning to the end of each shot, clear visual storytelling, motivated camera movement, cinematic lighting, realistic environments, consistent characters and wardrobe where applicable, smooth transitions, coherent geography and a purposeful final shot. Match shot scale and energy to the moment in the song. Do not leave the lead frozen in a pose; show actions with a clear start, development and finish.

STRICT AUDIO AND TEXT:
Silent video only. Absolutely no audible voice, dialogue, singing, soundtrack, music or sound effects. No subtitles, lyrics on screen, captions, titles, logos or watermarks. Do not generate text to represent the lyrics.

OUTPUT:
Target a complete 30-second silent sequence. If Flow cannot render 30 seconds in one generation, create five separate clips in the listed order, one for each timed scene, and assemble them sequentially. Keep the same lead character, face, age, hairstyle, clothing and visual style across every clip. Never repeat or recycle scenes. Check the actual duration of each result; if Flow returns only 0.08 seconds, regenerate it instead of treating it as complete. The final editor will use the original uploaded MP3 as the only soundtrack and align the finished export to the MP3 duration."""

flow_generate = st.button(
    "Generate my single Google Flow prompt",
    type="primary",
    key="flow_generate_prompt",
    disabled=not bool(flow_song)
)
if flow_generate:
    if len(flow_song.getvalue()) > 100 * 1024 * 1024:
        st.error("The MP3 is larger than 100 MB. Please upload a smaller file.")
    else:
        flow_temp_audio = None
        try:
            with tempfile.TemporaryDirectory(prefix="flow_prompt_") as flow_tmp:
                flow_temp_audio = Path(flow_tmp) / "flow_song.mp3"
                flow_temp_audio.write_bytes(flow_song.getvalue())
                flow_duration = probe_duration(flow_temp_audio)
            flow_prompt = make_single_flow_prompt(
                flow_duration, flow_theme, flow_lyrics, flow_style,
                flow_character, 6
            )
            st.session_state["generated_flow_master_prompt"] = flow_prompt
            st.session_state["generated_flow_song_name"] = flow_song.name
            st.session_state["generated_flow_song_bytes"] = flow_song.getvalue()
            st.session_state["generated_flow_song_duration"] = flow_duration
            st.success(f"Prompt generated for “{flow_song.name}” ({flow_duration:.1f} seconds).")
        except Exception as exc:
            st.error(f"Could not analyze the MP3: {exc}")

if st.session_state.get("generated_flow_master_prompt"):
    st.subheader("Your complete Google Flow prompt")
    st.caption(
        "Copy the entire prompt into Google Flow. For the closest match to your song, "
        "paste its lyrics above before generating the prompt."
    )
    st.text_area(
        "Single master prompt — copy all text",
        value=st.session_state["generated_flow_master_prompt"],
        height=460,
        key="flow_master_prompt_display"
    )
    st.download_button(
        "Download prompt as TXT",
        data=st.session_state["generated_flow_master_prompt"].encode("utf-8"),
        file_name="google_flow_full_video_prompt.txt",
        mime="text/plain",
        key="download_flow_master_prompt"
    )
    st.warning(
        "Important: the prompt requests a 30-second sequence, but Google Flow may limit each generation to a shorter clip. "
        "If it returns only a few seconds—or 0.08 seconds—regenerate that clip or create the five scenes separately, "
        "then assemble them in order. The app cannot force Flow's generator to render longer than its current limit."
    )

st.divider()
st.subheader("🎧 Finalize your Google Flow video with the original MP3")
st.write(
    "After Google Flow finishes, upload its generated video here. The app strips the video's "
    "audio and replaces it with the uploaded MP3, so any generated voice, dialogue or music is removed."
)
final_song = st.file_uploader(
    "G. Original song MP3 for the final soundtrack",
    type=["mp3"], key="final_song_mp3"
)
final_video = st.file_uploader(
    "H. Upload the Google Flow generated video",
    type=["mp4", "mov", "m4v", "webm"], key="final_flow_video"
)
if st.button(
    "Create final video — remove Flow audio and use MP3",
    type="primary",
    key="flow_finalize_video",
    disabled=not (final_song and final_video)
):
    if len(final_song.getvalue()) > 100 * 1024 * 1024:
        st.error("The MP3 is larger than 100 MB. Please upload a smaller file.")
    elif len(final_video.getvalue()) > 500 * 1024 * 1024:
        st.error("The video is larger than 500 MB. Please upload a smaller video.")
    elif not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        st.error("FFmpeg/FFprobe is not installed. Check packages.txt in your Streamlit repository and redeploy.")
    else:
        final_progress = st.progress(0, text="Preparing the uploaded video and song…")
        try:
            with tempfile.TemporaryDirectory(prefix="flow_final_video_") as final_tmp:
                final_work = Path(final_tmp)
                song_path = final_work / "original_song.mp3"
                video_suffix = Path(final_video.name).suffix.lower()
                if video_suffix not in (".mp4", ".mov", ".m4v", ".webm"):
                    video_suffix = ".mp4"
                input_video_path = final_work / ("flow_input" + video_suffix)
                final_output_path = final_work / "final_music_video.mp4"
                song_path.write_bytes(final_song.getvalue())
                input_video_path.write_bytes(final_video.getvalue())
                song_duration = probe_duration(song_path)
                input_video_duration = probe_duration(input_video_path)
                if input_video_duration < 1.0:
                    raise RuntimeError(
                        f"The uploaded video is only {input_video_duration:.3f} seconds long. "
                        "This is not a valid 30-second Flow clip. Regenerate it in Google Flow, "
                        "or generate the five scenes separately and combine them before uploading. "
                        "The app will not pretend a 0.08-second result is a complete video."
                    )
                final_progress.progress(0.25, text="Replacing all original video audio…")
                # Normalize the video first, then loop actual moving footage only when
                # it is shorter than the song. The original Flow audio is never mapped.
                run_command([
                    "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(input_video_path),
                    "-i", str(song_path),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-t", f"{song_duration:.6f}",
                    "-vf", "setpts=PTS-STARTPTS,fps=30,setsar=1,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                    "-c:a", "aac", "-b:a", "192k",
                    "-af", "aresample=async=1",
                    "-movflags", "+faststart",
                    str(final_output_path)
                ])
                final_duration = probe_duration(final_output_path)
                if abs(final_duration - song_duration) > 1.0:
                    raise RuntimeError(
                        f"Export duration check failed: MP3 is {song_duration:.2f}s but final MP4 is "
                        f"{final_duration:.2f}s. Please try a standard MP4 export from Google Flow."
                    )
                final_progress.progress(0.95, text="Checking the finished MP4…")
                if not final_output_path.exists() or final_output_path.stat().st_size < 1000:
                    raise RuntimeError("FFmpeg did not produce a valid output MP4.")
                final_bytes = final_output_path.read_bytes()
            final_progress.progress(1.0, text="Finished — original MP3 is the only audio track.")
            st.success("Final video ready. The uploaded Google Flow video's original audio has been discarded.")
            st.video(final_bytes)
            st.download_button(
                "Download final music video MP4",
                data=final_bytes,
                file_name="final_music_video.mp4",
                mime="video/mp4",
                type="primary",
                key="download_final_flow_video"
            )
        except Exception as exc:
            st.error("Could not finalize the video.")
            st.code(str(exc))
            st.caption(
                "Try an MP4 export from Google Flow or a shorter video/song if the server runs out of memory. "
                "The final export contains the uploaded MP3 audio only."
            )
