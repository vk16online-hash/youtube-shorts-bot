"""
Automated AI-Powered YouTube Shorts Generator — Full Pipeline (Fixed)
======================================================================
Runs as a plain Python script, designed to be triggered by GitHub Actions
on a cron schedule (see .github/workflows/generate_shorts.yml) for a fully
automated, zero-cost, N-times-per-day pipeline. Secrets (Gemini/Pexels/
YouTube keys) are read from environment variables injected by the workflow
from GitHub's encrypted repo Secrets — nothing is hardcoded or stored on disk.

Fixes applied vs. original draft:
  1. Broken markdown-style URLs (Pexels search, OAuth token URI, final YouTube
     link) replaced with plain valid URL strings.
  2. Script completed — was truncated mid-call with no output_path, no upload
     step, and no execution entrypoint.
  3. Captions now use a real TTF font at a legible size instead of
     ImageFont.load_default() (which renders ~10px and is unreadable on a
     1080x1920 canvas). Falls back gracefully if no TTF is found.
  4. Video length is now sized to the ACTUAL measured voiceover duration
     (via ffprobe) instead of a hardcoded 40.0s, so audio and video no
     longer drift/clip against each other.
  5. Intermediate files (broll_*, scaled_*, caption_*, temp_merged.mp4,
     clips_list.txt) are cleaned up before and after each run.
  6. B-roll download now retries with a generic fallback query if a scene's
     specific query returns nothing, and the pipeline aborts with a clear
     error if too few clips end up downloaded (instead of silently
     producing a video with missing scenes).
  7. General hardening: clearer error messages, guards against zero-chunk
     caption lists, safer subprocess handling.
"""

import os
import sys
import json
import glob
import time
import asyncio
import subprocess
import requests
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types
import edge_tts

# YouTube API Libraries
try:
    import google.oauth2.credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    HAS_YOUTUBE_LIBS = True
except ImportError:
    HAS_YOUTUBE_LIBS = False

# ==========================================
# 1. INITIALIZATION & SECRETS
# ==========================================
# Secrets are injected as environment variables by the GitHub Actions workflow
# (see .github/workflows/generate_shorts.yml), which pulls them from the
# repo's encrypted Settings > Secrets and variables > Actions store.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

if not GEMINI_API_KEY or not PEXELS_API_KEY:
    print("❌ Missing required secrets: GEMINI_API_KEY and/or PEXELS_API_KEY.")
    print("   Set them in your GitHub repo under Settings > Secrets and variables > Actions.")
    sys.exit(1)

YT_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")

client = genai.Client(api_key=GEMINI_API_KEY)
print("✅ Master Pipeline Initialized Successfully!\n")

MIN_REQUIRED_CLIPS = 4  # abort if fewer than this many B-roll clips download successfully


# ==========================================
# 2. 40-SECOND DETAILED SCRIPT & SCENE DISCOVERY
# ==========================================
def discover_detailed_40s_content():
    prompt = """
    Generate a deeply engaging, highly detailed, 40-second viral YouTube Short script (115-130 words) focusing on a mind-bending space, deep earth, or advanced physics mystery.
    It must have exactly 7 distinct sequential scenes so the B-roll changes every 5 to 6 seconds to keep retention hyper-high.

    Return ONLY a JSON object with this exact structure:
    {
      "topic": "Topic Name",
      "title": "Catchy Title with Emojis",
      "description": "SEO optimized description with viral hashtags #Shorts #Science",
      "voiceover_text": "Detailed 40-second script containing 115 to 130 words. Fast hook in the first 3 seconds.",
      "scenes": [
        {"timestamp": "0-6s", "pexels_query": "deep space galaxy rotation cinematic"},
        {"timestamp": "6-12s", "pexels_query": "quantum physics glowing particle energy"},
        {"timestamp": "12-18s", "pexels_query": "massive black hole accretion disk"},
        {"timestamp": "18-24s", "pexels_query": "futuristic warp speed space travel"},
        {"timestamp": "24-30s", "pexels_query": "subatomic particle collision abstract"},
        {"timestamp": "30-36s", "pexels_query": "supernova cosmic explosion shockwave"},
        {"timestamp": "36-40s", "pexels_query": "mysterious universe cosmos infinity"}
      ]
    }
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(response.text.strip())
    except Exception as e:
        print(f"⚠️ API Fallback: {e}")
        return {
            "title": "The Terrifying Physics of Time Dilation ⏳🌌",
            "description": "How time slows down near the speed of light. #Science #Shorts #Physics",
            "voiceover_text": "Did you know time actually runs slower the faster you move through space? If you boarded a rocket ship and traveled near the speed of light for just five years, when you finally returned to Earth, centuries would have passed by! Your friends, your family, and everyone you ever knew would be long gone. This isn't science fiction, it's proven physics governed by Einstein's theory of relativity. Gravity and speed warp the very fabric of spacetime itself. The universe has rules so bizarre, they completely break our everyday reality!",
            "scenes": [
                {"pexels_query": "futuristic space rocket launch cinematic"},
                {"pexels_query": "clock ticking time warp space"},
                {"pexels_query": "earth view from deep space orbit"},
                {"pexels_query": "albert einstein physics chalkboard abstract"},
                {"pexels_query": "spacetime gravitational wave distortion"},
                {"pexels_query": "glowing galaxy cosmic nebula rotation"}
            ]
        }


# ==========================================
# 3. BUILD AUDIO & WORD TIMELINE
# ==========================================
async def build_audio_and_timeline(text, vo_path):
    communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
    word_events = []

    with open(vo_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                word_events.append({
                    "text": chunk['text'].strip().upper(),
                    "start": chunk['offset'] / 10_000_000.0,
                    "end": (chunk['offset'] + chunk['duration']) / 10_000_000.0
                })
    return word_events


def get_audio_duration(vo_path):
    """Return exact duration (seconds) of the rendered voiceover via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        vo_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not determine audio duration via ffprobe: {result.stderr}")


# ==========================================
# 4. DOWNLOAD HIGH-RES VERTICAL B-ROLL
# ==========================================
def download_broll_clips(scenes):
    headers = {"Authorization": PEXELS_API_KEY}
    downloaded = []
    fallback_query = "abstract cinematic background loop"

    def try_download(query, idx):
        url = "https://api.pexels.com/videos/search"
        params = {"query": query, "per_page": 3, "orientation": "portrait"}
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10).json()
            if res.get("videos"):
                files = res["videos"][0]["video_files"]
                best_link = next((f["link"] for f in files if f.get("height", 0) >= 1080), files[0]["link"])
                clip_path = f"broll_{idx}.mp4"
                with open(clip_path, "wb") as f:
                    f.write(requests.get(best_link, timeout=15).content)
                return clip_path
        except Exception as e:
            print(f"    ⚠️ Request failed for '{query}': {e}")
        return None

    for i, scene in enumerate(scenes):
        query = scene.get("pexels_query", "space cosmos")
        print(f"  🎬 Downloading B-Roll Scene {i+1}/{len(scenes)} for query: '{query}'...")

        clip_path = try_download(query, i)
        if clip_path is None:
            print(f"    ↪️ No results for '{query}', retrying with fallback query...")
            clip_path = try_download(fallback_query, i)

        if clip_path:
            downloaded.append(clip_path)
        else:
            print(f"    ❌ Failed to get any clip for scene {i+1}, skipping.")

    if len(downloaded) < MIN_REQUIRED_CLIPS:
        raise RuntimeError(
            f"Only {len(downloaded)} B-roll clips downloaded successfully "
            f"(minimum required: {MIN_REQUIRED_CLIPS}). Aborting to avoid a broken video. "
            f"Check your PEXELS_API_KEY and network connectivity."
        )

    return downloaded


# ==========================================
# 5. CAPTION FONT LOADING
# ==========================================
def load_caption_font(size=72):
    """Try a handful of common Linux/Kaggle TTF paths; fall back to default bitmap font."""
    candidate_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/kaggle/working/DejaVuSans-Bold.ttf",  # if user manually uploads one
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    print("⚠️ No TTF font found on disk — captions will fall back to a tiny default font. "
          "Install 'fonts-dejavu-core' (apt-get) or upload a .ttf for legible captions.")
    return ImageFont.load_default()


# ==========================================
# 6. CLEANUP HELPERS
# ==========================================
def cleanup_intermediate_files():
    patterns = ["broll_*.mp4", "scaled_*.mp4", "caption_*.png", "temp_merged.mp4", "clips_list.txt"]
    for pattern in patterns:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass


# ==========================================
# 7. RENDER VIDEO & HARD-BURN CAPTIONS SAFELY (PIL Image Overlay Method)
# ==========================================
def render_professional_short(clips, vo_path, word_events, total_duration, output_path="final_short_1.mp4"):
    num_clips = len(clips)
    if num_clips == 0:
        raise ValueError("No video clips downloaded.")

    clip_duration = total_duration / num_clips
    scaled_clips = []
    for i, clip in enumerate(clips):
        out_c = f"scaled_{i}.mp4"
        cmd_scale = [
            "ffmpeg", "-y", "-i", clip,
            "-t", str(clip_duration),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-an", out_c
        ]
        res_scale = subprocess.run(cmd_scale, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_scale.returncode != 0:
            print(f"❌ Scaling Error on clip {i}:\n{res_scale.stderr}")
            raise RuntimeError(f"Clip scaling failed for {clip}")
        scaled_clips.append(out_c)

    # Write concat list safely with UTF-8 encoding
    list_txt_path = "clips_list.txt"
    with open(list_txt_path, "w", encoding="utf-8") as f:
        for c in scaled_clips:
            f.write(f"file '{c}'\n")

    temp_merged = "temp_merged.mp4"
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_txt_path,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-an",
        temp_merged
    ]
    res_concat = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_concat.returncode != 0:
        print(f"❌ Concat Demuxer Error:\n{res_concat.stderr}")
        raise RuntimeError("Video concatenation failed.")

    # Group words into clean 3-word display chunks
    chunks = []
    for i in range(0, len(word_events), 3):
        chunk_words = word_events[i:i + 3]
        start_time = chunk_words[0]["start"]
        end_time = chunk_words[-1]["end"]
        text_str = " ".join([w["text"] for w in chunk_words])
        chunks.append((start_time, end_time, text_str))

    if not chunks:
        print("⚠️ No word-boundary events captured — final video will have no captions.")
        # Just copy merged video + audio together with no overlay.
        final_cmd = [
            "ffmpeg", "-y", "-i", temp_merged, "-i", vo_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            output_path
        ]
        res = subprocess.run(final_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(f"❌ FFmpeg Error:\n{res.stderr}")
            raise RuntimeError("Final video assembly failed.")
        print(f"\n🎉 SUCCESS! Video Rendered (no captions): {output_path}")
        return

    print("⚡ Generating caption overlay images...")
    font = load_caption_font(size=72)

    for idx, (start, end, text) in enumerate(chunks):
        img_path = f"caption_{idx}.png"

        # Create transparent canvas matching vertical short size (1080x1920)
        img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw a clean high-contrast caption box near the bottom center
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]

        x = (1080 - w) / 2
        y = 1450

        # Background pill box for readability
        padding = 24
        draw.rounded_rectangle(
            [x - padding, y - padding, x + w + padding, y + h + padding],
            radius=20,
            fill=(0, 0, 0, 200)
        )
        # Bright yellow text matching viral short styles
        draw.text((x, y - bbox[1]), text, fill=(255, 255, 0, 255), font=font)
        img.save(img_path)

    print("⚡ Compiling final video with image overlays and audio track...")

    # Build complete filter graph input map dynamically.
    # Input 0 = merged video, Input 1 = voiceover audio, Inputs 2..N = caption PNGs.
    filter_complex_parts = []
    current_label = "0:v"

    for idx, (start, end, text) in enumerate(chunks):
        next_label = f"out{idx}"
        caption_input_idx = idx + 2  # offset by video(0) + audio(1)
        filter_complex_parts.append(
            f"[{current_label}][{caption_input_idx}:v]overlay=enable='between(t,{start},{end})'[{next_label}]"
        )
        current_label = next_label

    filter_graph = ";".join(filter_complex_parts)

    final_cmd = ["ffmpeg", "-y", "-i", temp_merged, "-i", vo_path]
    for idx in range(len(chunks)):
        final_cmd.extend(["-i", f"caption_{idx}.png"])

    final_cmd.extend([
        "-filter_complex", filter_graph,
        "-map", f"[{current_label}]",
        "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_path
    ])

    res = subprocess.run(final_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"❌ FFmpeg Error:\n{res.stderr}")
        raise RuntimeError("Final video assembly failed.")

    print(f"\n🎉 SUCCESS! Professional Video Rendered: {output_path}")


# ==========================================
# 8. AUTOMATED YOUTUBE SHORTS UPLOADER
# ==========================================
def upload_to_youtube(video_path, title, description):
    if not (HAS_YOUTUBE_LIBS and YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        print("ℹ️ YouTube credentials not found in secrets. Skipping automatic upload (video saved locally).")
        return False

    try:
        print("🚀 Initiating YouTube Shorts Auto-Upload...")
        creds = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=YT_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=YT_CLIENT_ID,
            client_secret=YT_CLIENT_SECRET
        )

        youtube = build("youtube", "v3", credentials=creds)
        body = {
            'snippet': {
                'title': title[:100],
                'description': description,
                'tags': ["Shorts", "Science", "Physics", "Space", "Viral"],
                'categoryId': '28'
            },
            'status': {
                'privacyStatus': 'unlisted',
                'selfDeclaredMadeForKids': False
            }
        }

        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype='video/mp4')
        request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"  └─ Upload Progress: {int(status.progress() * 100)}%")

        video_id = response.get('id')
        print(f"🎉 YouTube Upload Complete! Watch/Manage here: https://youtu.be/{video_id}")
        return True
    except Exception as e:
        print(f"⚠️ YouTube Upload failed: {e}")
        return False


# ==========================================
# 9. EXECUTE FULL MASTER PIPELINE
# ==========================================
async def run_master_pipeline():
    print("🧹 Cleaning up leftover files from any previous run...")
    cleanup_intermediate_files()

    print("\n1️⃣ Auto-Discovering detailed viral topic & scene data...")
    data = discover_detailed_40s_content()
    title = data.get('title', 'Untitled Short')
    description = data.get('description', 'Fascinating science short #Shorts')

    print(f"📌 Title: {title}")
    print(f"📝 Script Length: {len(data.get('voiceover_text', '').split())} words\n")

    vo_file = "voiceover.mp3"

    print("2️⃣ Generating High-Quality Voiceover & Word Timeline...")
    word_events = await build_audio_and_timeline(data.get("voiceover_text", ""), vo_file)
    actual_duration = get_audio_duration(vo_file)
    print(f"   🎙️ Actual voiceover duration: {actual_duration:.2f}s")

    print("3️⃣ Downloading Multi-Scene B-Roll Clips...")
    clips = download_broll_clips(data.get("scenes", []))
    print(f"   ✅ {len(clips)} B-roll clips ready.")

    print("4️⃣ Rendering Video with Hard-Burned Captions (synced to actual audio length)...")
    run_timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_filename = f"final_short_{run_timestamp}.mp4"
    render_professional_short(clips, vo_file, word_events, actual_duration, output_filename)

    print("5️⃣ Attempting Automated YouTube Upload (if credentials are configured)...")
    upload_to_youtube(output_filename, title, description)

    print("\n🧹 Cleaning up intermediate files (keeping final video + voiceover)...")
    cleanup_intermediate_files()

    print(f"\n✅ PIPELINE COMPLETE. Final file: {output_filename}")


if __name__ == "__main__":
    asyncio.run(run_master_pipeline())
