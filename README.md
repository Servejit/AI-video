# Free AI Music Video Studio — MP3 to MP4

Upload an MP3, describe a story, and click **Generate MP4**. The app automatically attempts to generate short, photorealistic video scenes through a public open-model Hugging Face Space, then combines the scenes with your original soundtrack.

## Features

- MP3 upload and MP4 download.
- Automatic best-effort AI scene generation using the public Lightricks/LTX-2 Hugging Face Space.
- Cinematic prompts with a consistent fictional lead character across scenes.
- Optional upload of your own AI-generated clips (not required).
- YouTube landscape (16:9), vertical (9:16), and square (1:1) outputs.
- If the free AI Space is unavailable or its quota/queue blocks a request, the app falls back to explicitly licensed Internet Archive footage when available, then locally generated animated scenes.
- Preserves the original MP3 soundtrack and matches the final video length to the audio.
- No captions, subtitles, lyrics, or text overlays.
- No paid APIs, no API charges, and no Google Colab.

## Important free-GPU limitation

This app calls a public third-party Hugging Face Space to access an open video model. **Automatic AI generation is best-effort, not guaranteed.** Public GPU Spaces may be busy, paused, quota-limited, slow, or change their API. This app cannot guarantee unlimited free AI generation or photorealistic output on every request. When the AI provider fails, the app reports the fallback in the scene credits and still attempts to build an MP4 using licensed stock footage and/or animated scenes.

For best results on a free GPU quota, start with 2–3 scenes. The generated shots are short and may repeat to cover a longer song. No paid service is called by this app.

### Optional Hugging Face token

The app can try unauthenticated access. If you have a free Hugging Face account and want to authenticate to the public Space, you may create a read token at https://huggingface.co/settings/tokens and add it to Streamlit Community Cloud as a secret named `HF_TOKEN`. This is optional; it does not unlock unlimited GPU time. Never paste tokens into source code or commit them to GitHub.

## Deploy on Streamlit Community Cloud

1. Open https://share.streamlit.io/
2. Select repository `Servejit/AI-video`.
3. Set the app file to `app.py`.
4. Deploy/reboot so the updated requirements are installed.
5. Upload an MP3, enter the topic/story, choose a format and number of scenes, then click **Generate MP4**.

The repository includes `packages.txt` for FFmpeg. The Python dependencies are in `requirements.txt`.

## Footage licensing

If AI generation is unavailable, the app tries to use only Internet Archive items whose metadata identifies a recognized Creative Commons or public-domain license. Metadata can be incomplete or inaccurate; open the source/rights page shown in the credits and verify the specific license and any attribution requirements before publishing or monetizing. If no suitable item is found, the app creates animated visuals locally.

## Run locally

Install Python 3.10+ and FFmpeg/FFprobe, then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1`.

## Links

- App source: https://github.com/Servejit/AI-video/blob/main/app.py
- Streamlit app: https://music-ai-video.streamlit.app
- LTX-2 public model Space: https://huggingface.co/spaces/Lightricks/ltx-2
