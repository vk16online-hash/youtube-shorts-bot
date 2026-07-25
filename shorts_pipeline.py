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

# Force unbuffered output so logs print live in GitHub Actions
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

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

# Target final short length
TARGET_MIN_SECONDS = 40
TARGET_MAX_SECONDS = 45
TRANSITION_DURATION = 0.5  # seconds of crossfade between scenes
TRANSITION_STYLES = ["fade", "dissolve", "wipeleft", "wiperight", "circleopen", "pixelize", "smoothleft", "smoothright"]

# ==========================================
# 1. CLEANUP & INITIALIZATION
# ==========================================
def cleanup_temp_files():
    """Removes leftover temporary files from previous pipeline runs."""
    print("🧹 Cleaning up leftover temporary files...")
    patterns = ["*.mp4", "*.mp3", "*.png", "*.srt", "*.vtt", "*.ass", "*.log", "temp_*", "raw_*"]
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
        "script": "In 1928, Alexander Fleming returned from vacation to find his petri dishes covered in mold. But instead of throwing them away, he noticed something impossible. The mold was completely killing bacteria around it! That single accident led to penicillin, one of the most important discoveries in human history. Within twenty years it was mass produced and shipped to soldiers dying from infected wounds. Today it's estimated penicillin and the antibiotics it inspired have saved over two hundred million lives. One messy lab, one lucky accident, and modern medicine was never the same. Subscribe for more crazy history facts!",
        "description": "How a messy laboratory accident in 1928 led to the discovery of penicillin, the miracle antibiotic that transformed modern medicine!\n\nKey Takeaways:\n- Alexander Fleming discovered penicillin by accident\n- Petri dish mold killed surrounding bacteria\n- Over 200 million lives saved worldwide\n\n#Shorts #Science #History #Penicillin #DidYouKnow",
        "stat_badges": ["YEAR 1928", "200 MILLION LIVES"],
        "scenes": [
            {"entity_query": "Alexander Fleming", "search_query": "old scientist laboratory desk", "media_type": "photo", "motion": "zoom_in", "filter": "vintage"},
            {"entity_query": "penicillin petri dish mold", "search_query": "petri dish bacteria macro", "media_type": "video", "motion": "zoom_out", "filter": "cinematic"},
            {"entity_query": "", "search_query": "microscope scientist lab", "media_type": "video", "motion": "pan_right", "filter": "normal"},
            {"entity_query": "penicillin vintage medicine bottle", "search_query": "vintage medicine pharmacy bottles", "media_type": "photo", "motion": "pan_left", "filter": "vintage"},
            {"entity_query": "Alexander Fleming laboratory notes", "search_query": "scientist writing notes lab", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"},
            {"entity_query": "", "search_query": "hospital corridor walking", "media_type": "video", "motion": "pan_right", "filter": "normal"},
            {"entity_query": "", "search_query": "modern hospital operating room", "media_type": "video", "motion": "zoom_in", "filter": "vibrant"}
        ]
    },
    {
        "title": "The Ocean Trench Deeper Than Mount Everest 🌊 Oceanic Abyss",
        "pillar": "Earth Science",
        "topic": "Secrets of the Mariana Trench",
        "script": "Did you know that the deepest point on Earth could swallow Mount Everest whole with miles to spare? The Mariana Trench plunges nearly 36,000 feet into complete darkness. The water pressure at the bottom is over 1,000 times greater than at the surface, enough to crush a submarine like a soda can. Sunlight has never touched the trench floor, yet strange glowing sea creatures thrive down there, surviving in freezing near total darkness. Only a handful of humans have ever gone down and come back. What else is hiding at the bottom of our own planet? Subscribe for more deep ocean mysteries!",
        "description": "Explore the terrifying depths of the Mariana Trench, Earth's deepest underwater abyss, reaching nearly 36,000 feet deep!\n\nKey Takeaways:\n- 36,000 feet deep in complete darkness\n- Water pressure 1,000x greater than surface level\n- Glowing creatures thrive in extreme depths\n\n#Shorts #Ocean #DeepSea #EarthFacts #DidYouKnow",
        "stat_badges": ["36,000 FEET", "1,000X PRESSURE"],
        "scenes": [
            {"entity_query": "Mariana Trench map", "search_query": "deep blue ocean water abyss", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"},
            {"entity_query": "bathyscaphe Trieste submarine", "search_query": "underwater submarine deep sea", "media_type": "photo", "motion": "pan_left", "filter": "cinematic"},
            {"entity_query": "", "search_query": "bioluminescent sea creature glowing", "media_type": "video", "motion": "zoom_out", "filter": "vibrant"},
            {"entity_query": "Mount Everest", "search_query": "mount everest snow mountain peak", "media_type": "photo", "motion": "pan_right", "filter": "normal"},
            {"entity_query": "", "search_query": "ocean waves aerial drone", "media_type": "video", "motion": "pan_left", "filter": "cinematic"},
            {"entity_query": "", "search_query": "deep sea diver exploring", "media_type": "video", "motion": "zoom_in", "filter": "vintage"},
            {"entity_query": "", "search_query": "deep ocean dark underwater", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"}
        ]
    },
    {
        "title": "The Golden Record Sent to Aliens 🚀 Voyaging Beyond Earth",
        "pillar": "Space Exploration",
        "topic": "Voyager 1 Golden Record Message",
        "script": "In 1977, NASA launched Voyager 1 into deep space carrying a 12 inch phonograph record made of solid gold. On it, scientists recorded natural sounds of Earth, music from Beethoven, and greetings in fifty five human languages. It was humanity's message in a bottle, thrown into the cosmic ocean. Voyager 1 is now over fifteen billion miles away in interstellar space, traveling at 38,000 miles per hour, further from home than anything else we've ever built. It will keep floating through the galaxy for billions of years, long after Earth itself is gone. Somewhere out there, it's still carrying our voice. Subscribe for more cosmic space stories!",
        "description": "Discover the Voyager Golden Record, humanity's time capsule sent to interstellar space for alien civilizations to find!\n\nKey Takeaways:\n- Solid gold record carrying Earth sounds and music\n- Greetings in 55 human languages\n- Over 15 billion miles from Earth in deep space\n\n#Shorts #Space #NASA #Voyager #Cosmos",
        "stat_badges": ["YEAR 1977", "15 BILLION MILES"],
        "scenes": [
            {"entity_query": "Voyager 1 spacecraft", "search_query": "voyager spacecraft space NASA", "media_type": "photo", "motion": "zoom_in", "filter": "cinematic"},
            {"entity_query": "Voyager Golden Record", "search_query": "golden record space voyager", "media_type": "photo", "motion": "pan_right", "filter": "vibrant"},
            {"entity_query": "", "search_query": "deep space stars galaxy nebula", "media_type": "video", "motion": "zoom_out", "filter": "cinematic"},
            {"entity_query": "", "search_query": "sound waves glowing audio spectrum", "media_type": "video", "motion": "pan_left", "filter": "vibrant"},
            {"entity_query": "Titan IIIE Centaur rocket launch 1977", "search_query": "rocket launch night sky", "media_type": "video", "motion": "zoom_in", "filter": "cinematic"},
            {"entity_query": "", "search_query": "solar system planets space", "media_type": "video", "motion": "pan_right", "filter": "normal"},
            {"entity_query": "Pale Blue Dot Earth NASA", "search_query": "planet earth floating space", "media_type": "video", "motion": "zoom_in", "filter": "normal"}
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
        exclusion_clause = f"\nCRITICAL: DO NOT generate any topic similar to these previously used topics:\n- " + "\n- ".join(used_topic_names[-20:])

    prompt = f"""
You are an expert video director for viral YouTube Shorts (style: Vox, Alex Hormozi, MagnatesMedia).
{exclusion_clause}

Generate a high-retention viral video blueprint in JSON format:
- title: Catchy short title with emojis (under 80 characters)
- pillar: Content pillar (Science, History, Space, Mysteries)
- topic: Specific detailed topic name
- script: High-energy storytelling script that takes 40 to 45 seconds to narrate aloud at a fast, punchy short-form pace (roughly 150-175 words). Pattern interrupt hook in first 3 seconds, fascinating core facts, open loop curiosity gap, and a subscribe CTA at the end.
- description: Full SEO YouTube description with summary, key takeaways, CTA, and 5 viral hashtags (#Shorts #Science #Facts #DidYouKnow #Viral).
- stat_badges: List of 2 key numbers/stats mentioned in the script (e.g. ["17,500 MPH", "0.007 SECONDS"]). These must be real, verifiable facts - do not invent or round numbers just to sound dramatic.
- scenes: Array of 10 to 12 scene objects matching the script progression:
  - "entity_query": The SPECIFIC named person, place, object, spacecraft, document, or dated event this scene is illustrating, exactly as it would appear in an archive search (e.g. "Alexander Fleming", "Mariana Trench", "Voyager 1 spacecraft", "Apollo 11 launch 1969"). Leave this empty ("") only for a pure mood/transition shot with no real-world referent.
  - "search_query": Simple cinematic mood/filler keywords to use ONLY if no real archival photo exists for this scene (e.g. "deep space stars", "old laboratory desk", "black hole CGI")
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
# 3b. LIGHTWEIGHT FACT-CHECK PASS
# ==========================================
async def fact_check_stat_badges(topic_data):
    api_key = os.environ.get("GEMINI_API_KEY")
    badges = topic_data.get("stat_badges", [])
    if not api_key or USE_NEW_SDK is not True or not badges:
        print("⚠️ Fact-check pass skipped (no Gemini key/SDK or no stat_badges).")
        return topic_data

    print("\n🔎 Fact-checking stat badges against a live web search before locking the script...")
    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""
Fact-check these claims from a short video script about "{topic_data.get('topic', '')}".

Claims to verify: {json.dumps(badges)}
Full script for context: {topic_data.get('script', '')}

Use web search to confirm whether each claim is factually accurate. Respond ONLY with
raw JSON: a list of objects, one per claim, each with:
- "claim": the original claim text
- "accurate": true or false
- "corrected_value": if inaccurate and you found the real figure, the corrected
  string in the same style (e.g. "36,070 FEET"); otherwise null. Only correct a value
  you are confident about from search results - do not guess.
"""
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        clean_json = (response.text or "").replace("```json", "").replace("```", "").strip()
        checks = json.loads(clean_json)

        corrected = []
        any_flagged = False
        for idx, original_claim in enumerate(badges):
            match = next((c for c in checks if c.get("claim") == original_claim), None)
            if match is None and idx < len(checks):
                match = checks[idx]
            if match and match.get("accurate") is False and match.get("corrected_value"):
                print(f"   ⚠️ Flagged '{original_claim}' -> corrected to '{match['corrected_value']}'")
                corrected.append(match["corrected_value"])
                any_flagged = True
            else:
                corrected.append(original_claim)

        topic_data["stat_badges"] = corrected
        print("✅ Fact-check pass complete." + (" One or more stats were corrected." if any_flagged else " No issues found."))
    except Exception as e:
        print(f"⚠️ Fact-check pass failed, keeping original stats unverified: {e}")

    return topic_data

# ==========================================
# 4. ADVANCED KARAOKE-STYLE ASS CAPTION ENGINE
# ==========================================
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
    time_str = time_str.replace(".", ",")
    parts = time_str.split(":")
    if len(parts) == 2:
        h = 0
        m = int(parts[0])
        s_ms = parts[1]
    else:
        h = int(parts[0])
        m = int(parts[1])
        s_ms = parts[2]
    s, ms = s_ms.split(",")
    return h * 3600000 + m * 60000 + int(s) * 1000 + int(ms)

def parse_vtt_to_karaoke_ass(vtt_file_path, ass_file_path="captions.ass"):
    """Parses full VTT file and creates animated karaoke .ass subtitle bursts."""
    if not os.path.exists(vtt_file_path):
        return False

    with open(vtt_file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    time_pattern = re.compile(r'((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})\s*-->\s*((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})')
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
    accent_colors = ["&H0000D7FF", "&H0059FFAD", "&H00FF6EC7"]  # Gold, Mint Green, Pink

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

            per_word_cs = max(int((chunk_duration / len(chunk_words)) / 10), 8)
            karaoke_text = "".join(f"{{\\kf{per_word_cs}}}{w.upper()} " for w in chunk_words).strip()

            pop = r"{\fscx55\fscy55\t(0,90,\fscx112\fscy112)\t(90,150,\fscx100\fscy100)}"
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

def _write_fallback_ass(ass_path):
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write("Dialogue: 0,0:00:00.00,0:00:05.00,Burst,,0,0,0,,AUTOMATED SHORT\n")

async def generate_voiceover_and_captions(text: str, audio_path: str = "voiceover.mp3", ass_path: str = "captions.ass"):
    print("\n2️⃣ Generating Pro Voiceover & Karaoke Captions...")
    raw_vtt = "raw_captions.vtt"
    try:
        cmd = [
            "edge-tts",
            "--voice", "en-US-ChristopherNeural",
            "--rate=+8%",
            "--pitch=-2Hz",
            "--text", text,
            "--write-media", audio_path,
            "--write-subtitles", raw_vtt
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

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

def get_media_duration(path: str, fallback: float = 40.0) -> float:
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration = float(result.stdout.strip())
        if duration > 0:
            return duration
    except Exception as e:
        print(f"⚠️ Could not probe duration of {path}: {e}")
    return fallback

def pad_audio_to_minimum(input_audio: str, min_seconds: float, output_audio: str = "voiceover_padded.mp3") -> str:
    current = get_media_duration(input_audio, fallback=min_seconds)
    if current >= min_seconds:
        return input_audio
    pad_needed = min_seconds - current + 0.5
    cmd = [
        "ffmpeg", "-y", "-i", input_audio,
        "-af", f"apad=pad_dur={pad_needed}",
        output_audio
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and os.path.exists(output_audio):
        return output_audio
    return input_audio

def apply_audio_fades(input_audio: str, output_audio: str = "voiceover_faded.mp3", fade_duration: float = 0.5):
    print("2b️⃣ Applying audio fades...")
    duration = get_media_duration(input_audio, fallback=42.0)
    fade_out_start = max(duration - fade_duration, 0)
    cmd = [
        "ffmpeg", "-y", "-i", input_audio,
        "-af", f"afade=t=in:ss=0:d={fade_duration},afade=t=out:st={fade_out_start}:d={fade_duration}",
        output_audio
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("✅ Audio fades applied successfully.")
    return output_audio

# ==========================================
# 5. AUDIO ENGINE (VOICEOVER + BGM)
# ==========================================
def generate_ambient_bgm(output_bgm: str = "bgm.mp3", duration: float = 45):
    print("🎵 Synthesizing background music track...")
    fade_out_start = max(duration - 3, 0)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=120:duration={duration}",
        "-af", f"volume=0.10,lowpass=f=350,afade=t=in:ss=0:d=2,afade=t=out:st={fade_out_start}:d=3",
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
    img = Image.new("RGBA", (700, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
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
        z_expr = "min(zoom+0.0018,1.24)"
        x_expr = "iw/2-(iw/zoom/2)+2*sin(on/25)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type == "zoom_out":
        z_expr = "max(1.24-0.0018*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)-2*sin(on/25)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type == "pan_left":
        z_expr = "1.18"
        x_expr = f"(1-on/{total_frames})*(iw-iw/zoom)"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        z_expr = "1.18"
        x_expr = f"(on/{total_frames})*(iw-iw/zoom)"
        y_expr = "ih/2-(ih/zoom/2)"

    color_filter = "eq=contrast=1.1:saturation=1.2,unsharp=5:5:0.5"
    if filter_style == "vintage":
        color_filter = "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,noise=alls=4:allf=t"
    elif filter_style == "bw":
        color_filter = "hue=s=0,eq=contrast=1.25"
    elif filter_style == "vibrant":
        color_filter = "eq=contrast=1.25:saturation=1.55:gamma=1.05,unsharp=5:5:0.6"
    elif filter_style == "cinematic":
        color_filter = "curves=r='0/0 0.5/0.45 1/0.95':b='0/0.08 0.5/0.5 1/0.9',eq=contrast=1.15:saturation=1.1"

    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=1080x1920:fps={fps},"
        f"{color_filter},vignette=PI/5"
    )

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_file,
        "-vf", vf, "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", output_clip
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ==========================================
# 7b. ARCHIVAL / PRIMARY-SOURCE MEDIA
# ==========================================
ARCHIVAL_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")

def search_wikimedia_commons(query, limit=3):
    if not REQUESTS_AVAILABLE:
        return []
    try:
        url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|mime",
            "format": "json",
        }
        res = requests.get(url, params=params, timeout=10, headers={"User-Agent": "shorts-bot/1.0"})
        if res.status_code != 200:
            return []
        pages = res.json().get("query", {}).get("pages", {})
        results = []
        for page in pages.values():
            infos = page.get("imageinfo", [])
            if not infos:
                continue
            info = infos[0]
            img_url = info.get("url", "")
            mime = info.get("mime", "")
            if not img_url.lower().endswith(ARCHIVAL_IMAGE_EXTENSIONS) and not mime.startswith("image/"):
                continue
            meta = info.get("extmetadata", {})
            license_name = meta.get("LicenseShortName", {}).get("value", "unknown_license")
            results.append({
                "url": img_url,
                "source": "wikimedia_commons",
                "license": license_name,
                "title": page.get("title", query),
            })
        return results
    except Exception as e:
        print(f"   ⚠️ Wikimedia Commons search error: {e}")
        return []

def search_nasa_images(query, limit=3):
    if not REQUESTS_AVAILABLE:
        return []
    try:
        url = "https://images-api.nasa.gov/search"
        res = requests.get(url, params={"q": query, "media_type": "image"}, timeout=10)
        if res.status_code != 200:
            return []
        items = res.json().get("collection", {}).get("items", [])[:limit]
        results = []
        for item in items:
            links = item.get("links", [])
            if not links:
                continue
            img_url = links[0].get("href", "")
            if not img_url:
                continue
            title = item.get("data", [{}])[0].get("title", query)
            results.append({
                "url": img_url,
                "source": "nasa_images",
                "license": "public_domain",
                "title": title,
            })
        return results
    except Exception as e:
        print(f"   ⚠️ NASA Images search error: {e}")
        return []

def search_loc_gov(query, limit=3):
    if not REQUESTS_AVAILABLE:
        return []
    try:
        url = "https://www.loc.gov/search/"
        res = requests.get(url, params={"q": query, "fo": "json", "c": limit}, timeout=10,
                            headers={"User-Agent": "shorts-bot/1.0"})
        if res.status_code != 200:
            return []
        items = res.json().get("results", [])[:limit]
        results = []
        for item in items:
            image_url = item.get("image_url")
            if isinstance(image_url, list) and image_url:
                img_url = image_url[-1]
            elif isinstance(image_url, str):
                img_url = image_url
            else:
                continue
            if not img_url.startswith("http"):
                img_url = f"https:{img_url}" if img_url.startswith("//") else None
            if not img_url:
                continue
            results.append({
                "url": img_url,
                "source": "loc_gov",
                "license": "loc_rights_may_vary",
                "title": item.get("title", query),
            })
        return results
    except Exception as e:
        print(f"   ⚠️ Library of Congress search error: {e}")
        return []

def search_archival_media(entity_query, pillar=""):
    if not entity_query or not entity_query.strip():
        return None

    pillar_lower = (pillar or "").lower()
    source_order = []
    if "space" in pillar_lower or "nasa" in pillar_lower or "astronomy" in pillar_lower:
        source_order = [search_nasa_images, search_wikimedia_commons]
    elif "history" in pillar_lower or "mystery" in pillar_lower or "medical" in pillar_lower:
        source_order = [search_wikimedia_commons, search_loc_gov]
    else:
        source_order = [search_wikimedia_commons, search_nasa_images]

    for search_fn in source_order:
        try:
            hits = search_fn(entity_query, limit=3)
        except Exception:
            hits = []
        if hits:
            return hits[0]
    return None

def _best_pexels_video_link(video_files):
    if not video_files:
        return None
    ranked = sorted(video_files, key=lambda vf: vf.get("height", 0) * vf.get("width", 0), reverse=True)
    hd = [vf for vf in ranked if vf.get("quality") == "hd"]
    return (hd[0]["link"] if hd else ranked[0]["link"])

async def download_mixed_media_broll(scenes, clip_duration=4.0, pillar=""):
    print("\n3️⃣ Assembling Mixed-Media Scenes (Archival Sources + HD Stock + Ken Burns Motion)...")
    clips = []
    pexels_api_key = os.environ.get("PEXELS_API_KEY")

    for i, sc in enumerate(scenes, 1):
        q = sc.get("search_query", "space stars")
        entity_query = sc.get("entity_query", "")
        m_type = sc.get("media_type", "video")
        motion = sc.get("motion", "zoom_in")
        f_style = sc.get("filter", "normal")
        clip_name = f"clip_{i}.mp4"
        downloaded = False

        archival_hit = search_archival_media(entity_query, pillar=pillar) if REQUESTS_AVAILABLE else None
        if archival_hit:
            try:
                raw_img = f"raw_archival_{i}.jpg"
                img_bytes = requests.get(archival_hit["url"], timeout=20,
                                          headers={"User-Agent": "shorts-bot/1.0"}).content
                with open(raw_img, "wb") as f:
                    f.write(img_bytes)
                create_ken_burns_clip(raw_img, clip_name, motion_type=motion, filter_style=f_style, duration=clip_duration)
                downloaded = True
                sc["resolved_source"] = archival_hit["source"]
                sc["resolved_license"] = archival_hit["license"]
                sc["resolved_title"] = archival_hit.get("title", entity_query)
                print(f"   📜 [ARCHIVAL Scene {i}/{len(scenes)}] {archival_hit['source']} -> '{entity_query}' ({archival_hit['license']})")
            except Exception as err:
                print(f"   ⚠️ Archival download failed for '{entity_query}': {err}")

        if downloaded:
            clips.append(clip_name)
            continue

        if pexels_api_key and REQUESTS_AVAILABLE:
            headers = {"Authorization": pexels_api_key}
            queries_to_try = [q, " ".join(q.split()[:2]) if len(q.split()) > 2 else q]

            if m_type == "video":
                for query_attempt in queries_to_try:
                    if downloaded:
                        break
                    try:
                        url = f"https://api.pexels.com/videos/search?query={query_attempt}&per_page=5&orientation=portrait"
                        res = requests.get(url, headers=headers, timeout=10)
                        if res.status_code == 200:
                            videos = res.json().get("videos", [])
                            for video in videos:
                                best_link = _best_pexels_video_link(video.get("video_files", []))
                                if not best_link:
                                    continue
                                raw_file = f"raw_{clip_name}"
                                v_res = requests.get(best_link, timeout=20)
                                with open(raw_file, "wb") as f:
                                    f.write(v_res.content)
                                subprocess.run([
                                    "ffmpeg", "-y", "-i", raw_file,
                                    "-t", str(clip_duration), "-r", "30",
                                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,eq=contrast=1.1:saturation=1.15",
                                    "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
                                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                downloaded = True
                                sc["resolved_source"] = "pexels_video"
                                sc["resolved_license"] = "pexels_stock_license"
                                sc["resolved_title"] = query_attempt
                                print(f"   🎬 [VIDEO Scene {i}/{len(scenes)}] '{query_attempt}'")
                                break
                    except Exception as err:
                        print(f"   ⚠️ Video search error: {err}")

            if not downloaded:
                for query_attempt in queries_to_try:
                    if downloaded:
                        break
                    try:
                        photo_url = f"https://api.pexels.com/v1/search?query={query_attempt}&per_page=5&orientation=portrait"
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
                                    create_ken_burns_clip(raw_img, clip_name, motion_type=motion, filter_style=f_style, duration=clip_duration)
                                    downloaded = True
                                    sc["resolved_source"] = "pexels_photo"
                                    sc["resolved_license"] = "pexels_stock_license"
                                    sc["resolved_title"] = query_attempt
                                    print(f"   🖼️ [PHOTO Scene {i}/{len(scenes)}] Ken Burns {motion.upper()}: '{query_attempt}'")
                    except Exception as err:
                        print(f"   ⚠️ Photo search error: {err}")

        if not downloaded:
            print(f"   🎬 [SYNTHETIC Scene {i}/{len(scenes)}] '{q}'...")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c=navy:s=1080x1920:d={clip_duration}:r=30",
                "-vf", f"drawtext=text='Scene {i} - {q[:15]}':fontcolor=yellow:fontsize=45:x=(w-text_w)/2:y=(h-text_h)/2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            sc["resolved_source"] = "synthetic_placeholder"
            sc["resolved_license"] = "n/a"
            sc["resolved_title"] = q

        clips.append(clip_name)

    print(f"   ✅ {len(clips)} mixed-media scenes ready!")
    return clips

# ==========================================
# 8. MASTER COMPOSITING & RENDERING ENGINE
# ==========================================
def render_professional_short(clips, audio_file, subtitle_file, stat_badges=None,
                               clip_duration=4.0, transition_duration=TRANSITION_DURATION,
                               output_filename="final_output.mp4"):
    print("\n4️⃣ Rendering Pro Video with Crossfade Transitions & Animated Karaoke Captions...")
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
        scaled_outputs.append(f"v{i}")

    if len(scaled_outputs) == 1:
        final_video_label = f"[{scaled_outputs[0]}]"
    else:
        current_label = scaled_outputs[0]
        running_offset = clip_duration - transition_duration
        for idx in range(1, len(scaled_outputs)):
            next_label = scaled_outputs[idx]
            out_label = f"x{idx}"
            style = TRANSITION_STYLES[(idx - 1) % len(TRANSITION_STYLES)]
            filter_chains.append(
                f"[{current_label}][{next_label}]xfade=transition={style}:duration={transition_duration}:offset={running_offset}[{out_label}]"
            )
            current_label = out_label
            running_offset += clip_duration - transition_duration
        final_video_label = f"[{current_label}]"

    vignette_box = "drawbox=x=0:y=ih-450:w=iw:h=450:color=black@0.4:t=fill"

    # Animated Karaoke .ass Subtitle Filter
    subtitle_filter = f"ass=filename={subtitle_file}"

    filter_chains.append(f"{final_video_label}{vignette_box},{subtitle_filter}[vfinal]")

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

    print("⚡ Compiling pro-level video with crossfades, vignette, music & karaoke captions...")
    
    # Log file redirection prevents 30-minute OS subprocess pipe deadlock
    with open("ffmpeg_render.log", "w", encoding="utf-8") as log_file:
        result = subprocess.run(ffmpeg_cmd, stdout=log_file, stderr=log_file)

    if result.returncode != 0:
        if os.path.exists("ffmpeg_render.log"):
            with open("ffmpeg_render.log", "r", encoding="utf-8") as f:
                print(f"❌ FFmpeg Error Output:\n{f.read()[-2000:]}")
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
                print(f"   Uploading... {int(status.progress() * 100)}%")

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

    # Step 1b: Fact-check the key stats
    topic_data = await fact_check_stat_badges(topic_data)

    # Step 2: Render Stat Graphic Badges
    stat_badges = topic_data.get("stat_badges", ["17,500 MPH"])
    for i, badge in enumerate(stat_badges):
        create_stat_badge_png(badge, f"badge_{i+1}.png")

    # Step 3: Voiceover, Animated Karaoke Captions & Sound Engine
    script_text = topic_data.get("script", "")
    raw_vo, ass_captions = await generate_voiceover_and_captions(script_text)
    raw_vo = pad_audio_to_minimum(raw_vo, TARGET_MIN_SECONDS)
    faded_vo = apply_audio_fades(raw_vo)

    audio_duration = get_media_duration(faded_vo, fallback=42.0)
    audio_duration = max(TARGET_MIN_SECONDS, min(audio_duration, TARGET_MAX_SECONDS + 5))
    print(f"⏱️ Measured narration length: {audio_duration:.1f}s")

    raw_bgm = generate_ambient_bgm(duration=audio_duration + 1)
    mixed_audio = mix_voiceover_and_bgm(faded_vo, raw_bgm)

    # Step 4: Mixed Media Assembly
    scenes = topic_data.get("scenes", [])
    num_scenes = max(len(scenes), 1)
    clip_duration = (audio_duration + (num_scenes - 1) * TRANSITION_DURATION) / num_scenes
    clip_duration = max(clip_duration, 2.5)
    print(f"🎞️ Using {num_scenes} scenes at ~{clip_duration:.1f}s each with {TRANSITION_DURATION}s crossfades")

    pillar = topic_data.get("pillar", "")
    clips = await download_mixed_media_broll(scenes, clip_duration=clip_duration, pillar=pillar)

    try:
        with open("media_sources.json", "w", encoding="utf-8") as f:
            json.dump({
                "topic": topic_data.get("topic"),
                "title": topic_data.get("title"),
                "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                "scenes": [
                    {
                        "entity_query": sc.get("entity_query", ""),
                        "search_query": sc.get("search_query", ""),
                        "source": sc.get("resolved_source", "unknown"),
                        "license": sc.get("resolved_license", "unknown"),
                        "title": sc.get("resolved_title", ""),
                    }
                    for sc in scenes
                ]
            }, f, indent=2, ensure_ascii=False)
        print("🗂️ Saved media_sources.json audit trail.")
    except Exception as e:
        print(f"⚠️ Failed to save media_sources.json: {e}")

    # Step 5: Render Final Master Video
    output_video = "final_output.mp4"
    render_professional_short(
        clips, mixed_audio, ass_captions, stat_badges,
        clip_duration=clip_duration, transition_duration=TRANSITION_DURATION,
        output_filename=output_video
    )

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
