"""Build a disclosed, narrated screenshot walkthrough; never contact the app or AI.

Uses macOS say, Pillow, and workspace-local imageio-ffmpeg. Refuses to render
UI evidence unless both story runs passed and screenshot inputs are declared.
Python 3.9 compatible. All generated artifacts stay below .runtime/demo-video.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import zipfile

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".runtime" / "media-tools"))
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
OUT = ROOT / ".runtime" / "demo-video"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def run(args):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return result


def duration(path):
    result = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True)
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not match:
        raise ValueError("No readable media duration: " + path.name)
    h, m, s = map(float, match.groups())
    return h * 3600 + m * 60 + s


def font(size):
    return ImageFont.truetype(FONT, size)


def stamp(seconds):
    milliseconds = round(seconds * 1000)
    h, rest = divmod(milliseconds, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def render_card(scene, index, evidence, source_dir):
    image = Image.new("RGB", (1920, 1080), "#171c1a")
    draw = ImageDraw.Draw(image)
    draw.text((64, 20), "FALLOUT  /  " + scene["title"], fill="#e8ede9", font=font(34))
    draw.line((64, 73, 1856, 73), fill="#7fd1a0", width=3)
    if "image" in scene:
        shot = Image.open(source_dir / scene["image"]).convert("RGB")
        shot.thumbnail((1792, 900), Image.Resampling.LANCZOS)
        image.paste(shot, ((1920 - shot.width) // 2, 88 + (900 - shot.height) // 2))
    else:
        lines = scene.get("card", [])
        if scene.get("evidence"):
            lines = []
            for story in evidence["stories"]:
                lines.extend([
                    story["scenario"].upper() + "  /  FAIL → PASS",
                    "Operation: " + story["failedOperation"]["id"],
                    "%d failed-state comparisons; %d recovered-state comparisons" % (
                        len(story["failed"]["comparisons"]), len(story["recovered"]["comparisons"])),
                    "Same operation; attempt %d → %d" % (
                        story["failedOperation"]["attempts"], story["recoveredOperation"]["attempts"]),
                ])
        y = 180 if len(lines) <= 5 else 145
        for line in lines:
            size = 48 if len(lines) <= 5 else 34
            for wrapped in textwrap.wrap(line, width=65 if size == 48 else 90):
                draw.text((105, y), wrapped, fill="#e8ede9", font=font(size))
                y += size + 25
            y += 20
        if scene.get("source"):
            draw.text((105, 940), scene["source"], fill="#a9b5ae", font=font(26))
    draw.text((64, 1023), "EDITED UI CAPTURES  |  SYNTHETIC VOICE  |  LOCAL DEMO / AI OFF", fill="#a9b5ae", font=font(22))
    draw.text((1750, 1023), "%02d / 17" % index, fill="#7fd1a0", font=font(22))
    path = OUT / ("scene-%02d.png" % index)
    image.save(path)
    return path


def validate_evidence(path, scenes):
    base = (ROOT / ".runtime" / "browser-validation").resolve()
    path = path.resolve()
    if path.name != "result.json" or base not in path.parents:
        raise ValueError("Only generated browser result.json evidence is accepted")
    evidence = json.loads(path.read_text())
    if evidence.get("status") != "PASS" or evidence.get("mode") != "story-only":
        raise ValueError("A passing story-only report is required")
    stories = evidence.get("stories", [])
    if {s["scenario"] for s in stories} != {"pricing", "documentation"}:
        raise ValueError("Both actual scenarios are required")
    for story in stories:
        assert story["failed"]["status"] == "FAIL"
        assert story["recovered"]["status"] == "PASS"
        assert story["failedOperation"]["id"] == story["recoveredOperation"]["id"]
        assert story["recoveredOperation"]["attempts"] == story["failedOperation"]["attempts"] + 1
    for scene in scenes:
        if "image" in scene:
            name = scene["image"]
            if Path(name).name != name or name not in evidence["artifacts"]:
                raise ValueError("Screenshot is not declared by the passing run")
            if not (path.parent / name).is_file():
                raise ValueError("Screenshot missing: " + name)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-only", action="store_true")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    scenes = json.loads((ROOT / "demo" / "scenes.json").read_text())
    evidence = None
    if not args.voice_only:
        if not args.result:
            parser.error("--result is required for video assembly")
        evidence = validate_evidence(args.result, scenes)
    OUT.mkdir(parents=True, exist_ok=True)
    timeline, subtitles, audio_inputs, video_inputs = [], [], [], []
    offset, cue = 0.0, 0
    for index, scene in enumerate(scenes, 1):
        print("Scene %02d: %s" % (index, scene["title"]), flush=True)
        text = OUT / ("scene-%02d.txt" % index)
        aiff = OUT / ("scene-%02d.aiff" % index)
        wav = OUT / ("scene-%02d.wav" % index)
        signature = hashlib.sha256(scene["narration"].encode()).hexdigest()
        cache = OUT / ("scene-%02d.sha256" % index)
        if not (cache.exists() and cache.read_text() == signature and wav.exists()):
            text.write_text(scene["narration"])
            run(["/usr/bin/say", "-v", "Samantha", "-r", "155", "-f", text, "-o", aiff])
            run([FFMPEG, "-y", "-i", aiff, "-af", "apad=pad_dur=1.2", "-ar", "48000", "-ac", "1", wav])
            cache.write_text(signature)
        length = duration(wav)
        audio_inputs.append(wav)
        chunks = textwrap.wrap(scene["narration"], width=105, break_long_words=False)
        total_chars = sum(len(c) for c in chunks)
        position = offset
        for chunk in chunks:
            end = position + (length - 1.2) * len(chunk) / total_chars
            cue += 1
            subtitles.append("%d\n%s --> %s\n%s\n" % (cue, stamp(position), stamp(end), textwrap.fill(chunk, 55)))
            position = end
        timeline.append({"scene": index, "title": scene["title"], "start": round(offset, 3),
                         "duration": length, "source_image": scene.get("image"), "narration": scene["narration"]})
        offset += length
        if evidence:
            frame = render_card(scene, index, evidence, args.result.parent)
            clip = OUT / ("scene-%02d.mp4" % index)
            run([FFMPEG, "-y", "-loop", "1", "-framerate", "24", "-i", frame, "-i", wav,
                 "-t", str(length), "-c:v", "libx264", "-preset", "fast", "-tune", "stillimage",
                 "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", clip])
            video_inputs.append(clip)
    srt = OUT / "fallout-demo.en.srt"
    srt.write_text("\n".join(subtitles))
    transcript = "# FALLOUT — narrated edited walkthrough\n\nSynthetic voice: macOS Samantha. Caption timings are approximate.\n\n"
    transcript += "\n\n".join("## %02d. %s\n\n%s" % (i, s["title"], s["narration"]) for i, s in enumerate(scenes, 1))
    (OUT / "transcript.md").write_text(transcript + "\n")
    listing = OUT / "audio-concat.txt"
    listing.write_text("".join("file '%s'\n" % p.name for p in audio_inputs))
    run([FFMPEG, "-y", "-f", "concat", "-safe", "1", "-i", listing, "-c:a", "pcm_s16le", OUT / "fallout-voiceover.wav"])
    manifest = {"synthetic_voice": "macOS Samantha at 155 words/minute", "format": "edited screenshots, not continuous screen recording",
                "live_ai_included": False, "duration_seconds": round(offset, 3), "scenes": timeline,
                "caption_timing": "Approximate proportional timings; not word-aligned",
                "manual_audiovisual_review": False}
    if evidence:
        listing = OUT / "video-concat.txt"
        listing.write_text("".join("file '%s'\n" % p.name for p in video_inputs))
        final = OUT / "fallout-narrated-demo.mp4"
        run([FFMPEG, "-y", "-f", "concat", "-safe", "1", "-i", listing, "-i", srt,
             "-map", "0:v:0", "-map", "0:a:0", "-map", "1:0", "-c:v", "copy", "-c:a", "copy",
             "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-movflags", "+faststart", final])
        run([FFMPEG, "-v", "error", "-i", final, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
        exported = {"status": evidence["status"], "mode": evidence["mode"], "startedAt": evidence["startedAt"],
                    "finishedAt": evidence["finishedAt"], "stories": evidence["stories"],
                    "limitations": evidence["limitations"]}
        (OUT / "verification-evidence.json").write_text(json.dumps(exported, indent=2))
        manifest.update({"browser_result": str(args.result.relative_to(ROOT)), "video_decode_check": "PASS",
                         "video_sha256": hashlib.sha256(final.read_bytes()).hexdigest(), "video_bytes": final.stat().st_size})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if evidence:
        (OUT / "README.md").write_text((ROOT / "demo" / "README.md").read_text())
        with zipfile.ZipFile(OUT / "fallout-upload-kit.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name in ["fallout-narrated-demo.mp4", "fallout-voiceover.wav", "fallout-demo.en.srt",
                         "transcript.md", "verification-evidence.json", "manifest.json", "README.md"]:
                archive.write(OUT / name, arcname=name)
    print("Generated", OUT, "duration", round(offset, 1), "seconds", flush=True)


if __name__ == "__main__":
    main()