# AI Music Video — MP3-only automatic generator

Upload one MP3, press **Generate complete AI music video**, and download the finished video plus platform-ready MP4 exports.

## Automatic workflow
1. Reads the MP3 duration.
2. Attempts vocal transcription using Replicate's Whisper model.
3. Creates cinematic scene prompts.
4. Generates moving AI clips using Replicate's Minimax Video-01 model (approximately 6 seconds per clip).
5. Joins the clips and adds the original MP3 soundtrack.
6. Creates exports for Instagram Reels/Stories/feed, Facebook Reels/feed, WhatsApp Status, YouTube/Shorts, TikTok, X, LinkedIn, Pinterest Idea Pins, and universal 720p landscape.
7. Provides a master MP4 and a ZIP containing all platform formats.

## Required one-time setup
1. Create a Replicate account and API token at https://replicate.com/account/api-tokens.
2. Deploy this repository from Streamlit Community Cloud.
3. Open the app settings, then Secrets, and add the following with your actual token:

   REPLICATE_API_TOKEN = "r8_your_real_token_here"

4. Save secrets and reboot/redeploy the app. Never commit the token to GitHub.
5. Upload an MP3 and press Generate.

## Costs and limitations
- **AI video generation is a paid service.** Review the current model pricing at https://replicate.com/minimax/video-01/api before generating.
- The model creates clips of approximately six seconds. A three-minute song can require around 30 video generations, and the provider charges for generations. Long songs can be expensive.
- Transcription uses the Replicate Whisper model. If transcription fails, the app falls back to a generic cinematic storyline, so scenes may not match lyrics as closely.
- Streamlit Community Cloud may time out on long generation jobs. A server with longer execution limits is recommended for long songs.
- Visual continuity and generated quality vary. Portrait exports crop landscape footage to fill the target frame; review files before posting.
- Hosted storage may be temporary. Download your files after generation.

## Deployment
Repository: Servejit/AI-video; branch: main; entry point: app.py. The requirements file installs Python packages and packages.txt installs FFmpeg/FFprobe.

## Local run
Install Python 3.10 or 3.11 and FFmpeg/FFprobe, then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export REPLICATE_API_TOKEN="r8_your_real_token_here"
streamlit run app.py
```

Windows PowerShell activation: `.venv\\Scripts\\Activate.ps1`. Set the token with `$env:REPLICATE_API_TOKEN="r8_your_real_token_here"`.

## API references
- Minimax Video-01: https://replicate.com/minimax/video-01/api
- Replicate API tokens: https://replicate.com/account/api-tokens
- Replicate HTTP API: https://replicate.com/docs/reference/http

Follow the terms of Replicate and the models you use. This app does not bundle model weights.
