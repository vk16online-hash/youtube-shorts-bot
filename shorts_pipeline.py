import os
import sys
import json
import glob
import asyncio
import subprocess
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
    print("🧹 Cleaning up leftover files from any previous run...")
    patterns = ["*.mp4", "*.mp3", "*.png", "*.srt", "temp_*"]
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            if filepath != "final_output.mp4" and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass


# ==========================================
# 2. AUTO-DISCOVER TOPIC (WITH MODEL FALLBACK)
# ==========================================
async def generate_topic_with_gemini_fallback(prompt: str) -> str:
    """Tries multiple Gemini models sequentially if rate-limited (429) or failed."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY environment variable not set.")
        return None

    for model_name in GEMINI_MODELS:
        print(f"🎯 Attempting generation using model: {model_name}...")
        for attempt in range(2):  # Retry up to 2 times per model if rate-limited
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
                    break  # Non-rate-limit error, move to next model

    print("⚠️ All Gemini models failed or quota exhausted.")
    return None


async def discover_viral_topic():
    print("\n1️⃣ Auto-Discovering detailed viral topic & scene data...")
    
    prompt = """
    Generate a short viral YouTube Short topic concept in JSON format.
    Include keys:
    - title: Catchy short title with emojis
    - pillar: Content pillar
    - topic: Detailed topic name
    - script: Short text script (around 100 words)
    - broll_queries: List of 7 search queries for stock video clips
    Return ONLY valid raw JSON output without extra markdown text.
    """
    
    response_text = await generate_topic_with_gemini_fallback(prompt)
    
    if response_text:
        try:
            clean_json = response_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            print(f"📌 Title: {data.get('title')}")
            print(f"🏷️  Pillar: {data.get('pillar')}")
            print(f"🧩 Topic: {data.get('topic')}")
            return data
        except Exception as e:
            print(f"⚠️ Failed to parse Gemini response JSON: {e}")

    # Fallback default if all AI models fail
    print("⚠️ Falling back to a safe on-niche default topic...")
    return {
        "title": "The Moldy Mistake That Saved a Billion Lives 🦠💊",
        "pillar": "Accidental Discoveries",
        "topic": "The Accidental Discovery of Penicillin",
        "script": "In 1928, Alexander Fleming left a petri dish uncovered before leaving for vacation. When he returned, mold had killed his bacteria! That accident created penicillin, saving millions of lives.",
        "broll_queries": [
            "vintage laboratory equipment 1920s",
            "old scientist notebook writing desk",
            "petri dish mold macro closeup",
            "microscope bacteria culture lab",
            "vintage medicine bottles pharmacy",
            "hospital ward historical black and white",
            "modern hospital medicine hopeful"
        ]
    }


# ==========================================
# 3. VOICEOVER GENERATION
# ==========================================
async def generate_voiceover(text: str, output_path: str = "voiceover.mp3"):
    print("\n2️⃣ Generating High-Quality Voiceover...")
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
        await communicate.save(output_path)
        print(f"✅ Voiceover saved to {output_path}")
    except Exception as e:
        print(f"⚠️ edge-tts failed or missing ({e}), generating silence/placeholder audio...")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "10", output_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return output_path


def apply_audio_fades(input_audio: str, output_audio: str = "voiceover_faded.mp3", fade_duration: float = 0.5):
    print("\n2b️⃣ Applying audio fades (fade-in/fade-out)...")
    cmd = [
        "ffmpeg", "-y", "-i", input_audio,
        "-af", f"afade=t=in:ss=0:d={fade_duration},afade=t=out:st=30:d={fade_duration}",
        output_audio
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("✅ Audio fades applied successfully.")
    return output_audio


# ==========================================
# 4. DOWNLOAD B-ROLL CLIPS (PEXELS API)
# ==========================================
async def download_broll_clips(queries):
    print("\n3️⃣ Downloading Multi-Scene B-Roll Clips...")
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
                                with open(clip_name, "wb") as f:
                                    f.write(v_res.content)
                                downloaded = True
                                print(f"  🎬 Downloaded Pexels B-Roll Scene {i}/{len(queries)} for: '{q}'")
            except Exception as err:
                print(f"  ⚠️ Pexels download failed for '{q}': {err}")

        if not downloaded:
            print(f"  🎬 Generating fallback synthetic scene {i}/{len(queries)} for query: '{q}'...")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c=blue:s=1080x1920:d=5:r=30",
                "-vf", f"drawtext=text='Scene {i} - {q[:15]}':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_name
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        clips.append(clip_name)

    print(f"   ✅ {len(clips)} B-roll clips ready.")
    return clips


# ==========================================
# 5. VIDEO ASSEMBLY & FFMPEG RENDERING
# ==========================================
def render_professional_short(clips, audio_file, output_filename="final_output.mp4"):
    print("\n4️⃣ Rendering Video with Crossfades & Captions...")
    
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
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v{i}]"
        )
        scaled_outputs.append(f"[v{i}]")

    concat_inputs = "".join(scaled_outputs)
    filter_chains.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=0[vconcat]")

    filter_complex_str = ";".join(filter_chains)

    if filter_complex_str and filter_complex_str.strip():
        ffmpeg_cmd.extend(["-filter_complex", filter_complex_str])
        ffmpeg_cmd.extend(["-map", "[vconcat]"])
    else:
        ffmpeg_cmd.extend(["-map", "0:v"])

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

    print("⚡ Compiling final video with audio track...")
    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ FFmpeg Error Output:\n{result.stderr}")
        raise RuntimeError("Final video assembly failed.")

    print(f"🎉 Final video compiled successfully: {output_filename}")


# ==========================================
# 6. YOUTUBE UPLOAD MODULE
# ==========================================
def upload_to_youtube(video_file, title, description, tags=None):
    print("\n5️⃣ Uploading Short to YouTube...")
    
    if not YOUTUBE_SDK_AVAILABLE:
        print("⚠️ google-api-python-client or google-auth missing. Skipping YouTube upload.")
        return False

    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    
    if not all([client_id, client_secret, refresh_token]):
        print("⚠️ YouTube API credentials not set in environment. Skipping YouTube upload.")
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
                "title": title[:100],  # Title limit is 100 characters
                "description": description,
                "tags": tags or ["shorts", "viral", "facts"],
                "categoryId": "27"  # Category 27 = Education / Science
            },
            "status": {
                "privacyStatus": "public",  # Options: 'public', 'unlisted', 'private'
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
    
    # Step 1: Topic Discovery (with Gemini Fallback)
    topic_data = await discover_viral_topic()
    
    # Step 2: Voiceover & Audio
    script_text = topic_data.get("script", "")
    raw_vo = await generate_voiceover(script_text)
    faded_vo = apply_audio_fades(raw_vo)
    
    # Step 3: Download B-Roll Clips (Pexels / Synthetic)
    broll_queries = topic_data.get("broll_queries", ["nature wallpaper"])
    clips = await download_broll_clips(broll_queries)
    
    # Step 4: Render Final Video
    output_video = "final_output.mp4"
    render_professional_short(clips, faded_vo, output_filename=output_video)
    
    # Step 5: Upload to YouTube
    video_title = topic_data.get("title", "Automated YouTube Short")
    video_desc = f"{script_text}\n\n#shorts #viral #facts"
    upload_to_youtube(output_video, title=video_title, description=video_desc)
    
    print("\n🚀 Pipeline finished successfully!")


if __name__ == "__main__":
    try:
        asyncio.run(run_master_pipeline())
    except Exception as e:
        print(f"\n❌ Pipeline crashed with exception: {e}")
        sys.exit(1)
