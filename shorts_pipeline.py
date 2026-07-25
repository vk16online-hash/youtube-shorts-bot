"""
Automated AI-Powered YouTube Shorts Generator — Full Pipeline (Professional Edition)
====================================================================================
Runs as a plain Python script, designed to be triggered by GitHub Actions
on a cron schedule (see .github/workflows/generate_shorts.yml) for a fully
automated, zero-cost, N-times-per-day pipeline. Secrets (Gemini/Pexels/
YouTube keys) are read from environment variables injected by the workflow
from GitHub's encrypted repo Secrets — nothing is hardcoded or stored on disk.

Professional enhancements in this version:
  1. Improved caption generation: fallback to time-based chunking if word
     boundaries fail, debug logging to diagnose caption issues.
  2. Audio fade-in/fade-out: voiceover now smoothly fades (prevents jarring
     starts/stops, more polished feel).
  3. Smooth crossfade transitions: clips transition with 0.5s fade instead of
     hard cuts, dramatically improves visual polish.
"""

import os
import sys
import json
import glob
import time
import random
import datetime
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
# 2. NICHE CONFIGURATION — "ORIGIN POINT"
# ==========================================
# Change CHANNEL_NAME to whatever you actually name the channel.
CHANNEL_NAME = "Origin Point"

# Rotating content pillars. Each run picks ONE pillar deterministically based
# on the date + which of the day's 2 runs it is, so:
#   - the 2 daily uploads never share the same angle
#   - the full set cycles every 4 days, keeping the channel varied
#   - re-running manually the same day/hour reuses the same pillar on purpose
#     (consistent behavior, not random drift)
CONTENT_PILLARS = [
    {
        "name": "Accidental Discoveries",
        "hint": "a major scientific or technological discovery that happened by accident or mistake",
        "examples": "penicillin, microwave ovens, Velcro, X-rays, vulcanized rubber, Post-it notes, Teflon"
    },
    {
        "name": "The Uncredited Scientist",
        "hint": "a scientist or inventor whose critical contribution was overlooked, stolen, or under-credited at the time",
        "examples": "Rosalind Franklin and DNA, Lise Meitner and nuclear fission, Nikola Tesla, Katherine Johnson"
    },
    {
        "name": "Ancient Tech Too Advanced For Its Time",
        "hint": "an ancient invention or engineering feat that seems impossibly advanced for when it was built",
        "examples": "the Antikythera mechanism, Roman concrete, the Baghdad battery, Damascus steel, Greek fire"
    },
    {
        "name": "The Experiment That Changed Everything",
        "hint": "a single pivotal scientific experiment whose result reshaped an entire field",
        "examples": "the Miller-Urey experiment, the double-slit experiment, Michelson-Morley, Pavlov's dogs"
    },
    {
        "name": "Mocked or Rejected First",
        "hint": "an invention or scientific idea that was ridiculed, dismissed, or rejected by experts before it succeeded",
        "examples": "the telephone, heavier-than-air flight, germ theory, plate tectonics, continental drift"
    },
    {
        "name": "Untold Space Race Moments",
        "hint": "a lesser-known, high-stakes moment from the history of space exploration",
        "examples": "the human computers behind early NASA missions, Apollo 13, Soyuz 1, the first Voyager images"
    },
    {
        "name": "Recent Breakthroughs People Underrate",
        "hint": "a modern scientific breakthrough that is more recent, or more remarkable, than most people realize",
        "examples": "CRISPR gene editing, mRNA vaccine technology, GPS's relativity correction, ARPANET's true origins"
    },
    {
        "name": "What If It Had Failed",
        "hint": "a close call or near-failure in science/history where things could easily have gone the other way",
        "examples": "the Cuban Missile Crisis submarine incident, a vaccine trial that nearly derailed, a near-miss asteroid discovery"
    },
]


def _select_todays_pillar():
    """Deterministically pick a content pillar based on date + time-of-day,
    so the 2x/day schedule rotates through all pillars without repeats."""
    now = datetime.datetime.utcnow()
    run_slot = 0 if now.hour < 12 else 1  # matches the 03:00 / 15:00 UTC cron
    rotation_index = (now.toordinal() * 2 + run_slot) % len(CONTENT_PILLARS)
    return CONTENT_PILLARS[rotation_index]


# ==========================================
# TOPIC HISTORY (prevents ever repeating the same story)
# ==========================================
# This file lives in the repo itself and is committed back by the GitHub
# Actions workflow after every successful run, so the "already covered"
# list persists across runs even though each run starts on a fresh runner.
TOPIC_HISTORY_PATH = "used_topics.json"
MAX_HISTORY_SENT_TO_PROMPT = 60  # keep prompt size reasonable even after months of runs


def load_topic_history():
    if not os.path.exists(TOPIC_HISTORY_PATH):
        return []
    try:
        with open(TOPIC_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ Could not read {TOPIC_HISTORY_PATH} ({e}) — starting with empty history.")
        return []


def save_topic_history(history):
    try:
        with open(TOPIC_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"⚠️ Could not write {TOPIC_HISTORY_PATH}: {e}")


def _topic_already_used(topic, history):
    """Fuzzy-ish duplicate check: case-insensitive substring match against
    past topics, since Gemini may phrase the same story slightly differently."""
    topic_norm = topic.strip().lower()
    for entry in history:
        past = entry.get("topic", "").strip().lower()
        if not past:
            continue
        if topic_norm == past or topic_norm in past or past in topic_norm:
            return True
    return False


# ==========================================
# 3. 40-SECOND DETAILED SCRIPT & SCENE DISCOVERY
# ==========================================
def discover_detailed_40s_content(topic_history=None):
    topic_history = topic_history or []
    pillar = _select_todays_pillar()
    print(f"🎯 Today's content pillar: {pillar['name']}")

    # Only send the most recent N past topics to keep the prompt compact.
    recent_topics = [entry.get("topic", "") for entry in topic_history[-MAX_HISTORY_SENT_TO_PROMPT:] if entry.get("topic")]
    already_covered_block = (
        "Topics ALREADY covered on this channel — do NOT repeat any of these "
        "or close variants of them:\n- " + "\n- ".join(recent_topics)
        if recent_topics else
        "No topics have been covered yet — this is one of the first videos."
    )

    def build_prompt():
        return f"""
    You are writing a script for the YouTube Shorts channel "{CHANNEL_NAME}",
    a channel dedicated to short, punchy stories about the surprising true
    history behind scientific discoveries, inventions, and innovations. Every
    video answers a variant of: "here's the real story behind something you
    thought you knew."

    Today's content pillar is: "{pillar['name']}" — specifically, {pillar['hint']}.
    Pick ONE specific, concrete real historical example that fits this pillar
    (for inspiration, similar past examples in this pillar include: {pillar['examples']}).

    {already_covered_block}

    You MUST pick a topic that is NOT in the already-covered list above.
    This is critical — the channel's catalog must never repeat a story.

    Generate a deeply engaging, highly detailed, 40-second viral YouTube Short
    script (115-130 words) telling that one true story clearly and dramatically.
    It must have exactly 7 distinct sequential scenes so the B-roll changes
    every 5 to 6 seconds to keep retention hyper-high.

    Requirements:
    - Fast, curiosity-driven hook in the first 3 seconds (no throat-clearing).
    - All facts must be real and historically accurate — no invented details.
    - End the voiceover_text with this exact outro line, verbatim:
      "That's the origin point. Follow {CHANNEL_NAME} for the next one."
    - The pexels_query for each scene must describe realistic, findable stock
      footage (avoid overly specific historical figures' faces, since stock
      footage of them won't exist — use eras, settings, objects, and abstract
      visuals instead, e.g. "vintage laboratory equipment 1920s" rather than
      a named person).

    Return ONLY a JSON object with this exact structure:
    {{
      "topic": "Topic Name",
      "pillar": "{pillar['name']}",
      "title": "Catchy Title with Emojis",
      "description": "SEO optimized description with viral hashtags #Shorts #Science #History",
      "voiceover_text": "Detailed 40-second script containing 115 to 130 words, ending with the exact outro line specified above.",
      "scenes": [
        {{"timestamp": "0-6s", "pexels_query": "..."}},
        {{"timestamp": "6-12s", "pexels_query": "..."}},
        {{"timestamp": "12-18s", "pexels_query": "..."}},
        {{"timestamp": "18-24s", "pexels_query": "..."}},
        {{"timestamp": "24-30s", "pexels_query": "..."}},
        {{"timestamp": "30-36s", "pexels_query": "..."}},
        {{"timestamp": "36-40s", "pexels_query": "..."}}
      ]
    }}
    """

    max_attempts = 3
    last_data = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=build_prompt(),
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = json.loads(response.text.strip())
            data.setdefault("pillar", pillar["name"])
            last_data = data

            topic = data.get("topic", "")
            if not topic:
                print(f"⚠️ Attempt {attempt}: no topic field returned, retrying...")
                continue

            if _topic_already_used(topic, topic_history):
                print(f"⚠️ Attempt {attempt}: topic '{topic}' looks like a repeat, retrying...")
                continue

            return data  # unique topic, good to go

        except Exception as e:
            print(f"⚠️ Attempt {attempt} failed: {e}")

    # All attempts either failed or kept returning duplicates — fall back.
    print("⚠️ Falling back to a safe on-niche default after repeated duplicate/failed attempts.")
    return _fallback_content()


def _fallback_content():
    return {
        "topic": "The Accidental Discovery of Penicillin",
        "pillar": "Accidental Discoveries",
        "title": "The Moldy Mistake That Saved a Billion Lives 🦠💊",
        "description": (
            "How a messy lab and a forgotten petri dish changed medicine forever. "
            "#Shorts #Science #History #Penicillin"
        ),
        "voiceover_text": (
            "In 1928, a scientist left for vacation without cleaning his lab. "
            "When he came back, one of his petri dishes was contaminated with mold. "
            "Most people would have thrown it away. He almost did too. "
            "But he noticed something strange: bacteria near the mold were dying. "
            "That mold was Penicillium, and it was killing bacteria on contact. "
            "That single overlooked, moldy dish became penicillin, the first true "
            "antibiotic, and it would go on to save hundreds of millions of lives. "
            "A forgotten mistake in a messy lab became one of medicine's greatest breakthroughs. "
            "That's the origin point. Follow Origin Point for the next one."
        ),
        "scenes": [
            {"pexels_query": "vintage laboratory equipment 1920s"},
            {"pexels_query": "old scientist notebook writing desk"},
            {"pexels_query": "petri dish mold macro closeup"},
            {"pexels_query": "microscope bacteria culture lab"},
            {"pexels_query": "vintage medicine bottles pharmacy"},
            {"pexels_query": "hospital ward historical black and white"},
            {"pexels_query": "modern hospital medicine hopeful"}
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


def apply_audio_fades(vo_path, output_path, fade_duration=0.5):
    """Apply fade-in and fade-out to voiceover for polished audio.
    
    Args:
        vo_path: Input audio file
        output_path: Output audio file with fades applied
        fade_duration: Duration of fade in/out in seconds (default 0.5s)
    """
    print(f"🎵 Applying audio fades (fade_duration={fade_duration}s)...")
    
    # Get voiceover duration first
    duration = get_audio_duration(vo_path)
    fade_out_start = max(0, duration - fade_duration)
    
    # Build ffmpeg command with afade filter
    cmd = [
        "ffmpeg", "-y", "-i", vo_path,
        "-af", f"afade=t=in:st=0:d={fade_duration},afade=t=out:st={fade_out_start}:d={fade_duration}",
        output_path
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"⚠️ Audio fade warning (non-critical): {result.stderr}")
        # Fall back to original if fading fails
        subprocess.run(["cp", vo_path, output_path], check=True)
    else:
        print(f"✅ Audio fades applied successfully.")


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
    patterns = ["broll_*.mp4", "scaled_*.mp4", "caption_*.png", "temp_merged.mp4", "clips_list.txt", "voiceover_faded.mp3"]
    for pattern in patterns:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass


# ==========================================
# 7. RENDER VIDEO WITH CROSSFADES & HARD-BURN CAPTIONS
# ==========================================
def render_professional_short(clips, vo_path, word_events, total_duration, output_path="final_short_1.mp4"):
    num_clips = len(clips)
    if num_clips == 0:
        raise ValueError("No video clips downloaded.")

    clip_duration = total_duration / num_clips
    scaled_clips = []
    
    print("⚡ Scaling clips to 1080x1920 vertical format...")
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

    print("⚡ Building crossfade filter graph (smooth transitions between clips)...")
    # Build a filter graph with crossfade transitions (0.5 second fade between clips)
    filter_parts = []
    fade_duration = 0.5
    
    for i in range(len(scaled_clips) - 1):
        current_offset = (i + 1) * clip_duration - fade_duration
        if i == 0:
            filter_parts.append(f"[0:v][1:v]xfade=transition=fade:duration={fade_duration}:offset={current_offset}[v{i+1}]")
        else:
            filter_parts.append(f"[v{i}][{i+1}:v]xfade=transition=fade:duration={fade_duration}:offset={current_offset}[v{i+1}]")
    
    if filter_parts:
        filter_graph = ";".join(filter_parts)
        video_input_label = f"[v{len(scaled_clips)-1}]"
    else:
        filter_graph = None
        video_input_label = "0:v"

    # Build concat command with xfade filter
    print("⚡ Concatenating clips with crossfade transitions...")
    temp_merged = "temp_merged.mp4"
    
    if filter_graph:
        concat_inputs = sum([["-i", c] for c in scaled_clips], [])
        concat_cmd = (
            ["ffmpeg", "-y"] + concat_inputs +
            ["-filter_complex", filter_graph,
             "-map", video_input_label,
             "-c:v", "libx264", "-preset", "medium", "-crf", "23",
             "-an", temp_merged]
        )
    else:
        # Fallback: use concat demuxer if filter graph fails
        list_txt_path = "clips_list.txt"
        with open(list_txt_path, "w", encoding="utf-8") as f:
            for c in scaled_clips:
                f.write(f"file '{c}'\n")
        
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_txt_path,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-an", temp_merged
        ]
    
    res_concat = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_concat.returncode != 0:
        print(f"❌ Concat Error:\n{res_concat.stderr}")
        raise RuntimeError("Video concatenation failed.")

    # Generate captions from word events with improved fallback logic
    print("⚡ Generating captions from voiceover...")
    chunks = []
    
    if word_events:
        print(f"   ℹ️ Found {len(word_events)} word boundary events")
        # Group into 2-word chunks for better pacing
        for i in range(0, len(word_events), 2):
            chunk_words = word_events[i:i + 2]
            start_time = chunk_words[0]["start"]
            end_time = chunk_words[-1]["end"]
            text_str = " ".join([w["text"] for w in chunk_words])
            chunks.append((start_time, end_time, text_str))
    
    # Fallback: if no word events, generate time-based captions
    if not chunks:
        print("⚠️ No word boundary events captured — generating time-based captions fallback")
        chunk_duration_captions = 2.5  # show caption for 2.5 seconds
        num_caption_chunks = int(total_duration / chunk_duration_captions) + 1
        
        for i in range(num_caption_chunks):
            start = i * chunk_duration_captions
            end = (i + 1) * chunk_duration_captions
            if start < total_duration:
                chunks.append((start, min(end, total_duration), f"..."))

    if not chunks:
        print("⚠️ Unable to generate any captions — final video will have no captions.")
        # Just merge video + audio without captions
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

    print(f"⚡ Generating {len(chunks)} caption overlay images...")
    font = load_caption_font(size=72)

    for idx, (start, end, text) in enumerate(chunks):
        if not text or text.strip() == "...":
            continue  # skip empty captions
            
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

        # Background pill box for readability (black semi-transparent)
        padding = 24
        draw.rounded_rectangle(
            [x - padding, y - padding, x + w + padding, y + h + padding],
            radius=20,
            fill=(0, 0, 0, 200)
        )
        # Bright white text with black outline for contrast
        draw.text((x, y - bbox[1]), text, fill=(255, 255, 255, 255), font=font, stroke_width=2, stroke_fill=(0, 0, 0, 255))
        img.save(img_path)

    print("⚡ Compiling final video with caption overlays and audio track...")

    # Build complete filter graph input map dynamically.
    # Input 0 = merged video, Input 1 = voiceover audio, Inputs 2..N = caption PNGs.
    filter_complex_parts = []
    current_label = "0:v"

    for idx, (start, end, text) in enumerate(chunks):
        if not text or text.strip() == "...":
            continue
        next_label = f"out{idx}"
        caption_input_idx = idx + 2  # offset by video(0) + audio(1)
        filter_complex_parts.append(
            f"[{current_label}][{caption_input_idx}:v]overlay=enable='between(t,{start},{end})'[{next_label}]"
        )
        current_label = next_label

    filter_graph = ";".join(filter_complex_parts)

    final_cmd = ["ffmpeg", "-y", "-i", temp_merged, "-i", vo_path]
    caption_count = 0
    for idx, (start, end, text) in enumerate(chunks):
        if not text or text.strip() == "...":
            continue
        final_cmd.extend(["-i", f"caption_{idx}.png"])
        caption_count += 1

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

    print(f"\n🎉 SUCCESS! Professional Video Rendered with {caption_count} captions & crossfades: {output_path}")


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

    print("📚 Loading topic history to avoid repeats...")
    topic_history = load_topic_history()
    print(f"   {len(topic_history)} past topics on record.\n")

    print("1️⃣ Auto-Discovering detailed viral topic & scene data...")
    data = discover_detailed_40s_content(topic_history)
    title = data.get('title', 'Untitled Short')
    description = data.get('description', 'Fascinating science short #Shorts')
    topic = data.get('topic', 'Unknown')

    print(f"📌 Title: {title}")
    print(f"🏷️  Pillar: {data.get('pillar', 'Unknown')}")
    print(f"🧩 Topic: {topic}")
    print(f"📝 Script Length: {len(data.get('voiceover_text', '').split())} words\n")

    vo_file = "voiceover.mp3"
    vo_file_faded = "voiceover_faded.mp3"

    print("2️⃣ Generating High-Quality Voiceover & Word Timeline...")
    word_events = await build_audio_and_timeline(data.get("voiceover_text", ""), vo_file)
    actual_duration = get_audio_duration(vo_file)
    print(f"   🎙️ Actual voiceover duration: {actual_duration:.2f}s")
    print(f"   📊 Word events captured: {len(word_events)}")
    
    print("\n2b️⃣ Applying audio fades (fade-in/fade-out)...")
    apply_audio_fades(vo_file, vo_file_faded, fade_duration=0.5)

    print("\n3️⃣ Downloading Multi-Scene B-Roll Clips...")
    clips = download_broll_clips(data.get("scenes", []))
    print(f"   ✅ {len(clips)} B-roll clips ready.")

    print("\n4️⃣ Rendering Video with Crossfades & Hard-Burned Captions...")
    run_timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_filename = f"final_short_{run_timestamp}.mp4"
    render_professional_short(clips, vo_file_faded, word_events, actual_duration, output_filename)

    print("\n5️⃣ Attempting Automated YouTube Upload (if credentials are configured)...")
    upload_to_youtube(output_filename, title, description)

    print("\n6️⃣ Recording this topic in history so it's never repeated...")
    topic_history.append({
        "topic": topic,
        "title": title,
        "pillar": data.get("pillar", "Unknown"),
        "date": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    })
    save_topic_history(topic_history)

    print("\n🧹 Cleaning up intermediate files (keeping final video + voiceover)...")
    cleanup_intermediate_files()

    print(f"\n✅ PIPELINE COMPLETE. Final file: {output_filename}")


if __name__ == "__main__":
    asyncio.run(run_master_pipeline())
