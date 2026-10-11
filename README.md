# Free Music Video Studio — MP3 to MP4

Upload an MP3, enter a topic or story, choose an aspect ratio, and download an MP4 with the original soundtrack.

## Features

- MP3 upload and MP4 download.
- Story/topic-based scene planning.
- YouTube landscape (16:9), vertical (9:16), and square (1:1) outputs.
- Searches Internet Archive for footage whose metadata explicitly identifies a recognized Creative Commons or public-domain license.
- If no suitable licensed footage is found, generates animated visual scenes locally using FFmpeg.
- Preserves the original song in the final MP4 and matches video duration to audio duration.
- No captions, subtitles, lyrics, or text overlays.
- No Google Colab, paid APIs, AI service keys, or subscription required.

## Important limitation

This version is a free stock-footage and animated-scene video maker, **not a generative AI text-to-video model**. The story guides scene searches and scene sequencing, but free Streamlit hosting cannot guarantee access to a GPU for true AI video generation. The animated fallback is generated locally with FFmpeg. It may be necessary to use shorter MP3 files on the free Streamlit tier because server CPU, memory, and execution time are limited.

## Deploy on Streamlit Community Cloud

1. Open https://share.streamlit.io/
2. Select this repository: `Servejit/AI-video`.
3. Set the app file to `app.py`.
4. Deploy. No secrets or API keys are needed.
5. Upload an MP3, enter a topic, choose the video format and scene count, then click **Generate MP4**.

The repository includes `packages.txt` with FFmpeg for Streamlit's Linux environment.

## Footage licensing

The app tries to use only items whose Internet Archive metadata includes a recognized public-domain or Creative Commons license. Metadata can be incomplete or inaccurate; open the source/rights page shown in the credits and verify the specific license and any attribution requirements before publishing or monetizing a video. When a matching licensed video cannot be found, the app uses locally generated animated visuals instead.

## Run locally

Install Python 3.10+ and FFmpeg/FFprobe, then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1`.

## Repository

- App: https://github.com/Servejit/AI-video/blob/main/app.py
- Streamlit app: https://music-ai-video.streamlit.app
