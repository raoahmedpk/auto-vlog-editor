import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import streamlit as st
from faster_whisper import WhisperModel

st.set_page_config(page_title="Auto Vlog Editor", page_icon="🎬", layout="wide")
st.title("🎬 Auto Vlog Editor")
st.write("Upload a short vlog, remove long silent gaps, create subtitles, clean voice audio and export an MP4.")

def run_cmd(args):
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-3500:])
    return p

def duration(path):
    p = run_cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(p.stdout.strip())

def silence_segments(path, noise_db, min_silence):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True
    )
    log = p.stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    total = duration(path)
    silences = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else total
        if end > start:
            silences.append((max(0, start), min(total, end)))
    keep, cursor = [], 0.0
    for start, end in silences:
        left, right = max(cursor, start + 0.12), max(cursor, end - 0.12)
        if left > cursor:
            keep.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < total:
        keep.append((cursor, total))
    keep = [(a, b) for a, b in keep if b - a >= 0.15]
    return keep or [(0.0, total)], len(silences)

def cut_silence(source, target, segments):
    if len(segments) == 1 and segments[0][0] == 0:
        shutil.copy2(source, target)
        return
    filters = []
    for i, (a, b) in enumerate(segments):
        filters.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        filters.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    joined = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    filters.append(f"{joined}concat=n={len(segments)}:v=1:a=1[v][a]")
    run_cmd(["ffmpeg", "-y", "-i", str(source), "-filter_complex", ";".join(filters),
             "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "22", "-c:a", "aac", "-b:a", "160k", str(target)])

def make_srt(items, path):
    def stamp(t):
        ms = round(t * 1000)
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    with open(path, "w", encoding="utf-8") as f:
        for i, item in enumerate(items, 1):
            f.write(f"{i}\n{stamp(item.start)} --> {stamp(item.end)}\n{item.text.strip()}\n\n")

def render(source, output, srt, ratio, font_size, music_path, music_volume):
    if ratio == "Vertical 9:16 (Reels/TikTok)":
        scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    elif ratio == "Horizontal 16:9":
        scale = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
    else:
        scale = "scale=1920:-2"
    srt_path = str(srt.resolve()).replace("\\", "/").replace(":", r"\:")
    vf = scale + f",subtitles='{srt_path}':force_style='FontName=Arial,FontSize={font_size},Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=85'"
    audio = "highpass=f=75,lowpass=f=14000,loudnorm=I=-16:TP=-1.5:LRA=11"
    if music_path:
        af = f"[0:a]{audio}[voice];[1:a]volume={music_volume}[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2[out]"
        args = ["ffmpeg", "-y", "-i", str(source), "-stream_loop", "-1", "-i", str(music_path),
                "-vf", vf, "-filter_complex", af, "-map", "0:v:0", "-map", "[out]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac",
                "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(output)]
    else:
        args = ["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-af", audio,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac",
                "-b:a", "160k", "-movflags", "+faststart", str(output)]
    run_cmd(args)

with st.sidebar:
    st.header("Editing options")
    lang = st.selectbox("Spoken language", ["Auto detect", "English", "Urdu", "Hindi", "Arabic"])
    remove_silence = st.checkbox("Remove long silent pauses", True)
    noise = st.slider("Silence threshold (dB)", -50, -20, -35)
    pause = st.slider("Minimum silence to cut (seconds)", 0.3, 2.0, 0.7, 0.1)
    ratio = st.selectbox("Video format", ["Vertical 9:16 (Reels/TikTok)", "Keep original ratio", "Horizontal 16:9"])
    font_size = st.slider("Subtitle size", 18, 42, 30)
    music_volume = st.slider("Background music volume", 0.02, 0.35, 0.10, 0.01)

video = st.file_uploader("Choose your vlog video (MP4/MOV/MKV/AVI/WEBM)", type=["mp4", "mov", "mkv", "avi", "webm"])
music = st.file_uploader("Optional background music (MP3/WAV/M4A)", type=["mp3", "wav", "m4a"])
st.info("Free cloud hosting has limited CPU/RAM. Start with a 1–3 minute clip under 200 MB. Uploaded videos are temporary and are not permanent storage.")

if video and video.size > 200 * 1024 * 1024:
    st.error("Video exceeds the 200 MB upload limit. Please compress it or choose a shorter clip.")
elif video and st.button("✨ Edit my vlog", type="primary", use_container_width=True):
    work = Path(tempfile.mkdtemp(prefix="autovlog_"))
    try:
        source = work / ("input" + Path(video.name).suffix.lower())
        source.write_bytes(video.getbuffer())
        music_path = None
        if music:
            music_path = work / ("music" + Path(music.name).suffix.lower())
            music_path.write_bytes(music.getbuffer())
        bar = st.progress(5, text="Preparing your video…")
        prepared = work / "prepared.mp4"
        if remove_silence:
            segments, count = silence_segments(source, noise, pause)
            cut_silence(source, prepared, segments)
            st.write(f"Found {count} long quiet section(s).")
        else:
            shutil.copy2(source, prepared)
        bar.progress(35, text="Loading speech recognition model…")
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        language_code = {"English": "en", "Urdu": "ur", "Hindi": "hi", "Arabic": "ar"}.get(lang)
        segments, info = model.transcribe(str(prepared), beam_size=3, vad_filter=True, language=language_code)
        captions = list(segments)
        if not captions:
            raise RuntimeError("No speech detected. Check the audio or choose the correct language.")
        srt = work / "captions.srt"
        make_srt(captions, srt)
        bar.progress(65, text="Rendering captions and cleaning audio…")
        output = work / "edited_vlog.mp4"
        render(prepared, output, srt, ratio, font_size, music_path, music_volume)
        bar.progress(100, text="Your vlog is ready!")
        st.success("Editing complete.")
        st.video(str(output))
        st.download_button("⬇️ Download edited MP4", output.read_bytes(),
                           file_name="edited_vlog.mp4", mime="video/mp4", use_container_width=True)
        st.download_button("Download subtitles (.srt)", srt.read_bytes(),
                           file_name="captions.srt", mime="text/plain")
        with st.expander("View transcript"):
            st.write(" ".join(x.text.strip() for x in captions))
        st.caption("This starter version removes long silence, generates/burns subtitles, applies basic voice filtering and optionally adds music. It does not yet create intelligent B-roll or cinematic story edits.")
    except Exception as exc:
        st.error(f"Editing failed: {exc}")
        st.info("Try a shorter MP4 clip. Free hosting can run out of memory on large videos.")
    finally:
        shutil.rmtree(work, ignore_errors=True)
else:
    st.subheader("What the editor does")
    st.markdown("- Removes long silent gaps while retaining short pauses\n- Creates subtitles using Whisper speech recognition\n- Burns subtitles into the final video\n- Applies basic voice filtering and loudness balancing\n- Adds optional background music\n- Exports MP4 for Reels, YouTube or original format")
