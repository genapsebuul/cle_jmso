import json, math, os, subprocess, sys, requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "-1003841663690").strip()
CALLBACK_URL = os.environ.get("CALLBACK_URL", "https://ceviod.com/api/render-log").strip()
TITLE = os.environ.get("VIDEO_TITLE", "Untitled Video").strip()
CATEGORY = os.environ.get("CATEGORY", "video").strip().lower()
PARTS_RAW = os.environ.get("PARTS_IDS", "").strip()
CREATOR_USERNAME = os.environ.get("CREATOR_USERNAME", "admin").strip()
CREATOR_NAME = os.environ.get("CREATOR_NAME", "CEVIOD Creator").strip()
MEDIA_ID = os.environ.get("MEDIA_ID", "").strip()

CHUNK_SIZE = 19 * 1024 * 1024

def tg_download_parts(part_ids, out_path):
    with open(out_path, "wb") as out_f:
        for idx, fid in enumerate(part_ids):
            if not fid:
                continue
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": fid}, timeout=30)
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram getFile failed for part {idx}: {data}")
            file_path = data["result"]["file_path"]
            dl_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            with requests.get(dl_url, stream=True, timeout=180) as dl:
                dl.raise_for_status()
                for chunk in dl.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        out_f.write(chunk)
    print(f"Downloaded & merged {len(part_ids)} parts -> {out_path} ({os.path.getsize(out_path)} bytes)")

def tg_upload_file(file_path, caption=""):
    file_size = os.path.getsize(file_path)
    num_parts = max(1, math.ceil(file_size / CHUNK_SIZE))
    file_ids = []
    base_name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        for i in range(num_parts):
            data_bytes = f.read(CHUNK_SIZE)
            part_name = base_name if num_parts == 1 else f"{base_name}.part{i}"
            files = {"document": (part_name, data_bytes)}
            payload = {"chat_id": CHAT_ID}
            if caption and i == 0:
                payload["caption"] = caption[:900]
            resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", data=payload, files=files, timeout=180)
            res_json = resp.json()
            if not res_json.get("ok"):
                raise RuntimeError(f"Telegram sendDocument failed: {res_json}")
            msg = res_json["result"]
            doc = msg.get("document") or msg.get("video") or msg.get("audio")
            file_ids.append(doc["file_id"])
    return file_ids

def probe_video(path):
    height = 720
    duration = 180.0
    try:
        h_out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=height", "-of", "csv=p=0", path
        ], text=True).strip()
        if h_out:
            height = int(float(h_out.splitlines()[0].strip()))
    except Exception as e:
        print(f"ffprobe height fallback: {e}")
    try:
        d_out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", path
        ], text=True).strip()
        if d_out:
            duration = float(d_out.splitlines()[0].strip())
    except Exception as e:
        print(f"ffprobe duration fallback: {e}")
    return height, duration

def generate_and_upload_thumb(input_path, duration_sec):
    thumb_path = "thumb.jpg"
    ss_sec = max(1, min(int(duration_sec * 0.15), 10))
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(ss_sec), "-i", input_path,
        "-vframes", "1", "-vf", "scale=1280:-2", "-q:v", "2", thumb_path
    ], check=False)
    if not os.path.exists(thumb_path) or os.path.getsize(thumb_path) < 1000:
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0", "-i", input_path,
            "-vframes", "1", "-vf", "scale=1280:-2", "-q:v", "2", thumb_path
        ], check=False)
    if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 500:
        ids = tg_upload_file(thumb_path, f"Thumbnail: {TITLE}")
        if ids:
            return f"https://ceviod.com/api/stream/{ids[0]}?type=image"
    return ""

def main():
    if not BOT_TOKEN or not PARTS_RAW:
        print("Missing BOT_TOKEN or PARTS_IDS")
        sys.exit(1)

    part_ids = [p.strip() for p in PARTS_RAW.split(",") if p.strip()]
    input_file = "input_source.mp4"
    tg_download_parts(part_ids, input_file)

    src_height, duration_sec = probe_video(input_file)
    print(f"Detected source height: {src_height}p | duration: {duration_sec:.1f}s")

    thumb_url = generate_and_upload_thumb(input_file, duration_sec)
    print(f"Generated thumbnail URL: {thumb_url}")

    LADDER = [
        ("1080p", 1080, "2400k", "128k"),
        ("720p", 720, "1200k", "128k"),
        ("480p", 480, "700k", "96k"),
        ("360p", 360, "400k", "96k"),
        ("240p", 240, "220k", "64k"),
        ("144p", 144, "110k", "48k"),
    ]
    effective_height = 1080 if src_height >= 1000 else (720 if src_height >= 680 else src_height)
    tiers = [t for t in LADDER if t[1] <= effective_height] or [LADDER[-1]]
    print(f"Target downward tiers: {[t[0] for t in tiers]}")

    for label, h, vbit, abit in tiers:
        out_name = f"out_{label}.mp4"
        print(f"Transcoding {label} ({h}p)...")
        subprocess.run([
            "ffmpeg", "-y", "-i", input_file,
            "-vf", f"scale=-2:{h}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", vbit, "-maxrate", vbit, "-bufsize", "2M",
            "-c:a", "aac", "-b:a", abit,
            "-movflags", "+faststart",
            out_name
        ], check=True)
        out_size = os.path.getsize(out_name)
        tg_ids = tg_upload_file(out_name, f"[{label}] {TITLE} - @{CREATOR_USERNAME}")

        cb_payload = {
            "stage": "completed",
            "status": "success",
            "media_id": f"{MEDIA_ID}_{label}" if MEDIA_ID else None,
            "title": f"[{label}] {TITLE}",
            "category": CATEGORY,
            "thumbnail_url": thumb_url,
            "duration": round(duration_sec),
            "creator_username": CREATOR_USERNAME,
            "creator_name": CREATOR_NAME,
            "media": {
                "title": f"[{label}] {TITLE}",
                "telegramFileId": tg_ids[0],
                "telegramFileIds": tg_ids,
                "fileSize": out_size,
                "duration": round(duration_sec),
                "thumbnail_url": thumb_url,
                "category": CATEGORY
            }
        }
        r = requests.post(CALLBACK_URL, json=cb_payload, timeout=30)
        print(f"Callback [{label}] -> {r.status_code}: {r.text[:150]}")

if __name__ == "__main__":
    main()