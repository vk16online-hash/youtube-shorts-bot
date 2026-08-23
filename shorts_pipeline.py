import os
import sys
import json
import random
import time
import re
import asyncio
import subprocess
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

# Google GenAI & YouTube API imports
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import edge_tts

# ---------------------------------------------------------------------------
# CONFIGURATION & CONSTANTS
# ---------------------------------------------------------------------------
WIDTH, HEIGHT = 1080, 1920
FPS = 30
MAX_SCENE_DURATION = 8.0
DEFAULT_TIMEOUT = 15  # Network request timeout in seconds
FFMPEG_TIMEOUT = 180   # FFmpeg process timeout in seconds

# Updated active models based on latest API specs
MODEL_CANDIDATES = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite"
]

FALLBACK_TOPICS = [
    {
        "topic": "The Volcano That Erased an Ancient City in Seconds 🌋 Pompeii",
        "script": "In 79 AD, Mount Vesuvius erupted with the force of 100,000 atomic bombs. Within seconds, a surge of superheated ash rolled down at 400 miles per hour, instantly burying the Roman city of Pompeii under 20 feet of debris.",
        "badges": [{"label": "YEAR", "value": "79 AD"}, {"label": "ASH DEPTH", "value": "20 FEET"}],
        "keywords": ["volcano eruption", "ancient rome", "pompeii ruins", "ash cloud", "archaeology", "ancient city"]
    },
    {
        "topic": "The Deepest Hole Humans Ever Dug 🕳️ Kola Superdeep",
        "script": "Scientists in Russia spent 20 years drilling the Kola Superdeep Borehole, reaching over 7.5 miles into the Earth. At the bottom, temperatures reached 356 degrees Fahrenheit—hot enough to turn rock into plastic.",
        "badges": [{"label": "DEPTH", "value": "7.5 MILES"}, {"label": "TEMP", "value": "356°F"}],
        "keywords": ["deep hole", "earth core", "drilling rig", "geology", "extreme temperature", "science experiment"]
    }
]

# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------
def safe_request_get(url, headers=None, params=None, stream=False, timeout=DEFAULT_TIMEOUT):
    """Executes requests.get with an enforced timeout to prevent network hangs."""
    try:
        response = requests.get(url, headers=headers, params=params, stream=stream, timeout=timeout)
        response.raise_for_status()
        return response
    except Exception as e:
        print(f"⚠️ Network request failed ({url}): {e}")
        return None

def run_ffmpeg_command(cmd, timeout=FFMPEG_TIMEOUT):
    """Runs FFmpeg via subprocess with explicit timeout and buffer capture."""
    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True
        )
        if process.returncode != 0:
            print(f"⚠️ FFmpeg Error:\n{process.stderr[-500:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"❌ FFmpeg process timed out after {timeout} seconds!")
        return False
    except Exception as e:
        print(f"❌ Subprocess execution error: {e}")
        return False

def generate_placeholder_image(filename, text="Shorts Media"):
    """Generates a simple fallback card if stock media download fails."""
    img = Image.new('RGB', (WIDTH, HEIGHT), color=(20, 24, 33))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, WIDTH-40, HEIGHT-40], outline=(100, 110, 140), width=4)
    img.save(filename)
    print(f"🖼️ Generated fallback placeholder image: {filename}")

# ---------------------------------------------------------------------------
# STEP 1: AI SCRIPT & CONCEPT GENERATOR
# ---------------------------------------------------------------------------
def generate_concept():
    print("1️⃣ AI Director Brainstorming Viral Concept & Blueprint...")
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if api_key and genai:
        client = genai.Client(api_key=api_key)
        prompt = (
            "Generate a highly engaging 40-second viral YouTube Short script on an astonishing history or science fact. "
            "Return JSON matching this format: "
            "{\"topic\": \"...\", \"script\": \"...\", \"badges\": [{\"label\": \"...\", \"value\": \"...\"}], \"keywords\": [\"kw1\", \"kw2\", \"kw3\", \"kw4\", \"kw5\", \"kw6\"]}"
        )
        
        for model in MODEL_CANDIDATES:
            print(f"🎯 Attempting generation using model: {model}...")
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                if response and response.text:
                    data = json.loads(response.text)
                    print(f"✅ Concept generated using {model}: {data.get('topic')}")
                    return data
            except Exception as e:
                print(f"⚠️ Model {model} failed: {e}")
    
    print("⚠️ Gemini API unavailable or exhausted. Selecting fallback concept...")
    chosen = random.choice(FALLBACK_TOPICS)
    print(f"📌 Selected Fallback Topic: {chosen['topic']}")
    return chosen

# ---------------------------------------------------------------------------
# STEP 2: VOICE & CAPTIONS ENGINE
# ---------------------------------------------------------------------------
async def create_voiceover(text, voice="en-US-AndrewNeural"):
    print("2️⃣ Generating Voiceover & Captions...")
    communicate = edge_tts.Communicate(text, voice)
    submaker = edge_tts.SubMaker()
    
    with open("voiceover.mp3", "wb") as file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                file.write(chunk["data"])
            elif chunk["type"] == "Word":
                submaker.feed(chunk)
                
    # Generate WebVTT format captions for modern edge-tts compatibility
    vtt_content = submaker.generate_subs()
    with open("captions.vtt", "w", encoding="utf-8") as f:
        f.write(vtt_content)
    print("✅ Voiceover (voiceover.mp3) and Captions (captions.vtt) generated.")

def measure_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprintwrappers=1:nokey=1", file_path]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        return float(res.stdout.strip())
    except Exception:
        return 40.0

# ---------------------------------------------------------------------------
# STEP 3: MEDIA RETRIEVAL & MOTION
# ---------------------------------------------------------------------------
def fetch_media(keywords, num_scenes=6):
    print("3️⃣ Assembling Mixed-Media Scenes...")
    pexels_key = os.environ.get("PEXELS_API_KEY")
    headers = {"Authorization": pexels_key} if pexels_key else {}
    scene_files = []

    for i in range(num_scenes):
        kw = keywords[i % len(keywords)]
        filename = f"scene_{i}.jpg"
        downloaded = False
        
        if pexels_key:
            url = f"https://api.pexels.com/v1/search?query={kw}&per_page=1&orientation=portrait"
            res = safe_request_get(url, headers=headers, timeout=10)
            if res:
                try:
                    photos = res.json().get("photos", [])
                    if photos:
                        img_url = photos[0]["src"]["portrait"]
                        img_res = safe_request_get(img_url, stream=True, timeout=15)
                        if img_res:
                            with open(filename, "wb") as f:
                                for chunk in img_res.iter_content(8192):
                                    f.write(chunk)
                            downloaded = True
                except Exception as e:
                    print(f"⚠️ Error parsing Pexels image for '{kw}': {e}")
        
        if not downloaded:
            generate_placeholder_image(filename, text=kw.upper())
            
        scene_files.append(filename)

    return scene_files

def render_ken_burns_scene(input_img, output_mp4, duration):
    """Applies pan/zoom to static image safely using FFmpeg with timeout."""
    zoom_cmd = (
        f"zoompan=z='min(zoom+0.0015,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={int(duration*FPS)}:s={WIDTH}x{HEIGHT}:fps={FPS}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", input_img,
        "-vf", f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,{zoom_cmd}",
        "-t", str(duration),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        output_mp4
    ]
    return run_ffmpeg_command(cmd, timeout=60)

# ---------------------------------------------------------------------------
# STEP 4: VIDEO ASSEMBLY & EDITING
# ---------------------------------------------------------------------------
def build_final_video(scenes, total_duration):
    print("4️⃣ FFmpeg Video Rendering...")
    scene_duration = total_duration / len(scenes)
    rendered_clips = []

    for i, img in enumerate(scenes):
        clip_path = f"clip_{i}.mp4"
        print(f"🎬 Processing Scene {i+1}/{len(scenes)} ({scene_duration:.1f}s)...")
        success = render_ken_burns_scene(img, clip_path, scene_duration)
        if success:
            rendered_clips.append(clip_path)

    if not rendered_clips:
        raise RuntimeError("❌ All scene renders failed.")

    with open("concat_list.txt", "w") as f:
        for clip in rendered_clips:
            f.write(f"file '{clip}'\n")

    # Combine video clips with audio and burn VTT subtitles
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", "concat_list.txt",
        "-i", "voiceover.mp3",
        "-vf", "subtitles=captions.vtt",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "final_output.mp4"
    ]
    
    print("🚀 Rendering final HD video file...")
    if run_ffmpeg_command(cmd, timeout=FFMPEG_TIMEOUT):
        print("✅ Video rendered successfully: final_output.mp4")
        return True
    else:
        raise RuntimeError("❌ Final video concatenation failed.")

# ---------------------------------------------------------------------------
# STEP 5: YOUTUBE AUTOMATED UPLOAD
# ---------------------------------------------------------------------------
def upload_to_youtube(concept_data):
    print("5️⃣ Uploading to YouTube...")
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    if not (client_id and client_secret and refresh_token):
        print("⚠️ YouTube credentials missing in environment secrets. Skipping upload.")
        return

    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret
        )
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": concept_data["topic"][:100],
                "description": f"{concept_data['script']}\n\n#Shorts #History #Science #Facts",
                "tags": concept_data.get("keywords", ["Shorts"]),
                "categoryId": "27"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload("final_output.mp4", chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"⬆️ Upload progress: {int(status.progress() * 100)}%")

        print(f"🎉 YouTube Short uploaded successfully! Video ID: {response.get('id')}")

    except Exception as e:
        print(f"❌ YouTube upload failed: {e}")

# ---------------------------------------------------------------------------
# MAIN PIPELINE EXECUTION
# ---------------------------------------------------------------------------
def main():
    print("✅ Autonomous AI Director Pipeline Initialized!")
    
    # 1. Concept Generation
    concept = generate_concept()

    # 2. Voiceover & Captions
    asyncio.run(create_voiceover(concept["script"]))
    audio_duration = measure_audio_duration("voiceover.mp3")
    print(f"⏱️ Audio duration measured: {audio_duration:.2f}s")

    # 3. Fetch Media
    scenes = fetch_media(concept.get("keywords", ["history"]), num_scenes=6)

    # 4. Render Video
    build_final_video(scenes, audio_duration)

    # 5. Upload to YouTube
    upload_to_youtube(concept)

    # Save topic history
    try:
        history = []
        if os.path.exists("used_topics.json"):
            with open("used_topics.json", "r") as f:
                history = json.load(f)
        history.append(concept["topic"])
        with open("used_topics.json", "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not write topic history: {e}")

if __name__ == "__main__":
    main()
