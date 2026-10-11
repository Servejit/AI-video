"""Cinematic Python-only music video maker.
Uses Pillow for original illustrated frames and FFmpeg for camera movement, cuts, and audio.
No Replicate, external AI API, or API key is required.
"""
from __future__ import annotations
import math, os, re, shutil, subprocess
from pathlib import Path
import streamlit as st
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path("music_video_output")
ROOT.mkdir(exist_ok=True)
FPS = 24
PRESETS = {
    "Cinematic YouTube (16:9)": (1280, 720),
    "Vertical Reels / Shorts / TikTok (9:16)": (720, 1280),
    "Square Social Video (1:1)": (900, 900),
}
MOODS = {
    "Golden-hour inspiration": [(8,20,48),(245,133,77),(255,205,129)],
    "Epic blue and amber": [(5,14,38),(31,78,116),(255,181,94)],
    "Dreamy romantic": [(33,24,63),(191,105,129),(255,207,183)],
    "Dark dramatic": [(5,8,20),(65,52,76),(240,167,103)],
    "Nature adventure": [(5,29,39),(49,111,93),(247,193,104)],
}
DEFAULT_CAPTIONS = [
    "Every great journey begins with a dream.", "When the road gets hard, keep moving.",
    "Every setback is a chance to rise.", "Let courage be louder than fear.",
    "Small steps can take you far.", "Your story is still being written.",
    "Keep going. Your moment will come.", "The horizon is waiting for you.",
]
st.set_page_config(page_title="Cinematic Video Studio", page_icon="🎬", layout="wide")
st.title("🎬 Cinematic Video Studio")
st.caption("Create an original illustrated music video with cinematic camera movement, atmosphere, captions and your own soundtrack.")
st.info("This version creates stylized cinematic illustrations and animates them with Python + FFmpeg. It does not generate photorealistic AI footage and does not use Replicate or paid APIs.")

def command(args: list[str], timeout: int = 1800) -> tuple[bool, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode == 0, (p.stdout or "") if p.returncode == 0 else (p.stderr or p.stdout or "Command failed.")
    except FileNotFoundError:
        return False, f"Required program not found: {args[0]}. Ensure FFmpeg is installed."
    except subprocess.TimeoutExpired:
        return False, "Video rendering timed out. Try a shorter song or fewer scenes."

def duration_of(path: Path) -> float:
    ok, out = command(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)], 60)
    if not ok:
        raise RuntimeError("Could not read this audio file. Check that FFmpeg is installed and try MP3 or WAV.")
    try:
        seconds = float(out.strip())
    except ValueError as exc:
        raise RuntimeError("Audio duration could not be read.") from exc
    if seconds <= 0:
        raise RuntimeError("The uploaded audio file is empty.")
    return seconds

def get_font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()

def wrap(draw, text: str, font, max_width: int) -> list[str]:
    result, current = [], ""
    for word in text.split():
        test = (current + " " + word).strip()
        if draw.textbbox((0,0),test,font=font)[2] <= max_width or not current: current = test
        else: result.append(current); current = word
    if current: result.append(current)
    return result

def lerp(a, b, t):
    return tuple(round(a[i]*(1-t)+b[i]*t) for i in range(3))

def make_frame(w: int, h: int, caption: str, title: str, index: int, palette) -> Image.Image:
    top, mid, glow = palette
    im = Image.new("RGB",(w,h)); px = im.load()
    for y in range(h):
        col = lerp(top,mid,min(1,(y/max(1,h-1))*1.38))
        for x in range(w): px[x,y] = col
    layer = Image.new("RGBA",(w,h),(0,0,0,0)); d = ImageDraw.Draw(layer,"RGBA")
    sun_x = int(w*(.73 if index%2 else .28)); sun_y = int(h*(.32+.025*math.sin(index)))
    for r,a in [(int(min(w,h)*.25),12),(int(min(w,h)*.19),18),(int(min(w,h)*.13),25)]:
        d.ellipse((sun_x-r,sun_y-r,sun_x+r,sun_y+r),fill=(*glow,a))
    rr = int(min(w,h)*.055)
    d.ellipse((sun_x-rr,sun_y-rr,sun_x+rr,sun_y+rr),fill=(*glow,245))
    for n in range(85):
        sx=(n*193+index*79)%w; sy=(n*107+31)%max(1,int(h*.57))
        rad=1 if w<1000 else 2
        d.ellipse((sx,sy,sx+rad,sy+rad),fill=(255,239,213,125))
    d.polygon([(0,int(h*.63)),(int(w*.13),int(h*.48)),(int(w*.24),int(h*.58)),
      (int(w*.39),int(h*.39)),(int(w*.55),int(h*.62)),(int(w*.72),int(h*.44)),
      (int(w*.87),int(h*.59)),(w,int(h*.47)),(w,h),(0,h)],fill=(23,39,61,255))
    d.polygon([(0,int(h*.77)),(int(w*.19),int(h*.62)),(int(w*.36),int(h*.78)),
      (int(w*.59),int(h*.60)),(int(w*.76),int(h*.75)),(w,int(h*.61)),(w,h),(0,h)],fill=(10,23,40,255))
    d.polygon([(int(w*.46),int(h*.73)),(int(w*.54),int(h*.73)),(int(w*.85),h),(int(w*.12),h)],fill=(104,76,64,100))
    cx,cy=int(w*(.50+.025*math.sin(index*1.4))),int(h*.72); head=max(7,int(min(w,h)*.018))
    d.ellipse((cx-head,cy-head*4,cx+head,cy-head*2),fill=(4,9,19,255))
    d.polygon([(cx-int(head*1.25),cy-head*2),(cx+int(head*1.25),cy-head*2),
      (cx+int(head*1.8),cy+head*1.3),(cx-int(head*1.8),cy+head*1.3)],fill=(4,9,19,255))
    d.line((cx-int(head*.65),cy+head,cx-int(head*1.2),cy+head*4),fill=(4,9,19,255),width=max(4,head//2))
    d.line((cx+int(head*.65),cy+head,cx+int(head*1.2),cy+head*4),fill=(4,9,19,255),width=max(4,head//2))
    d.line((cx-int(head),cy-head*1.4,cx-int(head*2.4),cy+head*.2),fill=(4,9,19,255),width=max(4,head//2))
    d.line((cx+int(head),cy-head*1.4,cx+int(head*2.3),cy-head*.1),fill=(4,9,19,255),width=max(4,head//2))
    panel_y=int(h*(.79 if h>w else .76))
    d.rounded_rectangle((int(w*.055),panel_y,int(w*.945),int(h*.955)),radius=max(12,int(min(w,h)*.018)),
      fill=(3,8,19,202),outline=(*glow,145),width=max(1,int(min(w,h)*.002)))
    title_font=get_font(max(15,int(min(w,h)*.026)),True)
    cap_font=get_font(max(20,int(min(w,h)*(.042 if h>w else .046))),True)
    d.text((int(w*.085),panel_y+int(h*.025)),title[:72].upper(),font=title_font,fill=(*glow,255))
    lines=wrap(d,caption,cap_font,int(w*.80))[:(2 if h>w else 3)]
    line_h=int(cap_font.size*1.2) if hasattr(cap_font,"size") else 32
    yy=panel_y+int(h*.075)
    for line in lines:
        bbox=d.textbbox((0,0),line,font=cap_font)
        d.text(((w-(bbox[2]-bbox[0]))//2,yy),line,font=cap_font,fill=(255,255,250,255),
          stroke_width=max(1,int(min(w,h)*.0015)),stroke_fill=(0,0,0,150))
        yy += line_h
    im=Image.alpha_composite(im.convert("RGBA"),layer)
    mask=Image.new("L",(w,h),0); md=ImageDraw.Draw(mask)
    for i in range(16):
        inset=int(i*min(w,h)*.018)
        md.rectangle((inset,inset,w-inset-1,h-inset-1),outline=min(100,i*7),width=max(1,int(min(w,h)*.008)))
    dark=Image.new("RGBA",(w,h),(0,0,0,0))
    dark.putalpha(mask.filter(ImageFilter.GaussianBlur(max(8,int(min(w,h)*.035)))))
    return Image.alpha_composite(im,dark).convert("RGB")

def render_video(audio_path: Path, output_path: Path, w: int, h: int, title: str,
                 captions: list[str], scene_count: int, palette, progress):
    total_seconds=duration_of(audio_path)
    work=ROOT/("work_"+re.sub(r"[^a-zA-Z0-9]+","_",output_path.stem))
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    per_scene=total_seconds/scene_count; clips=[]
    for i in range(scene_count):
        caption=captions[i%len(captions)] if captions else DEFAULT_CAPTIONS[i%len(DEFAULT_CAPTIONS)]
        still=work/f"scene_{i:02d}.jpg"; clip=work/f"clip_{i:02d}.mp4"
        make_frame(w,h,caption,title,i+1,palette).save(still,quality=94,optimize=True)
        frames=max(1,round(per_scene*FPS))
        vf=(f"zoompan=z='min(1.0+0.00045*on,1.075)':x='iw/2-(iw/zoom/2)+2*sin(on/32)':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
            f"fade=t=in:st=0:d=0.45,fade=t=out:st={max(0,per_scene-.45):.3f}:d=0.45,format=yuv420p")
        ok,msg=command(["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-i",str(still),
          "-t",f"{per_scene:.4f}","-vf",vf,"-an","-c:v","libx264","-preset","ultrafast","-crf","21","-r",str(FPS),str(clip)],1800)
        if not ok: raise RuntimeError(f"Could not render scene {i+1}: {msg[-1200:]}")
        clips.append(clip); progress((i+1)/(scene_count+1),f"Rendering scene {i+1} of {scene_count}…")
    concat_file=work/"clips.txt"
    concat_file.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in clips),encoding="utf-8")
    silent=work/"silent.mp4"
    ok,msg=command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(concat_file),"-c","copy",str(silent)],1800)
    if not ok: raise RuntimeError(f"Could not join scenes: {msg[-1200:]}")
    progress(.96,"Mixing the original soundtrack…")
    ok,msg=command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(silent),"-i",str(audio_path),
      "-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","192k","-t",f"{total_seconds:.3f}",
      "-movflags","+faststart",str(output_path)],1800)
    if not ok: raise RuntimeError(f"Could not add soundtrack: {msg[-1200:]}")
    progress(1.0,"Finished rendering.")
    return output_path

with st.sidebar:
    st.header("🎞️ Film settings")
    preset_name=st.selectbox("Video format",list(PRESETS.keys()),index=0)
    mood_name=st.selectbox("Visual mood",list(MOODS.keys()),index=0)
    scene_count=st.slider("Number of scenes",3,12,6)
    title=st.text_input("Video title","A Journey Worth Taking")
    caption_mode=st.radio("On-screen text",["Use my lyrics / captions","Use motivational captions"],index=0)

audio=st.file_uploader("1. Upload your song / audio",type=["mp3","wav","m4a","aac","ogg","flac"])
captions_text=st.text_area("2. Paste lyrics or captions (one line or sentence per scene)",height=140,
 placeholder="Every road begins with a dream.\nI will rise again.\nNothing can stop me.")
st.caption("For best results, paste short caption lines. Automatic speech-to-text is not included.")
go=st.button("✨ Create Cinematic Video",type="primary",use_container_width=True)
if go:
    if audio is None:
        st.error("Please upload an audio file first."); st.stop()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        st.error("FFmpeg/FFprobe is unavailable. Keep ffmpeg in packages.txt and redeploy."); st.stop()
    if audio.size>100*1024*1024:
        st.error("Audio is larger than the 100 MB upload limit."); st.stop()
    if caption_mode=="Use my lyrics / captions":
        captions=[x.strip() for x in re.split(r"[\n\r]+",captions_text) if x.strip()]
        if not captions:
            st.error("Paste at least one lyric/caption line, or choose motivational captions."); st.stop()
    else: captions=DEFAULT_CAPTIONS
    w,h=PRESETS[preset_name]; palette=MOODS[mood_name]
    safe_title=re.sub(r"[^A-Za-z0-9_-]+","_",title).strip("_") or "cinematic_video"
    audio_path=ROOT/("uploaded_audio"+Path(audio.name).suffix.lower())
    output_path=ROOT/f"{safe_title}_{w}x{h}.mp4"
    audio_path.write_bytes(audio.getvalue())
    bar=st.progress(0); status=st.empty()
    try:
        def update_progress(value,message):
            bar.progress(max(0.0,min(1.0,float(value)))); status.write(message)
        with st.spinner("Creating your cinematic video. This may take a few minutes…"):
            render_video(audio_path,output_path,w,h,title.strip() or "Cinematic Journey",captions,scene_count,palette,update_progress)
        st.success("Your cinematic video is ready.")
        st.video(str(output_path))
        st.download_button("⬇️ Download MP4 video",data=output_path.read_bytes(),file_name=output_path.name,
                           mime="video/mp4",use_container_width=True)
        st.caption(f"Output: {w} × {h} · {scene_count} scenes · original audio retained")
    except Exception as exc:
        st.error(str(exc))
