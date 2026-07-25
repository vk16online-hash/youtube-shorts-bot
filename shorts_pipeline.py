import os
import sys
import json
import glob
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

# --- Gemini SDK Setup ---
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
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
    patterns = ["*.mp4", "*.mp3", "*.png", "*.srt", "temp_*"]
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
    """Loads previously generated topics to prevent duplicates."""
    if os.path.exists(TOPIC_HISTORY_FILE):
        try:
            with open(TOPIC_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to read {TOPIC_HISTORY_FILE}: {e}")
    return []

def save_used_topic(topic_title, topic_name, pillar):
    """Appends newly generated topic to history file."""
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
# 3. AUTO-DISCOVER TOPIC (WITH AI DEDUPLICATION)
# ==========================================
async def generate_topic_with_gemini_fallback(prompt: str) -> str:
    """Tries multiple Gemini models sequentially if rate-limited (429) or failed."""
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
                    print("❌ No Gemini SDK installed (`google-genai` or `google-generativeai`).")
                    return None

            except Exception as err:
                err_msg = str(err)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    print(f"⚠️ Model {model_name} hit rate limit (429). Waiting 10 seconds before retrying...")
                    await asyncio.sleep(10)
                else:
                    print(f"⚠️ Model {model_name} failed with error: {err_msg}")
                    break

    print("⚠️ All Gemini models failed or quota exhausted.")
    return None


async def discover_viral_topic():
    print("\n1️⃣ Auto-Discovering detailed 40-second viral topic & scene data...")
    
    used_topics = load_used_topics()
    used_topic_names = [t.get("topic") for t in used_topics if t.get("topic")]
    
    exclusion_clause = ""
    if used_topic_names:
        exclusion_clause = f"\nCRITICAL: DO NOT generate any topic that is similar to these previously used topics:\n- " + "\n- ".join(used_topic_names[-20:])
    
    prompt = f"""
    Generate a high-retention viral YouTube Short topic concept in JSON format.
    {exclusion_clause}

    Include keys:
    - title: Catchy short title with emojis (under 80 characters)
    - pillar: Content pillar (e.g. Science, Unsolved Mysteries, History, Space)
    - topic: Specific detailed topic name
    - script: Rich, detailed storytelling script between 180 and 220 words (targeting around 40 seconds when spoken). Include a powerful hook, fascinating details, and a quick call-to-action.
    - broll_queries: List of 10 search queries for stock footage matching each section of the script.
    
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
            
            # Save topic to avoid repeats in future runs
            save_used_topic(data.get('title'), data.get('topic'), data.get('pillar'))
            return data
        except Exception as e:
            print(f"⚠️ Failed to parse Gemini response JSON: {e}")

    # Fallback default if AI fails
    print("⚠️ Falling back to a safe detailed default topic...")
    default_data = {
        "title": "The Strange Phenomenon That Solved Deep Space 🌌 Fleeting Time",
        "pillar": "Cosmic Science",
        "topic": "Einstein Time Dilation Mystery",
        "script": "Did you know that time doesn't run at the same speed for everyone? According to Albert Einstein's theory of relativity, gravity and high speed actually slow down time itself. Astronauts aboard the International Space Station orbit Earth at 17,500 miles per hour. Because of this extreme speed, they actually age slightly slower than everyone on Earth! After six months in space, astronauts return to Earth roughly 0.007 seconds younger than their twin siblings who stayed behind. But it gets even crazier. If you fell into a supermassive black hole, gravity would stretch time so intensely that one minute near the event horizon could equal 70 years back on Earth. Science is stranger than fiction. Subscribe for more cosmic secrets!",
        "broll_queries": [
            "deep space stars galaxy",
            "albert einstein historical physics",
            "international space station orbit earth",
            "astronaut floating zero gravity",
            "clock ticking fast motion",
            "black hole cosmic singularity",
            "gravity warping spacetime",
            "twin paradox time dilation",
            "galaxy nebula macro cinematic"
        ]
    }
    save_used_topic(default_data['title'], default_data['topic'], default_data['pillar'])
    return default_data


# ==========================================
# 4. VOICEOVER & WORD-SYNCED CAPTIONS
# ==========================================
async def generate_voiceover_and_captions(text: str, audio_path: str = "voiceover.mp3", srt_path: str = "captions.srt"):
    print("\n2️⃣ Generating High-Quality Voiceover & Word-Synced Captions...")
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
        submaker = edge_tts.SubMaker()
        
        with open(audio_path, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    submaker.feed(chunk)
                    
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(submaker.get_srt())
            
        print(f"✅ Voiceover saved to {audio_path}")
        print(f"✅ Subtitles generated and saved to {srt_path}")
    except Exception as e:
        print(f"⚠️ edge-tts failed or missing ({e}), generating silence & empty subtitles...")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "40", audio_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:05,000\nAutomated Video\n")

    return audio_path, srt_path


def apply_audio_fades(input_audio: str, output_audio: str = "voiceover_faded.mp3", fade_duration: float = 0.5):
    print("\n2b️⃣ Applying audio fades...")
    cmd = [
        "ffmpeg", "-y", "-i", input_audio,
        "-af", f"afade=t=in:ss=0:d={fade_duration},afade=t=out:st=38:d={fade_duration}",
        output_audio
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("✅ Audio fades applied successfully.")
    return output_audio


# ==========================================
# 5. DOWNLOAD HIGH-RETENTION B-ROLL CLIPS
# ==========================================
async def download_broll_clips(queries):
    print("\n3️⃣ Downloading Multi-Scene B-Roll Clips (3-4s Fast Cuts)...")
    clips = []
    pexels_api_key = os.environ.get("PEXELS_API_KEY")

    for i, q in enumerate(queries, 1):
        clip_name = f"clip_{i}.mp4"
        downloaded = False

        if pexels_api_key:
            try:
                import requests
                headers = {"Authorization": pexels_api_key}
                url = f"https://api.pexels.com/videos/search?query={q}&per_page=1&orientation=portrait"
                res = requests.get(url, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    videos = data.get("videos", [])
                    if videos:
                        video_files = videos[0].get("video_files", [])
                        best_file = None
                        for vf in video_files:
                            if vf.get("quality") == "hd":
                                best_file = vf.get("link")
                                break
                        if not best_file and video_files:
                            best_file = video_files[0].get("link")

                        if best_file:
                            v_res = requests.get(best_file, timeout=20)
                            if v_res.status_code == 200:
                                with open(f"raw_{clip_name}", "wb") as f:
                                    f.write(v_res.content)
                                # Trim to 4 seconds for fast retention cuts
                                subprocess.run([
                                    "ffmpeg", "-y", "-i", f"raw_{clip_name}",
                                    "-t", "4", "-c", "copy", clip_name
                                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                downloaded = True
                                print(f"  🎬 Downloaded Pexels Scene {i}/{len(queries)} for: '{q}'")
            except Exception as err:
                print(f"  ⚠️ Pexels download failed for '{q}': {err}")

        if not downloaded:
            print(f"  🎬 Generating fallback synthetic scene {i}/{len(queries)} for: '{q}'...")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c=navy:s=1080x1920:d=4:r=30",
                "-vf", f"drawtext=text='Scene {i} - {q[:15]}':fontcolor=yellow:fontsize=45:x=(w-text_w)/2:y=(h-text_h)/2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        clips.append(clip_name)

    print(f"   ✅ {len(clips)} fast-cut B-roll clips ready.")
    return clips


# ==========================================
# 6. ADVANCED VIDEO RENDERING WITH CAPTIONS
# ==========================================
def render_professional_short(clips, audio_file, subtitle_file, output_filename="final_output.mp4"):
    print("\n4️⃣ Rendering Advanced Video with Burnt-In Captions...")
    
    if not clips:
        raise ValueError("No video clips available for rendering.")

    ffmpeg_cmd = ["ffmpeg", "-y"]
    for clip in clips:
        ffmpeg_cmd.extend(["-i", clip])
    
    ffmpeg_cmd.extend(["-i", audio_file])
    
    filter_chains = []
    scaled_outputs = []

    # Scale, crop, and apply color enhancement to each B-roll clip
    for i in range(len(clips)):
        filter_chains.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,eq=contrast=1.1:saturation=1.2[v{i}]"
        )
        scaled_outputs.append(f"[v{i}]")

    # Concatenate clips
    concat_inputs = "".join(scaled_outputs)
    filter_chains.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=0[vconcat]")

    # Burn-in stylish subtitles (Bold Yellow/White text centered near bottom)
    subtitle_filter = (
        f"subtitles={subtitle_file}:force_style="
        "'Fontname=DejaVu Sans,Fontsize=22,PrimaryColour=&H0000FFFF&,"
        "OutlineColour=&H00000000&,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=180'"
    )
    filter_chains.append(f"[vconcat]{subtitle_filter}[vfinal]")

    filter_complex_str = ";".join(filter_chains)
    ffmpeg_cmd.extend(["-filter_complex", filter_complex_str])
    ffmpeg_cmd.extend(["-map", "[vfinal]"])

    # Map audio track (last input)
    audio_stream_index = len(clips)
    ffmpeg_cmd.extend(["-map", f"{audio_stream_index}:a"])

    # High-quality encoding settings
    ffmpeg_cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        output_filename
    ])

    print("⚡ Compiling final video with captions and audio...")
    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ FFmpeg Error Output:\n{result.stderr}")
        raise RuntimeError("Final video assembly failed.")

    print(f"🎉 Final video compiled successfully: {output_filename}")


# ==========================================
# 7. YOUTUBE UPLOAD MODULE
# ==========================================
def upload_to_youtube(video_file, title, description, tags=None):
    print("\n5️⃣ Uploading Short to YouTube...")
    
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
    print("✅ Master Pipeline Initialized Successfully!\n")
    
    cleanup_temp_files()
    
    # Step 1: Topic Discovery (Deduplicated against channel history)
    topic_data = await discover_viral_topic()
    
    # Step 2: Voiceover & Subtitles
    script_text = topic_data.get("script", "")
    raw_vo, srt_captions = await generate_voiceover_and_captions(script_text)
    faded_vo = apply_audio_fades(raw_vo)
    
    # Step 3: B-Roll Clips (3-4s fast cuts)
    broll_queries = topic_data.get("broll_queries", ["nature wallpaper"])
    clips = await download_broll_clips(broll_queries)
    
    # Step 4: Render Final Video with Burnt-in Captions
    output_video = "final_output.mp4"
    render_professional_short(clips, faded_vo, srt_captions, output_filename=output_video)
    
    # Step 5: Upload to YouTube
    video_title = topic_data.get("title", "Automated YouTube Short")
    video_desc = f"{script_text}\n\n#shorts #viral #facts #science"
    upload_to_youtube(output_video, title=video_title, description=video_desc)
    
    print("\n🚀 Pipeline finished successfully!")


if __name__ == "__main__":
    try:
        asyncio.run(run_master_pipeline())
    except Exception as e:
        print(f"\n❌ Pipeline crashed with exception: {e}")
        sys.exit(1)
