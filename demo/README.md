# FALLOUT narrated walkthrough

This produces a **first-cut edited screenshot video**, not a continuous screen
recording. Screenshots come from native browser interactions against a separate
AI-disabled session. Narration uses the installed macOS Samantha voice; no voice
cloning or external speech service is used.

## Output

Generated media is under `../.runtime/demo-video/`:

- `fallout-narrated-demo.mp4`: 1920 × 1080 H.264 video, AAC audio, selectable English captions.
- `fallout-voiceover.wav`: separate synthetic narration.
- `fallout-demo.en.srt`: sidecar captions with approximate phrase timings.
- `transcript.md`: complete narration.
- `verification-evidence.json`: synthetic failed and recovered records from both scenarios.
- `manifest.json`: provenance, scene timing, disclosure, media hash and decode result.
- `fallout-upload-kit.zip`: only the finished media, transcript, disclosures and synthetic evidence.

Intermediate scenes are retained for editing. Never upload the browser profile,
the whole runtime directory, or private environment files.

## Reproduction

1. Keep an AI-disabled preview running on loopback port 8516. Do not replace an
   existing user's session. The browser task opens its own temporary profile.
2. Run the VS Code task **FALLOUT: simplified UI repair story**.
3. Require the new run-specific `result.json` to report PASS for story-only,
   with both pricing and documentation stories.
4. Run **FALLOUT: generate synthetic demo narration** to generate narration alone.
5. Run the video assembly task using that exact passing result. To reproduce in
   another workspace, call `demo/build_video.py` with `--result` pointing at the
   generated browser report. The builder refuses failed or incomplete stories.

Dependencies: project Python environment and Pillow; macOS `say`; workspace-local
`imageio-ffmpeg==0.6.0` under `.runtime/media-tools`. No application dependencies
or private settings are changed by the builder.

## What the recording demonstrates

Both scenarios deliberately use **Skip one rebuild but report completion**,
followed by **Retry repair without a mistake**. This is slightly different from
the earlier proposed script, which showed a clean first attempt for documentation.
The final narration explicitly describes the actual recorded sequence.

The optional AI feature is explained on a separate title card. **No live Gemini
request or response is included.** The user's working AI session is untouched.
PASS means consistency with the original synthetic dataset and withdrawal history,
not real-world truth. History is session-local, not immutable or durable.

## Industry reference

Anthropic, *Managing context on the Claude Developer Platform*, September 29, 2025:
https://claude.com/blog/context-management

This is dated industry context, not breaking October 2026 news, endorsement,
or evidence of the specific failure demonstrated here.

## Publication draft

**Title:** The Repair Said Done. The Memory Was Still Wrong. | FALLOUT

**Description:**

FALLOUT demonstrates failure-aware lineage and memory repair using two synthetic
scenarios: pricing withdrawal and technical-documentation withdrawal. Both show
read-only preview, explicit approval, deliberate false success, independent FAIL,
and same-operation recovery to PASS.

This is an edited walkthrough of actual browser captures with synthetic narration,
not an uninterrupted live recording. No live AI request is included. The prototype
uses local in-memory storage; verification establishes consistency with recorded
evidence, not real-world truth. Optional Gemini assistance is separate from the
repair engine and verifier.

Industry context: https://claude.com/blog/context-management (September 29, 2025).

## Before publishing

- Watch and listen to the entire output; automated decoding is not human review.
- Check text readability, pronunciations, and approximate caption synchronization.
- Trim or replace scenes as needed; retain the synthetic-voice and edited-capture disclosures.
- Review the exact upload kit and any repository independently for private material.
- Choose the platform, account, visibility, title and description explicitly.
- No upload or publication is performed by these scripts.