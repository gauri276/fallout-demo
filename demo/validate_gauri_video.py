"""Validate the generated human-narrated media and its review archive locally."""
import hashlib
import json
import re
import zipfile

import build_video as media
from build_gauri_video import OUT


def main():
    video = OUT / "fallout-gauri-falkordb.mp4"
    manifest = json.loads((OUT / "manifest.json").read_text())
    assert manifest["audio_unchanged"] and manifest["decode_check"] == "PASS"
    assert manifest["backend"] == "falkordb"
    duration = media.duration(video)
    assert abs(duration - manifest["duration_seconds"]) < 0.1
    packet_hash = media.run([
        media.FFMPEG, "-v", "error", "-i", video, "-map", "0:a:0",
        "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-",
    ]).stdout.strip()
    assert packet_hash == manifest["audio_packet_hash"]
    with zipfile.ZipFile(OUT / "fallout-gauri-review-kit.zip") as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            video.name, "gauri-captions.en.srt", "manifest.json", "transcript.txt",
        }
        for name in archive.namelist():
            assert archive.read(name) == (OUT / name).read_bytes()
    blocks = (OUT / "gauri-captions.en.srt").read_text().strip().split("\n\n")
    last = 0
    for expected, block in enumerate(blocks, 1):
        lines = block.splitlines()
        assert int(lines[0]) == expected
        values = re.findall(r"(\d+):(\d+):(\d+),(\d+)", lines[1])
        start, end = [int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
                      for h, m, s, ms in values]
        assert last <= start < end <= duration, (expected, start, end)
        assert end - start <= 5.1, (expected, end - start)
        last = end
    result = media.run([
        media.FFMPEG, "-i", video, "-map", "0:a:0", "-af", "volumedetect",
        "-vn", "-sn", "-f", "null", "-",
    ])
    print("PASS: duration, archive CRC and contents, AAC hash, ordered short captions")
    print("Duration:", duration, "seconds; caption cues:", len(blocks))
    for name in [video.name, "fallout-gauri-review-kit.zip"]:
        print(name, "%.2f MiB" % ((OUT / name).stat().st_size / 1024 / 1024))
    print("Video SHA256:", hashlib.sha256(video.read_bytes()).hexdigest())
    for line in result.stderr.splitlines():
        if any(x in line for x in ["Duration:", "Stream #0:", "mean_volume:", "max_volume:"]):
            print(line)


if __name__ == "__main__":
    main()