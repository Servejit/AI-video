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

def make_ai_prompt(topic: str, scene: str, index: int, total: int) -> str:
    """Turn a story beat into a short, photorealistic cinematic music-video shot."""
    continuity = (
        "Maintain character continuity across all shots. The lead is a photorealistic South Asian man "
        "in his early 30s, short dark hair, subtle stubble, expressive eyes, wearing a charcoal jacket "
        "and plain dark shirt. His co-star is a photorealistic South Asian woman in her late 20s, "
        "long dark hair, expressive face, wearing a tasteful contemporary outfit that fits the scene. "
        "Both are fictional adult actors. Show realistic skin texture, natural anatomy, believable "
        "eye contact, nuanced emotions, and physically plausible movement."
    )
    return (
        f"Photorealistic live-action feature-film scene, shot {index + 1} of {total}. "
        f"Story theme: {topic}. Exact beat from the song/story, in order: {scene}. {continuity} "
        "DIRECTOR'S RULE: illustrate the meaning of this exact beat with one clear, physically visible "
        "human action. Do not choose a random attractive shot or generic scenery. Translate the words "
        "into a literal mini-event with a beginning and an outcome: if the beat describes a struggle, "
        "show the lead encountering a real obstacle; if it describes falling, show a stumble or setback; "
        "if it describes rising or hope, show the lead standing up, taking a step, or continuing forward; "
        "if it describes love or support, show the characters looking at, speaking to, or helping each "
        "other. These are examples only—follow the actual beat, not an unrelated example. Keep the same "
        "lead character, wardrobe, relationships, and chronological story across shots. Each new shot must "
        "continue the previous story, not restart it. Human actors must be clearly visible and doing the "
        "described action. No unrelated landscape cutaways, stock-montage feeling, visual metaphors that "
        "ignore the lyric, or generic smiling/walking unless that is what the beat means. "
        "Photorealistic live-action feature-film footage, natural facial expressions and believable "
        "body mechanics, cinematic blocking, eye-level and over-the-shoulder coverage, subtle camera "
        "movement, realistic lens depth of field, practical lighting, filmic contrast. One continuous "
        "shot, no text, no subtitles, no logos, no watermarks, no collage, no split screen, no frozen people."
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
st.write("**MP3 in → MP4 out.** Automatically try to generate photorealistic AI video scenes using a public open-model GPU Space, then finish the music video with your original song.")
st.info("Free-only best-effort AI generation: the app automatically calls a public Hugging Face LTX-2 open-model Space for short clips. Public free GPU availability, queue times, and quotas are not guaranteed. If generation fails, actor-first mode searches for explicitly licensed live-action footage even when stock preference is set to 0; it will stop rather than substitute abstract animation. No paid API is used.")

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
scene_count = st.slider("6. Number of scenes / AI generation attempts", 2, 8, 3)
stock_percent = st.slider("7. Stock footage preference (%)", 0, 100, 0, 10,
    help="Set to 0 to prioritize AI-generated actor scenes. Higher values try free licensed footage, but footage may not match your exact story.")
require_humans = st.checkbox("Prioritize real-looking male/female actors; stop rather than substitute abstract animation", value=True, help="Recommended for a movie-style result. If free AI generation and suitable footage both fail, the app will explain the failure instead of making an abstract video.")
st.caption("For the closest lyric-to-action match, paste the lyrics above in the correct order. Without lyrics, the app only has your topic/story and cannot know what each sung line means. Free video models may still imperfectly follow prompts.")

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
                                make_ai_prompt(topic.strip(), scene, index, scene_count),
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
                                found = get_stock_clip(scene, topic.strip(), raw, used_ids)
                            if found:
                                source_title, source_url = found
                                normalize_stock(raw, normalized, size, scene_seconds)
                                source_type = "Internet Archive stock footage (check source license)"
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

st.caption("Uses FFmpeg, the public Internet Archive metadata/search service, and best-effort access to the public Lightricks/LTX-2 Hugging Face Space. Free GPU availability and quotas are controlled by the provider, not this app. Verify stock licenses before reuse.")
