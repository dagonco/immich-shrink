# immich-shrink

Re-encode the big videos in your [Immich](https://immich.app) library to 1080p AV1 and swap them in place, keeping albums, favorites, dates and location.

Immich never touches your originals: it keeps every file exactly as uploaded, and at most adds a transcoded copy next to it. That is the right default, but action cameras and phones record 4K or 2.7K at 60 fps and 45–60 Mbps, which is **about 450 MB per minute** of video. For home videos you only want to watch, that is a lot of disk.

`immich-shrink` runs on your Mac, converts those videos with SVT-AV1 and replaces them in Immich one by one. On the author's library it replaced **286 videos, from 174 GB down to 56 GB (−68 %)**.

```bash
immich-shrink library --dry-run         # what would be converted
immich-shrink library --limit 2         # try it on two videos first
immich-shrink library --until 08:00     # overnight, stop before morning
```

## How it works

**`library` mode** looks for videos above 1080p, or 1080p above 20 Mbps, biggest first. For each one it:

1. downloads the original,
2. converts it to AV1 at 1080p, keeping the frame rate, the bit depth and HDR,
3. uploads the new file with the same dates,
4. copies albums, favorite, shared links, stack, description and rating onto it,
5. sends the original to Immich's **trash**, where it stays until the trash is emptied (30 days by default).

Every step is written to a log, so a rerun skips what is done and finishes any replacement that was interrupted after the upload. If the new file is not at least 15 % smaller, the original is kept and the video is never retried.

**`folder` mode** is for videos you have not uploaded yet. It never touches the source folder; it mirrors it into a new one with the videos converted and everything else (photos, small or already-AV1 videos) copied as-is, ready for you to upload into the right album.

```bash
immich-shrink folder ~/Videos/Iceland ~/Videos/Iceland-av1
```

## Requirements

- macOS, ideally on Apple Silicon. It uses the Mac's hardware to decode, and `caffeinate` / `taskpolicy` to stay awake and run at low priority. Linux falls back to plain software decoding and `nice`, but is untested.
- Python 3.11+ (standard library only).
- ffmpeg with SVT-AV1 (`brew install ffmpeg`) and curl.
- Immich (tested with v3.2) with the **trash enabled**. `immich-shrink` refuses to run without it, because the originals would be deleted for good.

## Install

```bash
git clone https://github.com/dagonco/immich-shrink.git
ln -s "$PWD/immich-shrink/immich-shrink" ~/.local/bin/immich-shrink   # any folder on your PATH
```

## Configure

Create an API key in Immich (Account Settings → API Keys). It needs to read, download, upload, update, copy and delete assets, and to read albums; granting all permissions is the simplest. Then create `~/.config/immich-shrink/config.toml`:

```toml
url = "http://192.168.1.10:2283"
api_key = "your-api-key"
# Optional: never touch videos in albums whose name contains any of these
keep_albums = ["Wedding"]
```

`IMMICH_URL` and `IMMICH_API_KEY` environment variables override the file; `--url` and `--keep-album` override both. The log of replacements lives next to the config, in `replaced.jsonl`.

## Running it

| Option | What it does |
|---|---|
| `--dry-run` | Show what would be converted and change nothing |
| `--limit N` | Stop after N videos |
| `--until HH:MM` | Do not start new videos after this time |
| `--gentle` | Low priority: slower, but you will not notice it while you work |

**Ctrl+C** stops; only the video in progress is lost and it is redone next time. **Ctrl+Z** pauses and `fg` resumes. Without `--gentle` it uses every core: the author's M5 converts 4K60 GoPro footage at about 2.3× real time.

## What is kept and what is lost

| Kept | Lost |
|---|---|
| Capture date and time zone | Resolution above 1080p |
| GPS location (phones) and GoPro telemetry | Camera make/model in some files (GoPro) |
| Rotation of vertical videos | Faces must be detected again (Immich does it on its own) |
| Frame rate, audio | |
| 10-bit and HDR (HLG/PQ), as 10-bit AV1 | |
| Albums, favorite, shared links, stack, description, rating | |

Live/motion photo videos are never touched.

## Immich server settings

Two settings decide whether your server undoes the work by transcoding the new files again. Both are in Administration → Settings → Video Transcoding:

- **Accepted video codecs:** add **HEVC** and **AV1**. Otherwise every replaced video is transcoded again on the server.
- **10-bit and HDR videos** (most recent phones) are transcoded by Immich even when their codec is accepted, and playback then uses that 720p copy instead of your 1080p HDR file. If the devices you watch on play HEVC and AV1 (recent phones, current browsers), set the **transcode policy to not transcode** at all.

## Why these settings

AV1 (SVT-AV1, preset 8, CRF 28) was chosen by measuring GoPro footage with VMAF against the original scaled to 1080p:

| Encoder | Size saved | VMAF mean | VMAF worst 1 % | Speed (M5) |
|---|---|---|---|---|
| Hardware HEVC (VideoToolbox, q70) | 4 % | 96.6 | 91.0 | 4.3× |
| x265 medium, CRF 18 | 22 % | 94.2 | 87.3 | 0.1× |
| x265 medium, CRF 21 | 52 % | 90.0 | 81.8 | 0.5× |
| **SVT-AV1 preset 8, CRF 28** | **37 %** | **94.8** | **85.2** | **2.1×** |
| SVT-AV1 preset 8, CRF 34 | 64 % | 90.3 | 76.7 | 2.2× |

That sample was hard, fast-moving 1080p/2.7K action. On a broader mix of 4K and 2.7K clips the same AV1 setting saved 65 % at VMAF 96.3. The Mac's hardware encoder is fast but barely shrinks action footage; x265 needs a lot more time for the same quality. Halving the frame rate to 30 fps would save another ~35 %, but the original frame rate was kept on purpose. All of this is in the constants at the top of the script if you want to tune it.

## License

[MIT](LICENSE)
