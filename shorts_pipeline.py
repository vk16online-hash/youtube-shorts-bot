import os
import sys
import json
import glob
import re
import random
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# --- Gemini SDK Setup ---
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

try:
    from google import genai
    from google.genai import types
    USE_NEW_SDK = True
except ImportError:
    try:
        import google.generativeai as genai_legacy
        USE_NEW_SDK = False
    except ImportError:
        USE_NEW_SDK = None

# --- YouTube API Imports ---
try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    YOUTUBE_SDK_AVAILABLE = True
except ImportError:
    YOUTUBE_SDK_AVAILABLE = False

# ==========================================
# GLOBAL EDIT CONFIG (ver7 — "advanced edit" pass)
# ==========================================
CLIP_DURATION = 4.3       # each scene's raw length before overlap is eaten by transitions
TRANS_DURATION = 0.30     # crossfade/whip length between scenes
FPS = 30
# Kept to transitions supported since ffmpeg 4.3 (xfade), so this runs on older ffmpeg builds too.
TRANSITION_POOL = ["fade", "dissolve", "wipeleft", "wiperight", "smoothleft", "smoothright", "circleopen", "radial"]

# ==========================================
# 1. CLEANUP & INITIALIZATION
# ==========================================
def cleanup_temp_files():
    """Removes leftover temporary files from previous pipeline runs."""
    print("🧹 Cleaning up leftover temporary files...")
    patterns = ["*.mp4", "*.mp3", "*.png", "*.srt", "*.vtt", "*.ass", "temp_*", "raw_*"]
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            if filepath != "final_output.mp4" and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass

# ==========================================
# 2. TOPIC DEDUPLICATION HISTORY
# ==========================================
TOPIC_HISTORY_FILE = "used_topics.json"

def load_used_topics():
    if os.path.exists(TOPIC_HISTORY_FILE):
        try:
            with open(TOPIC_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to read {TOPIC_HISTORY_FILE}: {e}")
    return []

def save_used_topic(topic_title, topic_name, pillar):
    history = load_used_topics()
    history.append({
        "topic": topic_name,
        "title": topic_title,
        "pillar": pillar,
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    })
    try:
        with open(TOPIC_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        print(f"💾 Updated topic history saved to {TOPIC_HISTORY_FILE}")
    except Exception as e:
        print(f"⚠️ Failed to save {TOPIC_HISTORY_FILE}: {e}")

# ==========================================
# DYNAMIC FALLBACK TOPICS POOL (VARIETY GUARANTEE)
# ==========================================
FALLBACK_TOPICS_POOL = [
    {
        "title": "The Moldy Mistake That Saved a Billion Lives 🦠💊",
        "pillar": "Medical History",
        "topic": "The Accidental Discovery of Penicillin",
        "script": "In 1928, Alexander Fleming returned from vacation to find his petri dishes covered in mold. But instead of throwing them away, he noticed something impossible. The mold was completely killing bacteria around it! That single accident led to penicillin, saving over 200 million lives and completely changing modern medicine forever. Subscribe for more crazy history facts!",
        "description": "How a messy laboratory accident in 1928 led to the discovery of penicillin, the miracle antibiotic that transformed modern medicine!\n\nKey Takeaways:\n- Alexander Fleming discovered penicillin by accident\n- Petri dish mold killed surrounding bacteria\n- Over 200 million lives saved worldwide\n\n#Shorts #Science #History #Penicillin #DidYouKnow",
        "stat_badges": ["YEAR 1928", "200 MILLION LIVES"],
        "scenes": [
            {"search_query": "old scientist laboratory desk", "media_type": "photo", "motion": "zoom_in", "filter": "vintage"},
            {"search_query": "petri dish bacteria macro", "media_type": "video", "motion": "zoom_out", "filter": "cinematic"},
            {"search_query": "microscope scientist lab", "media_type": "video", "motion": "pan_right", "filter": "normal"},
            {"search_query": "vintage medicine pharmacy bottles", "media_type": "photo", "motion": "pan_left", "filter": "vintage"},
            {"search_query": "modern hospital operating room", "media_type": "video", "motion": "zoom_in", "filter": "vibrant"}
        ]
    },
    {
        "title": "The Ocean Trench Deeper Than Mount Everest 🌊 Oceanic Abyss",
        "pillar": "Earth Science",
        "topic": "Secrets of the Mariana Trench",
        "script": "Did you know that the deepest point on Earth could swallow Mount Everest whole with miles to spare? The Mariana Trench plunges nearly 36,000 feet into complete darkness. The water pressure at the bottom is over 1,000 times greater than at the surface—enough to crush a submarine like a soda can! Yet bizarre glowing sea creatures thrive down there in total darkness. Subscribe for deep ocean mysteries!",
        "description": "Explore the terrifying depths of the Mariana Trench, Earth's deepest underwater abyss, reaching nearly 36,000 feet deep!\n\nKey Takeaways:\n- 36,000 feet deep in complete darkness\n- Water pressure 1,000x greater than surface level\n- Glowing creatures thrive in extreme depths\n\n#Shorts #Ocean #DeepSea #EarthFacts #DidYouKnow",
        "stat_badges": ["36,000 FEET", "1,000X PRESSURE"],
        "scenes": [
            {"search_query": "deep blue ocean water abyss", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"},
            {"search_query": "underwater submarine deep sea", "media_type": "photo", "motion": "pan_left", "filter": "cinematic"},
            {"search_query": "bioluminescent sea creature glowing", "media_type": "video", "motion": "zoom_out", "filter": "vibrant"},
            {"search_query": "mount everest snow mountain peak", "media_type": "photo", "motion": "pan_right", "filter": "normal"},
            {"search_query": "deep ocean dark underwater", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"}
        ]
    },
    {
        "title": "The Golden Record Sent to Aliens 🚀 Voyaging Beyond Earth",
        "pillar": "Space Exploration",
        "topic": "Voyager 1 Golden Record Message",
        "script": "In 1977, NASA launched Voyager 1 into deep space carrying a 12-inch phonograph record made of solid gold. On it, scientists recorded natural sounds of Earth, music from Beethoven, and greetings in 55 human languages. Voyager 1 is now over 15 billion miles away in interstellar space, traveling at 38,000 miles per hour. It will float through the galaxy for billions of years long after Earth is gone. Subscribe for cosmic space stories!",
        "description": "Discover the Voyager Golden Record, humanity's time capsule sent to interstellar space for alien civilizations to find!\n\nKey Takeaways:\n- Solid gold record carrying Earth sounds and music\n- Greetings in 55 human languages\n- Over 15 billion miles from Earth in deep space\n\n#Shorts #Space #NASA #Voyager #Cosmos",
        "stat_badges": ["YEAR 1977", "15 BILLION MILES"],
        "scenes": [
            {"search_query": "voyager spacecraft space NASA", "media_type": "photo", "motion": "zoom_in", "filter": "cinematic"},
            {"search_query": "golden record space voyager", "media_type": "photo", "motion": "pan_right", "filter": "vibrant"},
            {"search_query": "deep space stars galaxy nebula", "media_type": "video", "motion": "zoom_out", "filter": "cinematic"},
            {"search_query": "sound waves glowing audio spectrum", "media_type": "video", "motion": "pan_left", "filter": "vibrant"},
            {"search_query": "planet earth floating space", "media_type": "video", "motion": "zoom_in", "filter": "normal"}
        ]
    }
]

# ==========================================
# 3. AI BRAINSTORMING & DIRECTOR PLANNER
# ==========================================
async def generate_topic_with_gemini_fallback(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY environment variable not set.")
        return None

    for model_name in GEMINI_MODELS:
        print(f"🎯 Attempting generation using model: {model_name}...")
        for attempt in range(2):
            try:
                if USE_NEW_SDK:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(model=model_name, contents=prompt)
                    if response and response.text:
                        print(f"✅ Success with model {model_name}!")
                        return response.text
                elif USE_NEW_SDK is False:
                    genai_legacy.configure(api_key=api_key)
                    model = genai_legacy.GenerativeModel(model_name)
                    response = model.generate_content(prompt)
                    if response and response.text:
                        print(f"✅ Success with model {model_name}!")
                        return response.text
                else:
                    print("❌ No Gemini SDK installed.")
                    return None
            except Exception as err:
                err_msg = str(err)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    print(f"⚠️ Model {model_name} hit rate limit (429). Waiting 20 seconds...")
                    await asyncio.sleep(20)
                else:
                    print(f"⚠️ Model {model_name} failed with error: {err_msg}")
                    break
    print("⚠️ All Gemini models failed or quota exhausted.")
    return None

async def discover_viral_topic():
    print("\n1️⃣ AI Director Brainstorming Viral Concept & Blueprint...")
    used_topics = load_used_topics()
    used_topic_names = [t.get("topic") for t in used_topics if t.get("topic")]

    exclusion_clause = ""
    if used_topic_names:
        exclusion_clause = "\nCRITICAL: DO NOT generate any topic similar to these previously used topics:\n- " + "\n- ".join(used_topic_names[-20:])

    prompt = f"""
You are an expert video director for viral YouTube Shorts (style: Vox, Alex Hormozi, MagnatesMedia).
{exclusion_clause}

Generate a high-retention viral video blueprint in JSON format:
- title: Catchy short title with emojis (under 80 characters)
- pillar: Content pillar (Science, History, Space, Mysteries)
- topic: Specific detailed topic name
- script: High-energy storytelling script between 180 and 220 words (~40 seconds spoken at fast pace). Pattern interrupt hook in first 3 seconds, fascinating core facts, open loop curiosity gap, and subscribe CTA.
- description: Full SEO YouTube description with summary, key takeaways, CTA, and 5 viral hashtags (#Shorts #Science #Facts #DidYouKnow #Viral).
- stat_badges: List of 2 key numbers/stats mentioned in the script (e.g. ["17,500 MPH", "0.007 SECONDS"]).
- scenes: Array of 10 scene objects matching the script progression:
  - "search_query": Simple visual search keywords (e.g. "deep space stars", "albert einstein physics", "black hole CGI")
  - "media_type": Alternate between "video" and "photo"
  - "motion": One of ["zoom_in", "zoom_out", "pan_left", "pan_right"]
  - "filter": One of ["cinematic", "vintage", "bw", "vibrant", "normal"]

Return ONLY valid raw JSON output without markdown formatting.
"""
    response_text = await generate_topic_with_gemini_fallback(prompt)
    if response_text:
        try:
            clean_json = response_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            print(f"📌 Title: {data.get('title')}")
            print(f"🏷️ Pillar: {data.get('pillar')}")
            print(f"🧩 Topic: {data.get('topic')}")
            save_used_topic(data.get('title'), data.get('topic'), data.get('pillar'))
            return data
        except Exception as e:
            print(f"⚠️ Failed to parse Gemini response JSON: {e}")

    print("⚠️ Gemini unavailable. Selecting a fresh topic from the Fallback Pool...")
    available_pool = [t for t in FALLBACK_TOPICS_POOL if t.get("topic") not in used_topic_names]
    if not available_pool:
        available_pool = FALLBACK_TOPICS_POOL
    selected_topic = random.choice(available_pool)
    print(f"📌 Selected Fallback Topic: {selected_topic.get('title')}")
    save_used_topic(selected_topic.get('title'), selected_topic.get('topic'), selected_topic.get('pillar'))
    return selected_topic

# ==========================================
# 4. KARAOKE-STYLE ASS CAPTION ENGINE  (ver7)
# ==========================================
# ver6 burned plain yellow SRT text with no animation. ver7 renders proper
# ".ass" subtitles: bold oversized font, a punchy scale pop-in on every
# 2-word burst, and a karaoke color sweep (white -> accent) timed to the
# actual voice track, so captions look "designed" instead of pasted on.

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Burst,DejaVu Sans,86,&H00FFFFFF,&H0000D7FF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,340,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def _ass_time(ms):
    ms = max(int(ms), 0)
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    cs = (ms % 1000) // 10
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

def time_str_to_ms(time_str):
    h, m, s_ms = time_str.split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

def parse_vtt_to_karaoke_ass(vtt_file_path, ass_file_path="captions.ass"):
    """Parses the edge-tts VTT into 2-word bursts, each rendered as a karaoke
    line: pops in with a quick scale animation, then sweeps from white to a
    gold/accent color word-by-word in sync with the voiceover timing."""
    if not os.path.exists(vtt_file_path):
        return False

    with open(vtt_file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2}[\.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[\.,]\d{3})')
    cues = []
    current_start = None
    current_end = None
    current_text_lines = []

    for line in lines:
        line = line.strip()
        match = time_pattern.search(line)
        if match:
            if current_start and current_end and current_text_lines:
                text = " ".join(current_text_lines).strip()
                if text:
                    cues.append((current_start, current_end, text))
                current_text_lines = []
            current_start = match.group(1).replace(".", ",")
            current_end = match.group(2).replace(".", ",")
        elif line and not line.startswith("WEBVTT") and not line.isdigit():
            current_text_lines.append(line)

    if current_start and current_end and current_text_lines:
        text = " ".join(current_text_lines).strip()
        if text:
            cues.append((current_start, current_end, text))

    if not cues:
        return False

    events = []
    accent_colors = ["&H0000D7FF", "&H0059FFAD", "&H00FF6EC7"]  # gold, mint, pink — rotate per burst for variety

    for cue_idx, (start_str, end_str, full_text) in enumerate(cues):
        clean_text = re.sub(r'<[^>]+>', '', full_text).strip()
        words = clean_text.split()
        if not words:
            continue

        start_ms = time_str_to_ms(start_str)
        end_ms = time_str_to_ms(end_str)
        duration_ms = max(end_ms - start_ms, 400)

        chunks = [words[i:i + 2] for i in range(0, len(words), 2)]
        chunk_duration = duration_ms / len(chunks)

        for k, chunk_words in enumerate(chunks):
            c_start = start_ms + (k * chunk_duration)
            c_end = start_ms + ((k + 1) * chunk_duration)
            accent = accent_colors[(cue_idx + k) % len(accent_colors)]

            # \k timing is in centiseconds per word for the karaoke sweep
            per_word_cs = max(int((chunk_duration / len(chunk_words)) / 10), 8)
            karaoke_text = "".join(f"{{\\kf{per_word_cs}}}{w.upper()} " for w in chunk_words).strip()

            # Pop-in: start slightly small, snap to full size in ~120ms, tiny overshoot for punch
            pop = r"{\fscx55\fscy55\t(0,90,\fscx112\fscy112)\t(90,150,\fscx100\fscy100)}"
            # Override the karaoke "sung" colour per-line so bursts rotate accent colors
            color_override = f"{{\\2c{accent}}}"

            events.append(
                f"Dialogue: 0,{_ass_time(c_start)},{_ass_time(c_end)},Burst,,0,0,0,,{pop}{color_override}{karaoke_text}"
            )

    with open(ass_file_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write("\n".join(events))
        f.write("\n")

    print(f"✅ Created {len(events)} karaoke caption bursts in {ass_file_path}")
    return True

async def generate_voiceover_and_captions(text: str, audio_path: str = "voiceover.mp3", ass_path: str = "captions.ass"):
    print("\n2️⃣ Generating Pro Voiceover & Karaoke Captions...")
    raw_vtt = "raw_captions.vtt"
    try:
        cmd = [
            "edge-tts", "--voice", "en-US-AndrewNeural", "--rate=+5%",
            "--text", text, "--write-media", audio_path, "--write-subtitles", raw_vtt
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(raw_vtt):
            success = parse_vtt_to_karaoke_ass(raw_vtt, ass_path)
            if not success:
                _write_fallback_ass(ass_path)
        else:
            print(f"⚠️ CLI edge-tts notice: {result.stderr}")
            _write_fallback_ass(ass_path)
        print(f"✅ Voiceover saved to {audio_path}")
    except Exception as e:
        print(f"⚠️ Voiceover fallback ({e})...")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "40", audio_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _write_fallback_ass(ass_path)

    return audio_path, ass_path

def _write_fallback_ass(ass_path):
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write(f"Dialogue: 0,0:00:00.00,0:00:05.00,Burst,,0,0,0,,AUTOMATED SHORT\n")

def apply_audio_fades(input_audio: str, output_audio: str = "voiceover_faded.mp3", fade_duration: float = 0.5):
    print("2b️⃣ Applying audio fades...")
    cmd = [
        "ffmpeg", "-y", "-i", input_audio,
        "-af", f"afade=t=in:ss=0:d={fade_duration},afade=t=out:st=38:d={fade_duration}",
        output_audio
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("✅ Audio fades applied successfully.")
    return output_audio

# ==========================================
# 5. AUDIO ENGINE (VOICEOVER + BGM)
# ==========================================
def generate_ambient_bgm(output_bgm: str = "bgm.mp3", duration: int = 45):
    print("🎵 Synthesizing background music track...")
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=120:duration={duration}",
        "-af", "volume=0.10,lowpass=f=350,afade=t=in:ss=0:d=2,afade=t=out:st=40:d=3",
        "-c:a", "libmp3lame", output_bgm
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(output_bgm):
        cmd_fallback = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=120:duration={duration}",
            "-af", "volume=0.10,lowpass=f=350", output_bgm
        ]
        subprocess.run(cmd_fallback, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_bgm

def mix_voiceover_and_bgm(voiceover_file, bgm_file, output_mixed="final_audio.mp3"):
    print("🎚️ Mixing audio engine (Voiceover + BGM)...")
    if not os.path.exists(bgm_file) or os.path.getsize(bgm_file) == 0:
        return voiceover_file
    cmd = [
        "ffmpeg", "-y", "-i", voiceover_file, "-i", bgm_file,
        "-filter_complex", "[0:a]volume=1.0[v];[1:a]volume=0.10[b];[v][b]amix=inputs=2:duration=first[aout]",
        "-map", "[aout]", output_mixed
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and os.path.exists(output_mixed):
        print("✅ Audio track mixed with ducking.")
        return output_mixed
    return voiceover_file

# ==========================================
# 6. STAT BADGE PILLOW GRAPHIC GENERATOR
# ==========================================
def create_stat_badge_png(text_string, output_png_path="badge.png"):
    img = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(10, 10), (890, 210)], radius=32, fill=(15, 23, 42, 235), outline=(250, 204, 21, 255), width=5)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56)
    except Exception:
        font = ImageFont.load_default()
    draw.text((450, 112), text_string.upper(), fill=(255, 255, 255), font=font, anchor="mm")
    img.save(output_png_path)
    print(f"🎨 Rendered Stat Badge Card: '{text_string}'")
    return output_png_path

# ==========================================
# 7. KEN BURNS MOTION & MIXED MEDIA ENGINE
# ==========================================
def create_ken_burns_clip(image_file, output_clip, motion_type="zoom_in", filter_style="normal", duration=CLIP_DURATION, fps=FPS):
    total_frames = int(duration * fps)
    if motion_type == "zoom_in":
        z_expr = "min(zoom+0.0015,1.20)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type == "zoom_out":
        z_expr = "max(1.20-0.0015*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type == "pan_left":
        z_expr = "1.15"
        x_expr = f"(1-on/{total_frames})*(iw-iw/zoom)"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        z_expr = "1.15"
        x_expr = f"(on/{total_frames})*(iw-iw/zoom)"
        y_expr = "ih/2-(ih/zoom/2)"

    color_filter = "eq=contrast=1.1:saturation=1.2"
    if filter_style == "vintage":
        color_filter = "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131"
    elif filter_style == "bw":
        color_filter = "hue=s=0,eq=contrast=1.2"
    elif filter_style == "vibrant":
        color_filter = "eq=contrast=1.25:saturation=1.5"

    vf = (f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=1080x1920:fps={fps},{color_filter}")
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_file,
        "-vf", vf, "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", output_clip
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def download_mixed_media_broll(scenes):
    print("\n3️⃣ Assembling Mixed-Media Scenes (HD Videos + Ken Burns Motion)...")
    clips = []
    pexels_api_key = os.environ.get("PEXELS_API_KEY")

    for i, sc in enumerate(scenes, 1):
        q = sc.get("search_query", "space stars")
        m_type = sc.get("media_type", "video")
        motion = sc.get("motion", "zoom_in")
        f_style = sc.get("filter", "normal")
        clip_name = f"clip_{i}.mp4"
        downloaded = False

        if pexels_api_key:
            import requests
            headers = {"Authorization": pexels_api_key}
            if m_type == "video":
                try:
                    url = f"https://api.pexels.com/videos/search?query={q}&per_page=1&orientation=portrait"
                    res = requests.get(url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        videos = res.json().get("videos", [])
                        if videos:
                            vf_list = videos[0].get("video_files", [])
                            best_link = next((vf["link"] for vf in vf_list if vf.get("quality") == "hd"), vf_list[0]["link"] if vf_list else None)
                            if best_link:
                                raw_file = f"raw_{clip_name}"
                                v_res = requests.get(best_link, timeout=20)
                                with open(raw_file, "wb") as f:
                                    f.write(v_res.content)
                                subprocess.run([
                                    "ffmpeg", "-y", "-i", raw_file,
                                    "-t", str(CLIP_DURATION), "-r", str(FPS),
                                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,"
                                           "eq=contrast=1.08:saturation=1.15",
                                    "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
                                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                downloaded = True
                                print(f"  🎬 [VIDEO Scene {i}/{len(scenes)}] '{q}'")
                except Exception as err:
                    print(f"  ⚠️ Video search error: {err}")

            if not downloaded:
                try:
                    photo_url = f"https://api.pexels.com/v1/search?query={q}&per_page=1&orientation=portrait"
                    p_res = requests.get(photo_url, headers=headers, timeout=10)
                    if p_res.status_code == 200:
                        photos = p_res.json().get("photos", [])
                        if photos:
                            img_link = photos[0]["src"].get("large2x") or photos[0]["src"].get("original")
                            if img_link:
                                raw_img = f"raw_img_{i}.jpg"
                                img_bytes = requests.get(img_link, timeout=20).content
                                with open(raw_img, "wb") as f:
                                    f.write(img_bytes)
                                create_ken_burns_clip(raw_img, clip_name, motion_type=motion, filter_style=f_style, duration=CLIP_DURATION)
                                downloaded = True
                                print(f"  🖼️ [PHOTO Scene {i}/{len(scenes)}] Ken Burns {motion.upper()}: '{q}'")
                except Exception as err:
                    print(f"  ⚠️ Photo search error: {err}")

        if not downloaded:
            print(f"  🎬 [SYNTHETIC Scene {i}/{len(scenes)}] '{q}'...")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c=navy:s=1080x1920:d={CLIP_DURATION}:r={FPS}",
                "-vf", f"drawtext=text='Scene {i} - {q[:15]}':fontcolor=yellow:fontsize=45:x=(w-text_w)/2:y=(h-text_h)/2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        clips.append(clip_name)

    print(f"  ✅ {len(clips)} mixed-media scenes ready!")
    return clips

# ==========================================
# 8. MASTER COMPOSITING & RENDERING ENGINE  (ver7)
# ==========================================
def _build_xfade_chain(n_clips, clip_duration=CLIP_DURATION, trans_duration=TRANS_DURATION):
    """Chains ffmpeg's xfade filter across every clip so cuts become real
    whip/dissolve transitions instead of hard cuts, picking a different
    transition style per cut for variety."""
    chain = []
    last_label = "v0"
    running_offset = clip_duration - trans_duration
    for i in range(1, n_clips):
        next_label = f"v{i}"
        out_label = f"x{i}" if i < n_clips - 1 else "vconcat"
        transition = random.choice(TRANSITION_POOL)
        chain.append(
            f"[{last_label}][{next_label}]xfade=transition={transition}:duration={trans_duration}:offset={running_offset:.3f}[{out_label}]"
        )
        last_label = out_label
        running_offset += clip_duration - trans_duration
    return chain, running_offset  # running_offset ends up ~= total duration after loop

def render_professional_short(clips, audio_file, subtitle_file, stat_badges=None, output_filename="final_output.mp4"):
    print("\n4️⃣ Rendering Pro Video with Transitions, Cinematic Grade & Karaoke Captions...")
    if not clips:
        raise ValueError("No video clips available for rendering.")

    stat_badges = stat_badges or []
    badge_files = [f"badge_{i+1}.png" for i in range(len(stat_badges)) if os.path.exists(f"badge_{i+1}.png")]

    ffmpeg_cmd = ["ffmpeg", "-y"]
    for clip in clips:
        ffmpeg_cmd.extend(["-i", clip])
    audio_input_index = len(clips)
    ffmpeg_cmd.extend(["-i", audio_file])
    badge_input_start = audio_input_index + 1
    for bf in badge_files:
        ffmpeg_cmd.extend(["-i", bf])

    filter_chains = []
    for i in range(len(clips)):
        filter_chains.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps={FPS}[v{i}]"
        )

    # Real transitions instead of a hard concat cut
    if len(clips) > 1:
        xfade_chain, total_duration = _build_xfade_chain(len(clips))
        filter_chains.extend(xfade_chain)
    else:
        filter_chains.append("[v0]copy[vconcat]")
        total_duration = CLIP_DURATION

    # Cinematic grade: subtle vignette + soft gradient bottom bar (two stacked
    # boxes at different alpha approximate a gradient without a slow geq filter)
    grade = "vignette=PI/5"
    gradient_box_1 = "drawbox=x=0:y=ih-520:w=iw:h=520:color=black@0.15:t=fill"
    gradient_box_2 = "drawbox=x=0:y=ih-300:w=iw:h=300:color=black@0.35:t=fill"

    subtitle_filter = f"ass=filename={subtitle_file}"

    current = "vconcat"
    filter_chains.append(f"[{current}]{grade},{gradient_box_1},{gradient_box_2},{subtitle_filter}[vgraded]")
    current = "vgraded"

    # Stat badge overlays: pop-in with fade + slight scale, placed near the
    # upper third so they never collide with the caption band.
    badge_windows = []
    if badge_files:
        window_span = max(total_duration - 8, 6)
        for idx in range(len(badge_files)):
            start_t = 2.5 + idx * (window_span / max(len(badge_files), 1))
            badge_windows.append((start_t, start_t + 3.0))

    for idx, bf in enumerate(badge_files):
        badge_stream = f"{badge_input_start + idx}:v"
        start_t, end_t = badge_windows[idx]
        scaled_label = f"badge{idx}"
        filter_chains.append(
            f"[{badge_stream}]format=rgba,fade=t=in:st=0:d=0.25:alpha=1,fade=t=out:st={end_t-start_t-0.25:.2f}:d=0.25:alpha=1[{scaled_label}]"
        )
        next_label = f"vbadge{idx}"
        filter_chains.append(
            f"[{current}][{scaled_label}]overlay=x=(main_w-overlay_w)/2:y=180:enable='between(t,{start_t:.2f},{end_t:.2f})'[{next_label}]"
        )
        current = next_label

    filter_chains.append(f"[{current}]null[vfinal]")

    filter_complex_str = ";".join(filter_chains)
    ffmpeg_cmd.extend(["-filter_complex", filter_complex_str])
    ffmpeg_cmd.extend(["-map", "[vfinal]", "-map", f"{audio_input_index}:a"])
    ffmpeg_cmd.extend([
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", output_filename
    ])

    print("⚡ Compiling pro-level video with transitions, grade, badges & karaoke captions...")
    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ FFmpeg Error Output:\n{result.stderr}")
        raise RuntimeError("Final video assembly failed.")
    print(f"🎉 Master video compiled successfully: {output_filename}")

# ==========================================
# 9. YOUTUBE UPLOAD MODULE
# ==========================================
def upload_to_youtube(video_file, title, description, tags=None):
    print("\n5️⃣ Uploading Short to YouTube Channel...")
    if not YOUTUBE_SDK_AVAILABLE:
        print("⚠️ YouTube SDK missing. Skipping upload.")
        return False

    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        print("⚠️ YouTube API credentials not set. Skipping upload.")
        return False

    try:
        creds = Credentials(
            token=None, refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id, client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )
        youtube = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags or ["shorts", "viral", "science", "facts"],
                "categoryId": "27"
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
        }
        media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"  Uploading... {int(status.progress() * 100)}%")
        print(f"🎉 Video uploaded successfully to YouTube! Video ID: {response.get('id')}")
        return True
    except Exception as e:
        print(f"❌ YouTube upload failed: {e}")
        return False

# ==========================================
# MASTER PIPELINE RUNNER
# ==========================================
async def run_master_pipeline():
    print("✅ Autonomous AI Director Pipeline Initialized (ver7 — advanced edit)!\n")
    cleanup_temp_files()

    topic_data = await discover_viral_topic()

    stat_badges = topic_data.get("stat_badges", ["17,500 MPH"])
    for i, badge in enumerate(stat_badges):
        create_stat_badge_png(badge, f"badge_{i+1}.png")

    script_text = topic_data.get("script", "")
    raw_vo, ass_captions = await generate_voiceover_and_captions(script_text)
    faded_vo = apply_audio_fades(raw_vo)
    raw_bgm = generate_ambient_bgm()
    mixed_audio = mix_voiceover_and_bgm(faded_vo, raw_bgm)

    scenes = topic_data.get("scenes", [])
    clips = await download_mixed_media_broll(scenes)

    output_video = "final_output.mp4"
    render_professional_short(clips, mixed_audio, ass_captions, stat_badges, output_filename=output_video)

    video_title = topic_data.get("title", "Automated YouTube Short")
    video_description = topic_data.get("description", f"{script_text}\n\n#Shorts #Viral #Facts")
    upload_to_youtube(output_video, title=video_title, description=video_description)

    print("\n🚀 Autonomous AI Director pipeline (ver7) finished successfully!")

if __name__ == "__main__":
    try:
        asyncio.run(run_master_pipeline())
    except Exception as e:
        print(f"\n❌ Pipeline crashed with exception: {e}")
        sys.exit(1)
