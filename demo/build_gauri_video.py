"""Assemble real FalkorDB captures to locally transcribed human narration.

No synthetic speech, external requests, app mutations, or alteration of input.
Audio is stream-copied from the provided M4A; output length follows the recording.
"""
import argparse
import hashlib
import json
from pathlib import Path
import textwrap
import zipfile

from PIL import Image, ImageDraw

import build_video as media

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime" / "gauri-video"
REPORT = ROOT / ".runtime/browser-validation/run-1792334154142-85006/result.json"
RESTART = ROOT / ".runtime/falkordb-validation/restart_f52d862436974eacac5d30b72dbc5a9b.json"


def frame(scene, index, total, evidence):
    canvas = Image.new("RGB", (1920, 1080), "#171c1a")
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 25), "FALLOUT / " + scene["title"], font=media.font(36), fill="#e8ede9")
    draw.line((64, 80, 1856, 80), fill="#7fd1a0", width=3)
    if scene.get("image"):
        shot = Image.open(REPORT.parent / scene["image"]).convert("RGB")
        shot.thumbnail((1792, 900), Image.Resampling.LANCZOS)
        canvas.paste(shot, ((1920-shot.width)//2, 92+(900-shot.height)//2))
    else:
        lines = scene.get("lines", [])
        if scene.get("restart"):
            proof = json.loads(RESTART.read_text())
            assert proof["status"] == "PASS"
            lines = ["Graceful database restart: PASS", "Exact saved snapshots preserved"]
            for name, saved in proof["scenarios"].items():
                assert saved["verification_after_recovery"]["status"] == "PASS"
                lines += [name.title() + ": same-operation recovery PASS", "Operation: " + saved["operation_id"]]
            lines += ["RDB snapshots tested; not a crash/failover guarantee"]
        if scene.get("database"):
            assert evidence["backend"] == "falkordb"
            lines = ["ACTUAL BACKEND: FalkorDB", "Source → Fact → Summary", "Real graph nodes and lineage relationships",
                     "Versioned snapshots + revision-checked publication", "Python engine repairs / separate verifier checks"]
        y = 180
        size = 40 if len(lines) > 5 else 49
        for line in lines:
            for wrapped in textwrap.wrap(line, 76 if size == 40 else 63):
                draw.text((95, y), wrapped, font=media.font(size), fill="#e8ede9")
                y += size + 22
            y += 13
        if scene.get("source"):
            draw.text((95, 945), scene["source"], font=media.font(26), fill="#a9b5ae")
    draw.text((64, 1028), "GAURI DEO / ORIGINAL VOICE  ·  EDITED FALKORDB CAPTURES  ·  NO LIVE AI REQUEST", font=media.font(21), fill="#a9b5ae")
    draw.text((1760, 1028), "%02d / %02d" % (index, total), font=media.font(21), fill="#7fd1a0")
    output = OUT / ("frame-%02d.png" % index)
    canvas.save(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    args = parser.parse_args()
    scenes = json.loads((ROOT / "demo/gauri_timeline.json").read_text())
    evidence = media.validate_evidence(REPORT, scenes)
    assert evidence["backend"] == "falkordb"
    length = media.duration(args.audio)
    assert scenes[0]["start"] == 0
    starts = [s["start"] for s in scenes]
    assert starts == sorted(set(starts)) and starts[-1] < length
    original_hash = hashlib.sha256(args.audio.read_bytes()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, scene in enumerate(scenes):
        end = scenes[i+1]["start"] if i+1 < len(scenes) else length
        clip = OUT / ("clip-%02d.mp4" % (i+1))
        print("Rendering", i+1, scene["title"], flush=True)
        image = frame(scene, i+1, len(scenes), evidence)
        media.run([media.FFMPEG, "-y", "-loop", "1", "-framerate", "24", "-i", image,
                   "-t", str(end-scene["start"]), "-an", "-c:v", "libx264", "-preset", "fast",
                   "-tune", "stillimage", "-crf", "19", "-pix_fmt", "yuv420p", clip])
        clips.append(clip)
    concat = OUT / "clips.txt"
    concat.write_text("".join("file '%s'\n" % c.name for c in clips))
    transcription = json.loads((OUT / "transcription.json").read_text())
    subtitles = OUT / "gauri-captions.en.srt"
    cues = []
    words = [w for s in transcription["segments"] for w in s.get("words", [])]
    chunk = []
    for word in words:
        if chunk and (len(chunk) >= 9 or word["end"] - chunk[0]["start"] > 5 or
                      word["start"] - chunk[-1]["end"] > 0.7):
            cues.append({"start": chunk[0]["start"], "end": chunk[-1]["end"],
                         "text": " ".join(w["word"].strip() for w in chunk)})
            chunk = []
        chunk.append(word)
    if chunk:
        cues.append({"start": chunk[0]["start"], "end": chunk[-1]["end"],
                     "text": " ".join(w["word"].strip() for w in chunk)})
    if not cues:
        cues = transcription["segments"]
    subtitles.write_text("\n".join("%d\n%s --> %s\n%s\n" % (
        i+1, media.stamp(s["start"]), media.stamp(s["end"]), textwrap.fill(s["text"], 55))
        for i, s in enumerate(cues) if s["end"] > s["start"]))
    final = OUT / "fallout-gauri-falkordb.mp4"
    media.run([media.FFMPEG, "-y", "-f", "concat", "-safe", "1", "-i", concat, "-i", args.audio,
               "-i", subtitles, "-map", "0:v:0", "-map", "1:a:0", "-map", "2:0", "-c:v", "copy",
               "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:a:0", "language=eng",
               "-metadata:s:s:0", "language=eng", "-t", str(length), "-movflags", "+faststart", final])
    media.run([media.FFMPEG, "-v", "error", "-i", final, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
    def audio_hash(path):
        return media.run([media.FFMPEG, "-v", "error", "-i", path, "-map", "0:a:0", "-c:a", "copy",
                          "-f", "hash", "-hash", "sha256", "-"]).stdout.strip()
    assert audio_hash(final) == audio_hash(args.audio), "Original AAC audio packets must be preserved"
    assert hashlib.sha256(args.audio.read_bytes()).hexdigest() == original_hash
    manifest = {"voice": "User-supplied human recording; no synthesis", "audio_packet_hash": audio_hash(final),
                "audio_unchanged": True, "source_sha256": original_hash, "duration_seconds": length,
                "format": "Edited actual UI screenshots and explanatory evidence cards, not continuous recording",
                "backend": "falkordb", "live_ai_request": False, "decode_check": "PASS",
                "alignment": "Scene cues selected from local automatic transcription; manual audiovisual review required",
                "captions": "Automatic local transcription, unreviewed; may contain recognition errors",
                "scenes": scenes, "evidence": str(REPORT.relative_to(ROOT)),
                "restart_evidence": str(RESTART.relative_to(ROOT)), "manual_audiovisual_review": False}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (OUT / "transcript.txt").write_text("AUTOMATIC TRANSCRIPT — review before publication\n\n" +
        "\n".join("%.2f–%.2f %s" % (s["start"], s["end"], s["text"]) for s in transcription["segments"]))
    with zipfile.ZipFile(OUT / "fallout-gauri-review-kit.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in [final.name, subtitles.name, "manifest.json", "transcript.txt"]:
            archive.write(OUT / name, arcname=name)
    print("READY", final, "seconds", length, "original AAC audio preserved", flush=True)


if __name__ == "__main__":
    main()