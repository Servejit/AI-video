# AI Music Video Studio

A Streamlit app for planning a music video from an MP3: audio analysis, optional lyric transcription, a timed storyboard, cinematic scene prompts, an optional ComfyUI API workflow connector, and an FFmpeg fallback that combines user-provided still images with the original song.

Upstream workflow reference: https://github.com/GeekatplayStudio/ComfyUI-Music-to-Video

This repository is an integration/frontend and does not bundle the upstream custom nodes, workflow, or model weights. It is not affiliated with Geekatplay Studio.

## Features

- Upload MP3, WAV, M4A, FLAC, or OGG.
- Analyze song duration and (when librosa works) estimate tempo, beat times, and average energy.
- Paste lyrics or optionally install Whisper for automatic transcription.
- Generate a scene timeline and editable cinematic prompts.
- Download storyboard JSON and scene-prompt text.
- Optionally submit an API-format workflow to a running ComfyUI server.
- Configure a server-side song filename for workflows that load audio from the ComfyUI input folder.
- Check a ComfyUI job and download output videos/images.
- Create an MP4 from uploaded still images with the original song as audio.
- Choose 16:9, 9:16, or 1:1 for the still-image MP4.
- Export a generated MP4 for Instagram Reels/Stories/feed, Facebook Reels/feed, WhatsApp Status, YouTube/Shorts, TikTok, X, LinkedIn, Pinterest Idea Pins, and a universal 720p preset.
- Platform exports normalize dimensions/aspect ratio, frame rate, H.264 video, AAC audio, and fast-start MP4 metadata.
- Choose crop-to-fill, blurred background, or black bars; optionally replace generated audio with the original uploaded song.

## Important limitations

1. AI rendering requires a compatible ComfyUI installation, GPU, model weights, custom nodes, and an API-format workflow.
2. The generic ComfyUI connector cannot infer every custom workflow. Node IDs and input names vary. Set the correct audio-loader node and prompt node in the app. Your workflow must accept the supplied inputs and produce an output node that ComfyUI records in history.
3. The current connector queues one workflow job using Scene 1's prompt. It does not automatically render every storyboard scene as a separate clip or stitch ComfyUI clips into a full song-length video. That requires a workflow-specific adapter for the exact ComfyUI workflow and nodes you use.
4. The still-image fallback is not AI video generation: it sequences still images and adds the song.
5. Platform presets are practical export targets, not a guarantee of acceptance; platform upload specifications and limits can change. Check the destination app before publishing. Cropping can remove content near the edges, so preview the exported file.
6. Streamlit Community Cloud has limited memory, disk, execution time, and no guaranteed GPU. Long video rendering is better hosted on a GPU machine. Do not expose an unauthenticated ComfyUI server to the public internet.
7. Working files are stored in the local music_video_output directory. Hosted environments may delete these files when restarted or redeployed. Download outputs you need to keep.
8. For best lyric matching, paste verified lyrics manually. Speech recognition may mishear sung vocals, names, accents, or non-English lyrics.

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub.
2. Open https://share.streamlit.io/
3. Create an app from Servejit/AI-video, branch main, entry point app.py.
4. Deploy. Streamlit installs requirements.txt and system packages from packages.txt.
5. Upload your song and generate the storyboard.

The FFmpeg package is requested through packages.txt. If FFprobe/FFmpeg is unavailable in your environment, the app will explain the missing dependency.

## Run locally

Install Python 3.10 or 3.11, then install FFmpeg (including FFprobe).

Ubuntu / Debian:
    
    sudo apt-get update
    sudo apt-get install -y ffmpeg

macOS:

    brew install ffmpeg

Windows: install FFmpeg and ensure both ffmpeg and ffprobe are on PATH.

Then run:

    python -m venv .venv
    # macOS/Linux:
    source .venv/bin/activate
    # Windows PowerShell:
    # .venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    streamlit run app.py

## Optional automatic lyric transcription

Whisper uses additional dependencies and can consume substantial memory. Install it separately if your environment can support it:

    pip install openai-whisper

FFmpeg must also be installed. In resource-limited hosting, use the manual lyrics field instead.

## Configure ComfyUI

1. Run ComfyUI on a trusted machine or private GPU service.
2. Install the custom nodes and model requirements for the workflow you intend to use.
3. Load and configure the workflow in ComfyUI.
4. Export/download the workflow in API format (not the ordinary UI workflow JSON).
5. In this app, enter the reachable ComfyUI base URL and upload the API JSON.
6. For audio-conditioned workflows, copy the uploaded song into the ComfyUI input folder using your normal ComfyUI file-transfer method, then enter its server-side filename, audio node ID, and exact input field. ComfyUI does not have a universal audio-upload endpoint that works for every custom node.
7. If using a text prompt node, enter its node ID and input field (commonly text, but this depends on the node). The app inserts the first scene prompt.
8. Queue the workflow, then use the status/download control to retrieve recorded outputs.

Security: Do not put API keys or private service tokens in the repository. Do not expose ComfyUI publicly without authentication and network restrictions. Use HTTPS and a private connection where possible.

## How to build full song-length AI video

To automate all scenes, the next integration must be tailored to the exact API workflow. It should map each scene prompt into the correct nodes, render each clip, poll every job, download each result, normalize clip dimensions/frame rate/audio, concatenate clips in timeline order, and mux the original song with FFmpeg. Some audio-driven workflows perform their own beat analysis, storyboarding, or stitching, so avoid duplicating those steps. Exact node IDs, input schemas, model versions, and output types must be taken from the workflow you run.

## Upstream project and licensing

This app links to and is inspired by workflow concepts in ComfyUI-Music-to-Video. No upstream source files are copied into this repository by this integration. Check the upstream repository's current LICENSE and preserve its copyright/license notice if you copy, modify, or redistribute any upstream code or custom nodes. Follow the licenses of every model and custom node you install.

## Troubleshooting

- No duration / MP4 creation fails: install FFmpeg and FFprobe.
- Whisper unavailable: paste lyrics manually or install openai-whisper.
- ComfyUI connection error: confirm the server URL is reachable from the Streamlit host. A ComfyUI server running only on your own localhost is not reachable from Streamlit Cloud.
- No outputs listed: inspect the ComfyUI job and workflow output nodes; the workflow must expose media outputs in its history.
- Out of memory / timeout: use fewer scenes, a smaller transcription model, or a GPU host with adequate VRAM and disk space.
