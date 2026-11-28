import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Auto Vlog Editor", page_icon="🎬", layout="wide")
st.title("🎬 Auto Vlog Editor")
st.write("Upload your vlog, then press the green **Start editing** button. Processing runs on the cloud and may take several minutes.")

MAX_MB = 200

from types import SimpleNamespace
import textwrap
import unicodedata

@st.cache_resource
def romanizer():
    import uroman
    return uroman.Uroman()

def roman_urdu(text):
    common = {"میں":"main","آپ":"aap","اپ":"aap","ہم":"hum","یہ":"yeh","وہ":"woh","ہے":"hai","ہیں":"hain","ہوں":"hoon","نہیں":"nahi","کیا":"kya","کیوں":"kyun","کہ":"ke","کے":"ke","کی":"ki","کا":"ka","کو":"ko","سے":"se","اور":"aur","تو":"to","بھی":"bhi","اب":"ab","پھر":"phir","بہت":"bohat","ایک":"ek","دو":"do","آج":"aaj","کل":"kal","میرے":"mere","میرا":"mera","میری":"meri","کر":"kar","کرنا":"karna","رہا":"raha","رہی":"rahi","رہے":"rahe","گیا":"gaya","گئی":"gayi","چاہیے":"chahiye","ویڈیو":"video","ایڈیٹنگ":"editing","بھائی":"bhai","یار":"yaar","ٹھیک":"theek","لوگ":"log","لیکن":"lekin","اگر":"agar","اس":"is","ان":"un","اسے":"isse","ہو":"ho","گے":"ge","تھا":"tha","تھی":"thi","تھے":"the","بات":"baat","ساتھ":"saath","لیے":"liye","لئے":"liye","دیکھ":"dekh","بس":"bas","شروع":"shuru","کام":"kaam"}
    def convert(m):
        w=m.group()
        return common.get(w) or romanizer().romanize_string(w, lcode="urd")
    text=re.sub(r"[\u0621-\u06d3]+",convert,text)
    text=romanizer().romanize_string(text)
    text="".join(c for c in unicodedata.normalize("NFKD",text) if not unicodedata.combining(c))
    return re.sub(r"[^\x20-\x7e]", " ", text).strip()

def make_captions(segments, roman):
    out=[]
    for seg in segments:
        if seg.no_speech_prob > 0.65 and seg.avg_logprob < -0.8:
            continue
        if seg.avg_logprob < -1.25:
            continue
        words=seg.words or []
        if words:
            group=[]
            for word in words:
                t=roman_urdu(word.word.strip()) if roman else word.word.strip()
                if not t or word.start is None or word.end is None:
                    continue
                if group and (len(group)>=6 or len(" ".join(x[2] for x in group))+len(t)>38 or word.end-group[0][0]>3.4 or word.start-group[-1][1]>0.7):
                    out.append(SimpleNamespace(start=group[0][0],end=group[-1][1],text=" ".join(x[2] for x in group)))
                    group=[]
                group.append((word.start,word.end,t))
            if group:
                out.append(SimpleNamespace(start=group[0][0],end=group[-1][1],text=" ".join(x[2] for x in group)))
        else:
            t=roman_urdu(seg.text) if roman else seg.text.strip()
            if t:
                out.append(SimpleNamespace(start=seg.start,end=seg.end,text="\n".join(textwrap.wrap(t,38))))
    return out

def write_ass(items, path, vertical, size):
    w,h=(720,1280) if vertical else (1280,720)
    margin=215 if vertical else 65
    header=f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{size},&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,1,1.2,0.6,2,75,110,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def ts(t):
        cs=max(0,round(t*100))
        h,cs=divmod(cs,360000)
        m,cs=divmod(cs,6000)
        s,cs=divmod(cs,100)
        return f"{h}:{m:02}:{s:02}.{cs:02}"
    lines=[]
    for item in items:
        t=" ".join(item.text.split()).replace("{","(").replace("}",")").replace("\\","/")
        t=r"\N".join(textwrap.wrap(t,36))
        lines.append(f"Dialogue: 0,{ts(item.start)},{ts(max(item.end,item.start+0.2))},Default,,0,0,0,,{t}")
    path.write_text(header+"\n".join(lines)+"\n",encoding="utf-8")



def command(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError("FFmpeg/FFprobe is not installed on the cloud server. Please open Manage app → Logs and share the error.") from e
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "Unknown FFmpeg error")[-4500:])
    return p

def probe(path):
    p = command(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(p.stdout.strip())

def has_audio(path):
    p = command(["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", str(path)])
    return bool(p.stdout.strip())

def get_keep_segments(path, threshold, silence_seconds):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af",
         f"silencedetect=noise={threshold}dB:d={silence_seconds}", "-f", "null", "-"],
        capture_output=True, text=True
    )
    if p.returncode != 0:
        raise RuntimeError("FFmpeg could not read the uploaded video/audio: " + (p.stderr or "")[-2500:])
    total = probe(path)
    starts = [float(v) for v in re.findall(r"silence_start:\s*([0-9.]+)", p.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end:\s*([0-9.]+)", p.stderr)]
    silences = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else total
        if end > start:
            silences.append((max(0.0, start), min(total, end)))
    keep, cursor = [], 0.0
    for start, end in silences:
        left = max(cursor, start + 0.10)
        right = max(cursor, end - 0.10)
        if left > cursor:
            keep.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < total:
        keep.append((cursor, total))
    keep = [(a, b) for a, b in keep if b - a >= 0.15]
    return keep or [(0.0, total)], len(silences)

def cut_segments(source, target, segments, audio_present):
    if len(segments) == 1 and segments[0][0] <= 0.001:
        shutil.copy2(source, target)
        return
    filters = []
    for i, (a, b) in enumerate(segments):
        filters.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        if audio_present:
            filters.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    if audio_present:
        joined = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
        filters.append(f"{joined}concat=n={len(segments)}:v=1:a=1[v][a]")
        args = ["ffmpeg", "-y", "-i", str(source), "-filter_complex", ";".join(filters),
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast",
                "-crf", "23", "-c:a", "aac", "-b:a", "160k", str(target)]
    else:
        joined = "".join(f"[v{i}]" for i in range(len(segments)))
        filters.append(f"{joined}concat=n={len(segments)}:v=1:a=0[v]")
        args = ["ffmpeg", "-y", "-i", str(source), "-filter_complex", ";".join(filters),
                "-map", "[v]", "-an", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast",
                "-crf", "23", str(target)]
    command(args)

def timestamp(t):
    ms = round(t * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def write_srt(segments, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, item in enumerate(segments, 1):
            f.write(f"{i}\n{timestamp(item.start)} --> {timestamp(item.end)}\n{item.text.strip()}\n\n")

def render_video(source, output, srt, aspect, font_size, music_path, music_volume, audio_present):
    if aspect == "Vertical 9:16 (Reels)":
        scale = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
    elif aspect == "Horizontal 16:9":
        scale = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2"
    else:
        scale = "scale=w=min(1280\\,iw):h=-2"
    subtitle_path = str(srt.resolve()).replace("\\", "/").replace(":", r"\:")
    vf = scale + f",ass='{subtitle_path}'"
    clean_audio = "highpass=f=75,afftdn=nf=-28,acompressor=threshold=0.125:ratio=2:attack=15:release=150,lowpass=f=14000,loudnorm=I=-16:TP=-1.5:LRA=11"
    if music_path and audio_present:
        af = f"[0:a]{clean_audio}[voice];[1:a]volume={music_volume}[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2[out]"
        args = ["ffmpeg", "-y", "-i", str(source), "-stream_loop", "-1", "-i", str(music_path),
                "-vf", vf, "-filter_complex", af, "-map", "0:v:0", "-map", "[out]",
                "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
                "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(output)]
    elif audio_present:
        args = ["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-af", clean_audio,
                "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
                "-b:a", "160k", "-movflags", "+faststart", str(output)]
    else:
        args = ["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-an",
                "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
                "-movflags", "+faststart", str(output)]
    command(args)

@st.cache_resource(show_spinner=False)
def load_whisper(model_name):
    from faster_whisper import WhisperModel
    return WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=2, num_workers=1)

with st.sidebar:
    st.header("Editing settings")
    language = st.selectbox("Spoken language", ["Urdu", "Auto detect", "English", "Hindi", "Arabic"])
    caption_language = st.selectbox("Subtitle language", ["Roman Urdu (same spoken words)", "English translation"])
    model_name = st.selectbox("Recognition quality", ["base", "small"], index=1, help="Small is more accurate; base uses less cloud memory.")
    remove_silence = st.checkbox("Remove long silent pauses", True)
    threshold = st.slider("Silence threshold (dB)", -50, -20, -35)
    pause_length = st.slider("Minimum silence to cut (seconds)", 0.3, 2.0, 0.7, 0.1)
    aspect = st.selectbox("Output format", ["Vertical 9:16 (Reels)", "Keep original ratio", "Horizontal 16:9"])
    subtitle_size = st.slider("Subtitle size", 18, 30, 23)
    music_volume = st.slider("Background music volume", 0.02, 0.35, 0.10, 0.01)

video = st.file_uploader("1) Choose your vlog video", type=["mp4", "mov", "mkv", "avi", "webm"])
if video:
    st.success(f"Video selected: {video.name} ({video.size / (1024*1024):.1f} MB)")
    if video.size > MAX_MB * 1024 * 1024:
        st.error(f"File is over {MAX_MB} MB. Please choose a shorter or compressed video.")
music = st.file_uploader("2) Optional background music", type=["mp3", "wav", "m4a"])
st.caption("For free cloud hosting, test with a 1–3 minute clip under 200 MB. The first run downloads the speech model and may take several minutes.")

start = st.button("🚀 3) START EDITING", type="primary", use_container_width=True,
                  disabled=(video is None or video.size > MAX_MB * 1024 * 1024))

if start and video:
    temp_dir = Path(tempfile.mkdtemp(prefix="auto_vlog_"))
    try:
        input_path = temp_dir / ("source" + Path(video.name).suffix.lower())
        with open(input_path, "wb") as f:
            f.write(video.getbuffer())
        music_path = None
        if music:
            music_path = temp_dir / ("music" + Path(music.name).suffix.lower())
            with open(music_path, "wb") as f:
                f.write(music.getbuffer())

        progress = st.progress(0)
        status = st.status("Starting cloud editing…", expanded=True)
        status.write("Checking video and audio streams…")
        total_duration = probe(input_path)
        audio_present = has_audio(input_path)
        status.write(f"Video duration: {total_duration:.1f} seconds. Audio track: {'found' if audio_present else 'not found'}.")
        prepared = temp_dir / "prepared.mp4"

        if remove_silence and audio_present:
            status.write("Step 1/4: Detecting long silent pauses…")
            keep, count = get_keep_segments(input_path, threshold, pause_length)
            status.write(f"Found {count} quiet section(s); preparing the edited timeline…")
            cut_segments(input_path, prepared, keep, True)
        else:
            shutil.copy2(input_path, prepared)
            if remove_silence and not audio_present:
                status.write("No audio track found; silence removal skipped.")

        progress.progress(25)
        status.write("Step 2/4: Loading speech recognition model. First run can take a few minutes…")
        model = load_whisper(model_name)
        lang_code = {"English": "en", "Urdu": "ur", "Hindi": "hi", "Arabic": "ar"}.get(language)
        status.write("Step 3/4: Transcribing speech and creating subtitles…")
        if not audio_present:
            raise RuntimeError("No audio track found.")
        roman = caption_language.startswith("Roman")
        import numpy as np
        pcm = subprocess.run(["ffmpeg","-v","error","-i",str(prepared),"-vn","-ac","1","-ar","16000","-f","f32le","-"],capture_output=True,check=True).stdout
        audio = np.frombuffer(pcm,dtype=np.float32).copy()
        segments, info = model.transcribe(audio, beam_size=5, vad_filter=True, language=lang_code, task="transcribe" if roman else "translate", word_timestamps=roman, condition_on_previous_text=False, temperature=0.0)
        raw_segments = list(segments)
        captions = make_captions(raw_segments, roman)
        status.write(f"Detected {len(captions)} caption groups. Automatic recognition may contain mistakes.")
        if not captions:
            raise RuntimeError("No speech was detected. Check that the video has clear speech, or select the correct language.")
        srt_path = temp_dir / "captions.srt"
        write_srt(captions, srt_path)
        ass_path = temp_dir / "captions.ass"
        vertical = aspect == "Vertical 9:16 (Reels)"
        if aspect == "Keep original ratio":
            dims=command(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","csv=p=0:s=x",str(prepared)]).stdout.strip().splitlines()[0].split("x")
            vertical=int(dims[1])>int(dims[0])
        write_ass(captions, ass_path, vertical, subtitle_size)
        progress.progress(65)
        status.write("Step 4/4: Rendering the final MP4 with captions and balanced audio…")
        output_path = temp_dir / "edited_vlog.mp4"
        render_video(prepared, output_path, ass_path, aspect, subtitle_size, music_path, music_volume, audio_present)
        progress.progress(100)
        status.update(label="Editing complete!", state="complete", expanded=False)
        st.success("Your edited video is ready.")
        st.session_state["edited_bytes"] = output_path.read_bytes()
        st.session_state["subtitle_bytes"] = srt_path.read_bytes()
        st.session_state["caption_text"] = " ".join(x.text.strip() for x in captions)
        st.video(st.session_state["edited_bytes"])
        
    except Exception as exc:
        st.error("The editing process stopped. Exact error:")
        st.exception(exc)
        st.info("If this error mentions memory/RAM, try a 30–60 second 720p MP4. If it mentions FFmpeg, send this error screenshot so I can fix the cloud dependency.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
else:
    st.subheader("How to use")
    st.markdown("1. Select your vlog above.\n2. Confirm the green **START EDITING** button is enabled.\n3. Press it once and wait. First-time speech-model download may take a few minutes.\n4. When complete, play and download the edited MP4.")

if "edited_bytes" in st.session_state:
    st.download_button("Download edited MP4", st.session_state["edited_bytes"], file_name="edited_vlog.mp4", mime="video/mp4")
    st.download_button("Download subtitles", st.session_state["subtitle_bytes"], file_name="captions.srt", mime="text/plain")
    with st.expander("Generated transcript"):
        st.write(st.session_state["caption_text"])
