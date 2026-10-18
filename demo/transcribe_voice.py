"""Local-only transcription of the explicitly supplied narration.

Model weights may download; audio is decoded and inferred locally. Never uploads
audio. Outputs automatic captions requiring human review, not verified quotations.
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", choices=["base.en", "small.en"], default="small.en")
    args = parser.parse_args()
    from faster_whisper import WhisperModel
    root = Path(__file__).resolve().parents[1]
    out = root / ".runtime" / "gauri-video"
    out.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(args.model, device="cpu", compute_type="int8",
                         download_root=str(root / ".runtime" / "speech-models"), cpu_threads=4)
    segments, info = model.transcribe(str(args.audio), language="en", beam_size=5,
                                      word_timestamps=True, vad_filter=True,
                                      initial_prompt="FALLOUT. FalkorDB. GraphQL hackathon. Gauri Deo. Gemini. Anthropic.")
    result = []
    for segment in segments:
        row = {"start": segment.start, "end": segment.end, "text": segment.text.strip(),
               "words": [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                         for w in (segment.words or [])]}
        result.append(row)
        print("%.2f–%.2f %s" % (segment.start, segment.end, segment.text), flush=True)
    (out / "transcription.json").write_text(json.dumps({"automatic": True, "model": args.model,
        "language": info.language, "duration": info.duration, "segments": result}, indent=2))


if __name__ == "__main__":
    main()