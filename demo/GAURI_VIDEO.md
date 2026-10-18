# FALLOUT — Gauri's FalkorDB walkthrough

This version uses Gauri's supplied human recording, **not synthetic speech**.
The full recording is approximately **5:07**. Its AAC audio packets are copied
without speeding up, trimming, normalizing, or replacing the voice. The original
recording remains unchanged. No audio is uploaded for transcription.

## Visuals and evidence

- 24 timestamp-aligned scenes: actual FalkorDB-backed browser screenshots and
  explanatory cards, not an uninterrupted screen recording.
- Both pricing and documentation show deliberate false success, independent
  FAIL, then recovery to PASS using the same operation ID.
- The database restart card uses a passing graceful RDB restart test. It is
  **not** a state reset and does not establish crash/failover durability.
- The Gemini card explains the optional feature. No live AI request is shown.
- PASS establishes consistency with original synthetic evidence and withdrawal
  history, not truth about the real world.
- FalkorDB is the graph database; this project does not expose a GraphQL API.

The original in-memory, synthetic-voice video remains separate and unchanged.

## Generated files

Outputs are kept under `.runtime/gauri-video/`:

- `fallout-gauri-falkordb.mp4`: 1080p H.264, original AAC, selectable captions.
- `gauri-captions.en.srt`: automatic local word-timestamped captions.
- `transcript.txt`: raw automatic transcript for review.
- `manifest.json`: provenance, timeline, audio hash, and validation results.
- `fallout-gauri-review-kit.zip`: finished video, captions, transcript, and
  manifest only. No credentials or browser profiles are included.

## Review before publication

The captions are **unreviewed automatic recognition**, not an authoritative
transcript. In particular, review names such as FalkorDB, the retry explanation
around 2:45, and the closing phrases around 4:50–5:05. Do not mistake recognition
errors for confirmed spoken wording. Captions are selectable, not burned in.

Watch and listen to the whole video to check scene alignment, text readability,
voice playback, and captions. Automated decoding and audio packet matching do
not constitute human audiovisual review. No publication or upload is performed.

## Reproduction

Use the existing **FALLOUT: transcribe Gauri narration locally** task, then
**FALLOUT: render Gauri FalkorDB video**. The isolated `.runtime/voice-tools`
environment contains faster-whisper, Pillow, and imageio-ffmpeg. Only model
weights are downloaded; recognition runs locally. The application environment
and private configuration are not used by the media tasks.

`demo/gauri_timeline.json` controls scene timing. `demo/build_gauri_video.py`
requires the recorded passing FalkorDB browser evidence and restart evidence;
it neither contacts nor mutates the running application.

Industry reference: Anthropic, *Managing context on the Claude Developer
Platform*, September 29, 2025: https://claude.com/blog/context-management.
This is dated context, not breaking news or endorsement.