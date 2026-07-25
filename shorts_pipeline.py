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
# 1. CLEANUP & INITIALIZATION
# ==========================================
def cleanup_temp_files():
    """Removes leftover temporary files from previous pipeline runs."""
    print("🧹 Cleaning up leftover temporary files...")
    patterns = ["*.mp4", "*.mp3", "*.png", "*.srt", "*.vtt", "temp_*", "raw_*"]
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
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt
                    )
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
        exclusion_clause = f"\nCRITICAL: DO NOT generate any topic similar to these previously used topics:\n- " + "\n- ".join(used_topic_names[-20:])
    
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
            print(f"🏷️  Pillar: {data.get('pillar')}")
            print(f"🧩 Topic: {data.get('topic')}")
            
            save_used_topic(data.get('title'), data.get('topic'), data.get('pillar'))
            return data
        except Exception as e:
            print(f"⚠️ Failed to parse Gemini response JSON: {e}")

    # Fallback default blueprint
    print("⚠️ Falling back to master default topic blueprint...")
    default_data = {
        "title": "The Strange Phenomenon That Solved Deep Space 🌌 Fleeting Time",
        "pillar": "Cosmic Science",
        "topic": "Einstein Time Dilation Mystery",
        "script": "Did you know that time doesn't run at the same speed for everyone? According to Albert Einstein's theory of relativity, gravity and high speed actually slow down time itself. Astronauts aboard the International Space Station orbit Earth at 17,500 miles per hour. Because of this extreme speed, they actually age slightly slower than everyone on Earth! After six months in space, astronauts return to Earth roughly 0.007 seconds younger than their twin siblings who stayed behind. But it gets even crazier. If you fell into a supermassive black hole, gravity would stretch time so intensely that one minute near the event horizon could equal 70 years back on Earth. Science is stranger than fiction. Subscribe for more cosmic secrets!",
        "description": "Discover how gravity and extreme velocity warp time itself! From astronauts on the Space Station aging slower to time stopping near black holes, Einstein's theory of time dilation changes everything we know about space.\n\nKey Takeaways:\n- Astronauts age 0.007s slower in orbit\n- Extreme velocity slows down time\n- Gravity near black holes stretches time drastically\n\nSubscribe for daily mind-bending science facts!\n\n#Shorts #Science #Space #TimeDilation #DidYouKnow",
        "stat_badges": ["17,500 MPH", "0.007 SECONDS"],
        "scenes": [
            {"search_query": "deep space starry night", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"},
            {"search_query": "albert einstein portrait physics", "media_type": "photo", "motion": "pan_right", "filter": "vintage"},
            {"search_query": "planet earth space station orbit", "media_type": "video", "motion": "zoom_out", "filter": "normal"},
            {"search_query": "astronaut floating zero gravity", "media_type": "photo", "motion": "pan_left", "filter": "vibrant"},
            {"search_query": "vintage clock ticking fast", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"},
            {"search_query": "black hole cosmic space", "media_type": "video", "motion": "zoom_out", "filter": "vibrant"},
            {"search_query": "glowing spacetime physics grid", "media_type": "photo", "motion": "zoom_in", "filter": "cinematic"},
            {"search_query": "hourglass sand falling time", "media_type": "video", "motion": "pan_right", "filter": "vintage"},
            {"search_query": "galaxy nebula space motion", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"},
            {"search_query": "glowing cosmic particles space", "media_type": "photo", "motion": "pan_left", "filter": "vibrant"}
        ]
    }
    save_used_topic(default_data['title'], default_data['topic'], default_data['pillar'])
    return default_data


# ==========================================
# 4. HORMOZI 1-3 WORD SUBTITLE ENGINE
# ==========================================
def parse_and_chunk_subtitles(vtt_or_srt_path, output_srt_path="captions.srt"):
    """Parses subtitle file and chunks lines into 1-3 words max for Hormozi style."""
    if not os.path.exists(vtt_or_srt_path):
        return False
        
    with open(vtt_or_srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2}[\.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[\.,]\d{3})')
    
    entries = []
    current_time = None
    current_text = []
    
    for line in lines:
        line = line.strip()
        match = time_pattern.search(line)
        if match:
            if current_time and current_text:
                entries.append((current_time[0], current_time[1], " ".join(current_text)))
                current_text = []
            start, end = match.group(1).replace(".", ","), match.group(2).replace(".", ",")
            current_time = (start, end)
        elif line and not line.startswith("WEBVTT") and not line.isdigit():
            current_text.append(line)
            
    if current_time and current_text:
        entries.append((current_time[0], current_time[1], " ".join(current_text)))

    # Chunk long subtitle blocks into 1 to 3 words
    short_entries = []
    for start, end, full_text in entries:
        words = re.sub(r'<[^>]+>', '', full_text).strip().split()
        if not words:
            continue
        
        # Split into chunks of 2-3 words
        chunk_size = 2 if len(words) > 4 else len(words)
        word_chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]
        
        # Approximate timing per chunk
        # Format HH:MM:SS,mmm
        short_entries.append((start, end, " ".join(words[:3])))

    with open(output_srt_path, "w", encoding="utf-8") as f:
        idx = 1
        for start, end, text in entries:
            clean_text = re.sub(r'<[^>]+>', '', text).strip()
            # Wrap in 2-3 word blocks
            words = clean_text.split()
            chunks = [" ".join(words[i:i+3]) for i in range(0, len(words), 3)]
            for chk in chunks:
                if chk:
                    f.write(f"{idx}\n")
                    f.write(f"{start} --> {end}\n")
                    f.write(f"{chk.upper()}\n\n")
                    idx += 1
                
    print(f"✅ Generated {idx-1} Hormozi-style short caption bursts in {output_srt_path}")
    return True


async def generate_voiceover_and_captions(text: str, audio_path: str = "voiceover.mp3", srt_path: str = "captions.srt"):
    print("\n2️⃣ Generating Pro Voiceover & Hormozi Subtitles...")
    raw_vtt = "raw_captions.vtt"
    
    try:
        cmd = [
            "edge-tts",
            "--voice", "en-US-AndrewNeural",
            "--rate=+5%",
            "--text", text,
            "--write-media", audio_path,
            "--write-subtitles", raw_vtt
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(raw_vtt):
            parse_and_chunk_subtitles(raw_vtt, srt_path)
        else:
            import edge_tts
            communicate = edge_tts.Communicate(text, "en-US-AndrewNeural", rate="+5%")
            submaker = edge_tts.SubMaker()
            with open(audio_path, "wb") as file:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        file.write(chunk["data"])
                    elif chunk["type"] in ["WordBoundary", "SentenceBoundary"]:
                        submaker.feed(chunk)
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(submaker.get_srt())

        print(f"✅ Voiceover saved to {audio_path}")
    except Exception as e:
        print(f"⚠️ Voiceover fallback ({e})...")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "40", audio_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:05,000\nAUTOMATED SHORT\n\n")

    return audio_path, srt_path


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
# 5. AUDIO ENGINE (VOICEOVER + BGM + SFX)
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
    else:
        return voiceover_file


# ==========================================
# 6. STAT BADGE PILLOW GRAPHIC GENERATOR
# ==========================================
def create_stat_badge_png(text_string, output_png_path="badge.png"):
    """Renders a sleek dark-glass stat graphic badge card."""
    img = Image.new("RGBA", (700, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw dark rounded rectangle with yellow border
    draw.rounded_rectangle([(10, 10), (690, 170)], radius=25, fill=(15, 23, 42, 230), outline=(250, 204, 21, 255), width=4)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
    except Exception:
        font = ImageFont.load_default()
        
    draw.text((350, 90), text_string.upper(), fill=(255, 255, 255), font=font, anchor="mm")
    img.save(output_png_path)
    print(f"🎨 Rendered Stat Badge Card: '{text_string}'")
    return output_png_path


# ==========================================
# 7. KEN BURNS MOTION & MIXED MEDIA ENGINE
# ==========================================
def create_ken_burns_clip(image_file, output_clip, motion_type="zoom_in", filter_style="normal", duration=4, fps=30):
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
    else:  # pan_right
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

    vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=1080x1920:fps={fps},{color_filter}"
    
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
            
            # 1. VIDEO SEARCH
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
                                    "-t", "4", "-r", "30",
                                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                                    "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
                                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                downloaded = True
                                print(f"  🎬 [VIDEO Scene {i}/{len(scenes)}] '{q}'")
                except Exception as err:
                    print(f"  ⚠️ Video search error: {err}")

            # 2. PHOTO SEARCH + KEN BURNS ANIMATION
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
                                create_ken_burns_clip(raw_img, clip_name, motion_type=motion, filter_style=f_style, duration=4)
                                downloaded = True
                                print(f"  🖼️ [PHOTO Scene {i}/{len(scenes)}] Ken Burns {motion.upper()}: '{q}'")
                except Exception as err:
                    print(f"  ⚠️ Photo search error: {err}")

        # 3. SYNTHETIC FALLBACK
        if not downloaded:
            print(f"  🎬 [SYNTHETIC Scene {i}/{len(scenes)}] '{q}'...")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c=navy:s=1080x1920:d=4:r=30",
                "-vf", f"drawtext=text='Scene {i} - {q[:15]}':fontcolor=yellow:fontsize=45:x=(w-text_w)/2:y=(h-text_h)/2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        clips.append(clip_name)

    print(f"   ✅ {len(clips)} mixed-media scenes ready!")
    return clips


# ==========================================
# 8. MASTER COMPOSITING & RENDERING ENGINE
# ==========================================
def render_professional_short(clips, audio_file, subtitle_file, stat_badges=None, output_filename="final_output.mp4"):
    print("\n4️⃣ Rendering Pro Video with Dark Vignette & Hormozi Captions...")
    
    if not clips:
        raise ValueError("No video clips available for rendering.")

    ffmpeg_cmd = ["ffmpeg", "-y"]
    for clip in clips:
        ffmpeg_cmd.extend(["-i", clip])
    
    ffmpeg_cmd.extend(["-i", audio_file])
    
    filter_chains = []
    scaled_outputs = []

    for i in range(len(clips)):
        filter_chains.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30[v{i}]"
        )
        scaled_outputs.append(f"[v{i}]")

    concat_inputs = "".join(scaled_outputs)
    filter_chains.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=0[vconcat]")

    # 1. Dark Vignette overlay bar across bottom 30% for subtitle contrast
    vignette_box = "drawbox=x=0:y=ih-450:w=iw:h=450:color=black@0.4:t=fill"
    
    # 2. Hormozi 1-3 word subtitles (Yellow bold text with black border, lower third)
    subtitle_filter = (
        f"subtitles=filename={subtitle_file}:force_style="
        "'Fontname=DejaVu Sans,Fontsize=24,PrimaryColour=&H0000FFFF&,"
        "OutlineColour=&H00000000&,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=120'"
    )
    filter_chains.append(f"[vconcat]{vignette_box},{subtitle_filter}[vfinal]")

    filter_complex_str = ";".join(filter_chains)
    ffmpeg_cmd.extend(["-filter_complex", filter_complex_str])
    ffmpeg_cmd.extend(["-map", "[vfinal]"])

    audio_stream_index = len(clips)
    ffmpeg_cmd.extend(["-map", f"{audio_stream_index}:a"])

    ffmpeg_cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        output_filename
    ])

    print("⚡ Compiling pro-level video with vignette, music & captions...")
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
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
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
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
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
    print("✅ Autonomous AI Director Pipeline Initialized!\n")
    
    cleanup_temp_files()
    
    # Step 1: AI Brainstorming & Director Planning
    topic_data = await discover_viral_topic()
    
    # Step 2: Render Stat Graphic Badges
    stat_badges = topic_data.get("stat_badges", ["17,500 MPH"])
    for i, badge in enumerate(stat_badges):
        create_stat_badge_png(badge, f"badge_{i+1}.png")
    
    # Step 3: Voiceover, Hormozi Captions & Sound Engine
    script_text = topic_data.get("script", "")
    raw_vo, srt_captions = await generate_voiceover_and_captions(script_text)
    faded_vo = apply_audio_fades(raw_vo)
    
    raw_bgm = generate_ambient_bgm()
    mixed_audio = mix_voiceover_and_bgm(faded_vo, raw_bgm)
    
    # Step 4: Mixed Media Assembly (HD Videos + Ken Burns Photos)
    scenes = topic_data.get("scenes", [])
    clips = await download_mixed_media_broll(scenes)
    
    # Step 5: Render Final Master Video
    output_video = "final_output.mp4"
    render_professional_short(clips, mixed_audio, srt_captions, stat_badges, output_filename=output_video)
    
    # Step 6: Upload to YouTube Channel
    video_title = topic_data.get("title", "Automated YouTube Short")
    video_description = topic_data.get("description", f"{script_text}\n\n#Shorts #Viral #Facts")
    upload_to_youtube(output_video, title=video_title, description=video_description)
    
    print("\n🚀 Autonomous AI Director pipeline finished successfully!")


if __name__ == "__main__":
    try:
        asyncio.run(run_master_pipeline())
    except Exception as e:
        print(f"\n❌ Pipeline crashed with exception: {e}")
        sys.exit(1)
