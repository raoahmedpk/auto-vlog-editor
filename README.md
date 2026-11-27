# Auto Vlog Editor — Free Cloud Starter

A Streamlit app for short vlog clips. It removes long silent gaps, creates and burns subtitles using Whisper, applies basic audio filtering/loudness normalization, and optionally mixes user-provided background music.

## Deploy
1. Open https://share.streamlit.io/ and sign in with GitHub.
2. Choose **New app**.
3. Select repository `raoahmedpk/auto-vlog-editor`, branch `main`, file path `app.py`.
4. Deploy and wait for the app to build.
5. Open the Streamlit URL and upload a short test clip.

## Free-tier notes
- Start with clips around 1–3 minutes and under 200 MB.
- CPU/RAM are limited; long or high-resolution videos may fail or take a long time.
- The Whisper `tiny` model is used to reduce memory use.
- Uploaded footage and output files are temporary; this is not permanent cloud storage.
- Do not upload sensitive footage unless you are comfortable with the hosting provider's privacy terms.
- This is not full automatic cinematic editing: it does not yet choose B-roll, add story-aware cuts, or generate advanced effects.
