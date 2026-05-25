#!/usr/bin/env python3
# ==============================================================================
# download.py – Version 3.7
#   - Fixed subtitle detection: directly checks for the expected .srt file
#     after conversion, instead of relying on run_cmd_live new‑file logic.
#   - Burn logic: if burn is on, the .srt is deleted after burning;
#     if off, both video and .srt are uploaded.
#   - Kept --js-runtimes node, --cookies-from-browser, --verbose.
# ==============================================================================

import os, sys, time, subprocess, base64, requests, json, re, traceback
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone

URLS_JSON   = os.environ.get("URLS_JSON", "[]").strip()
QUALITY     = os.environ.get("QUALITY", "720p")
SUB_LANG    = os.environ.get("SUB_LANG", "none").strip()
BURN_SUBS   = os.environ.get("BURN_SUBS", "false").lower() == "true"
COMPRESS    = os.environ.get("COMPRESS_VIDEO", "false").lower() == "true"
FOLDER_NAME = os.environ.get("FOLDER_NAME", "Download")
TOKEN       = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
REPO        = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH      = os.environ.get("GITHUB_REF_NAME", "main")
COOKIES_FILE = os.environ.get("COOKIES_FILE", "")

LOG_FILE    = "logs/download.log"
os.makedirs("logs", exist_ok=True)

def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def run_cmd_live(cmd, timeout=600):
    before = set(os.listdir("."))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        stripped = line.rstrip('\n')
        if stripped:
            log(stripped)
        else:
            log("")
    proc.wait(timeout=timeout)
    after = set(os.listdir("."))
    new_files = after - before
    return proc.returncode, new_files

def upload_to_github(local_path, remote_path):
    log(f"⬆️  Uploading {Path(local_path).name} ...")
    if not TOKEN:
        log("❌ No GH_TOKEN or GITHUB_TOKEN provided")
        return False
    url = f"https://api.github.com/repos/{REPO}/contents/{quote(remote_path)}"
    headers = {"Authorization": f"token {TOKEN}"}
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            sha = resp.json().get("sha") if resp.status_code == 200 else None
            with open(local_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            body = {
                "message": f"Add {Path(local_path).name}",
                "content": b64,
                "branch": BRANCH
            }
            if sha: body["sha"] = sha
            resp_put = requests.put(url, headers=headers, json=body, timeout=60)
            if resp_put.status_code in (200, 201):
                log(f"   ✅ Uploaded (HTTP {resp_put.status_code})")
                return True
            log(f"   ⚠️  Upload attempt {attempt} HTTP {resp_put.status_code}: {resp_put.text[:200]}")
        except Exception as exc:
            log(f"   ⚠️  Upload attempt {attempt} exception: {exc}")
        time.sleep(5)
    log(f"   ❌ Upload failed after 3 attempts")
    return False

def is_youtube_url(url: str) -> bool:
    return "youtube.com/watch" in url or "youtu.be/" in url

def download_youtube(url, quality, sub_lang, burn_subs, compress_video, cookies_file=None):
    log(f"📹 YouTube: {url}")
    quality_map = {
        "144p":  "bestvideo[height<=144][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=144]+bestaudio/best[height<=144]",
        "360p":  "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio/best[height<=360]",
        "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]",
        "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",
        "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "best":  "best"
    }
    fmt = quality_map.get(quality, quality_map["720p"])

    ytdlp_base = [
        "--js-runtimes", "node",
        "--geo-bypass", "--geo-bypass-country", "US",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "--retries", "10", "--fragment-retries", "10",
        "--sleep-requests", "1", "--sleep-interval", "5", "--max-sleep-interval", "15",
        "--verbose"
    ]

    profile_path = "/tmp/chrome_profile/Default"
    if os.path.isdir(profile_path):
        ytdlp_base.extend(["--cookies-from-browser", f"chrome:{profile_path}"])
    else:
        log("   ℹ️ Chrome profile not found – continuing without cookies")

    output_template = "%(title).150B [%(id)s].%(ext)s"
    video_cmd = ["yt-dlp", "--no-playlist", "--format", fmt, "--merge-output-format", "mp4",
                 "--output", output_template] + ytdlp_base + [url]
    ok, new_files = run_cmd_live(video_cmd)
    video_file = None
    for f in sorted(new_files):
        if not f.endswith((".part", ".srt", ".ytdl", ".log", ".tmp")) and os.path.exists(f):
            video_file = f
            break
    if not video_file:
        log("❌ YouTube download failed")
        return False, None, None
    size_mb = os.path.getsize(video_file) / 1024**2
    log(f"   📦 Downloaded: {video_file} ({size_mb:.1f} MB)")

    subtitle_file = None
    if sub_lang != "none":
        requested_code = sub_lang.split()[0]
        langs_to_try = [requested_code]
        if requested_code == "en":
            langs_to_try.append("fa")
        elif requested_code == "fa":
            langs_to_try.append("en")

        for lang_code in langs_to_try:
            # Clean up any leftover current_sub* files so the new one is always detected
            for leftover in os.listdir("."):
                if leftover.startswith("current_sub"):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass

            log(f"   📝 Attempting subtitle download for '{lang_code}' ...")
            sub_cmd = ["yt-dlp", "--no-playlist", "--skip-download",
                       "--write-subs", "--write-auto-subs",
                       "--sub-lang", lang_code, "--convert-subs", "srt",
                       "--output", "current_sub.%(ext)s"] + ytdlp_base
            sub_cmd.append(url)
            # We don't need the return value; we will check for the file directly
            run_cmd_live(sub_cmd)

            expected_srt = f"current_sub.{lang_code}.srt"
            if os.path.exists(expected_srt) and os.path.getsize(expected_srt) > 0:
                video_base = os.path.splitext(video_file)[0]
                new_srt_name = video_base + ".srt"
                if os.path.exists(new_srt_name):
                    os.remove(new_srt_name)
                os.rename(expected_srt, new_srt_name)
                subtitle_file = new_srt_name
                log(f"   ✅ Subtitle downloaded: {subtitle_file}")
                break
            else:
                log(f"   ⚠️ Subtitle '{lang_code}' not available.")
        if not subtitle_file:
            log("   ℹ️ No subtitle could be downloaded for any language.")

    # Handle burn / compression
    if burn_subs and subtitle_file:
        # Burn subtitles into video, then delete the .srt
        normalized = f"burned_{video_file}"
        cmd_ffmpeg = ["ffmpeg", "-y", "-i", video_file,
                      "-vf", f"subtitles='{os.path.abspath(subtitle_file)}':force_style='FontName=Arial,FontSize=18'",
                      "-c:a", "copy", normalized]
        log(f"🎞️  Burning subtitles: {' '.join(cmd_ffmpeg)}")
        try:
            subprocess.run(cmd_ffmpeg, check=True, capture_output=True, text=True)
            os.remove(video_file)
            os.rename(normalized, video_file)
            log(f"   ✅ Subtitles burned into video")
            # Delete the separate .srt since it's now embedded
            os.remove(subtitle_file)
            subtitle_file = None
        except subprocess.CalledProcessError as e:
            log(f"   ❌ Burn failed: {e.stderr.strip()[-300:]}")
            if os.path.exists(normalized):
                os.remove(normalized)

    if compress_video:
        compressed = f"compressed_{video_file}"
        cmd_ffmpeg = ["ffmpeg", "-y", "-i", video_file, "-c:v", "libx264", "-crf", "26", "-preset", "medium", compressed]
        log(f"🎞️  Compressing: {' '.join(cmd_ffmpeg)}")
        try:
            subprocess.run(cmd_ffmpeg, check=True, capture_output=True, text=True)
            os.remove(video_file)
            os.rename(compressed, video_file)
            new_size = os.path.getsize(video_file) / 1024**2
            log(f"   ✅ Compressed – new size {new_size:.1f} MB")
        except subprocess.CalledProcessError as e:
            log(f"   ❌ Compression failed: {e.stderr.strip()[-300:]}")
            if os.path.exists(compressed):
                os.remove(compressed)

    return True, video_file, subtitle_file

def download_direct(url, idx):
    log(f"🔗 Direct link: {url}")
    filename = os.path.basename(url).split("?")[0] or f"download_{idx+1}.bin"
    temp_file = f"temp_download_{idx}"
    http_code = subprocess.run(
        ["curl", "-L", "-o", temp_file, "-w", "%{http_code}",
         "-H", "User-Agent: Mozilla/5.0", "--max-time", "120", url],
        capture_output=True, text=True
    ).stdout.strip() or "0"

    if http_code.isdigit() and 200 <= int(http_code) < 400 and os.path.exists(temp_file) and os.path.getsize(temp_file) > 0:
        with open(temp_file, "rb") as f:
            head = f.read(200)
        if b"<!DOCTYPE html" in head.lower() or b"<html" in head.lower():
            log("   → Got HTML, will try browser fallback")
            os.remove(temp_file)
        else:
            os.rename(temp_file, filename)
            size_mb = os.path.getsize(filename) / 1024**2
            log(f"✅ Downloaded {filename} ({size_mb:.1f} MB)")
            return True, filename

    log("   → Trying Playwright browser")
    with open("pending_urls.txt", "w") as f:
        f.write(url + "\n")
    try:
        subprocess.run(["node", "downloader.js"], check=False, timeout=120)
    except Exception as e:
        log(f"   ⚠️ Browser fallback error: {e}")
    finally:
        try:
            os.remove("pending_urls.txt")
        except OSError:
            pass

    for f in sorted(os.listdir("."), key=lambda x: os.path.getmtime(x), reverse=True):
        if os.path.isfile(f) and not f.endswith((".part", ".log", ".py", ".yml", ".js", ".txt", ".sh")):
            size_mb = os.path.getsize(f) / 1024**2
            log(f"✅ Browser downloaded {f} ({size_mb:.1f} MB)")
            return True, f
    log("❌ Direct download failed")
    return False, None

def main():
    try:
        try:
            urls = json.loads(URLS_JSON)
        except Exception:
            log("❌ Invalid JSON in URLS_JSON input.")
            sys.exit(1)

        if not urls:
            log("❌ No URLs provided.")
            sys.exit(1)

        log(f"🚀 Processing {len(urls)} URL(s) – Quality: {QUALITY}, Subtitles: {SUB_LANG}, Burn: {BURN_SUBS}, Compress: {COMPRESS}")
        out_dir = Path(FOLDER_NAME)
        out_dir.mkdir(exist_ok=True)

        CHUNK_SIZE = 20 * 1024 * 1024
        large_files = []
        total_uploaded = 0

        for idx, url in enumerate(urls):
            log("")
            log(f"{'='*50}  URL #{idx+1}  {'='*50}")
            log(f"📥 {url}")

            if is_youtube_url(url):
                ok, video_file, subtitle_file = download_youtube(url, QUALITY, SUB_LANG, BURN_SUBS, COMPRESS)
                if not ok:
                    continue
                if os.path.getsize(video_file) < CHUNK_SIZE:
                    if upload_to_github(video_file, f"{FOLDER_NAME}/{video_file}"):
                        total_uploaded += 1
                        os.remove(video_file)
                    else:
                        large_files.append(video_file)
                else:
                    large_files.append(video_file)
                if subtitle_file:
                    if os.path.getsize(subtitle_file) < CHUNK_SIZE:
                        if upload_to_github(subtitle_file, f"{FOLDER_NAME}/{subtitle_file}"):
                            total_uploaded += 1
                            os.remove(subtitle_file)
                        else:
                            large_files.append(subtitle_file)
                    else:
                        large_files.append(subtitle_file)
            else:
                ok, filename = download_direct(url, idx)
                if not ok:
                    continue
                if os.path.getsize(filename) < CHUNK_SIZE:
                    if upload_to_github(filename, f"{FOLDER_NAME}/{filename}"):
                        total_uploaded += 1
                        os.remove(filename)
                    else:
                        large_files.append(filename)
                else:
                    large_files.append(filename)

        if large_files:
            log(f"\n📦 Splitting {len(large_files)} large file(s) with chunker.py")
            chunker_cmd = ["python3", "python/chunker.py", "--files"] + large_files + \
                          ["--output-dir", FOLDER_NAME, "--chunk-size", "20"]
            result = subprocess.run(chunker_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                log(f"❌ chunker.py failed: {result.stderr}")
                sys.exit(1)
            log(result.stdout)
            for item in out_dir.iterdir():
                if item.is_file() and (".part" in item.name or item.name == "reassemble.bat"):
                    if upload_to_github(str(item), f"{FOLDER_NAME}/{item.name}"):
                        total_uploaded += 1
            for vf in large_files:
                try: os.remove(vf)
                except: pass

        log(f"\n📊 Total uploaded items: {total_uploaded}")
        if total_uploaded == 0:
            log("❌ No files were uploaded.")
            sys.exit(1)
        log("✅ All done.")

    except Exception as e:
        log(f"❌ Unhandled error: {traceback.format_exc()}")
        sys.exit(1)
    finally:
        try:
            os.remove("pending_urls.txt")
        except OSError:
            pass
        try:
            for f in os.listdir("."):
                if f.startswith("current_sub") and f.endswith(".srt"):
                    os.remove(f)
        except Exception:
            pass

if __name__ == "__main__":
    main()