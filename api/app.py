import os
import time
import uuid
import asyncio
from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOAD_DIR = "/tmp/UniversalVideoDownloader"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── In-memory state ───────────────────────────────────────────────────────────
download_status: dict = {}
search_cache:    dict = {}
CACHE_TTL = 300  # seconds

# ── Common yt-dlp options (spoof a real browser to avoid bot detection) ───────
COMMON_OPTS = {
    "quiet":       True,
    "no_warnings": True,
    "http_headers": {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
        "Sec-Fetch-User":  "?1",
    }, 
    # Use cookies file if present (place cookies.txt inside the api/ folder)
    "cookiefile": os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
                  if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"))
                  else None,
}


# ══════════════════════════════════════════════════════════════════════════════
#  SYNC HELPERS  (run inside a thread-pool via run_in_executor)
# ══════════════════════════════════════════════════════════════════════════════

def get_video_info_sync(url: str) -> dict:
    """Return approximate file-sizes (MB) for each quality tier."""
    ydl_opts = {
        **COMMON_OPTS,
    }
    size_map = {"best": None, "720p": None, "480p": None, "360p": None, "audio": None}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info    = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])

            for fmt in formats:
                filesize = fmt.get("filesize") or fmt.get("filesize_approx") or 0
                height   = fmt.get("height") or 0
                ext      = fmt.get("ext", "")
                vcodec   = fmt.get("vcodec", "none")
                acodec   = fmt.get("acodec", "none")
                mb       = round(filesize / (1024 * 1024), 1) if filesize else None

                if vcodec == "none" and acodec != "none":
                    if size_map["audio"] is None and mb:
                        size_map["audio"] = mb

                if vcodec != "none" and ext == "mp4":
                    if height <= 360 and size_map["360p"] is None and mb:
                        size_map["360p"] = mb
                    elif height <= 480 and size_map["480p"] is None and mb:
                        size_map["480p"] = mb
                    elif height <= 720 and size_map["720p"] is None and mb:
                        size_map["720p"] = mb
                    elif size_map["best"] is None and mb:
                        size_map["best"] = mb

    except Exception as e:
        print(f"❌ Info error: {e}")

    return size_map


def fetch_video_by_url_sync(url: str) -> dict | None:
    """Fetch metadata for a single video URL."""
    ydl_opts = {
        **COMMON_OPTS,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info       = ydl.extract_info(url, download=False)
            video_id   = info.get("id", str(uuid.uuid4()))
            title      = info.get("title", "Unknown Title")
            channel    = info.get("channel") or info.get("uploader", "Unknown")
            duration   = str(info.get("duration", "")) or ""
            views      = str(info.get("view_count") or "")
            thumbnails = info.get("thumbnails", [])
            thumbnail  = (
                thumbnails[-1]["url"]
                if thumbnails
                else "https://placehold.co/180x101/16213e/555?text=No+Preview"
            )
            clean_url = info.get("webpage_url", url)

            return {
                "title":     title,
                "url":       clean_url,
                "thumbnail": thumbnail,
                "channel":   channel,
                "duration":  duration,
                "views":     views,
                "video_id":  video_id,
            }

    except Exception as e:
        print(f"❌ URL fetch error: {e}")
        return None


def scrape_youtube_sync(query: str) -> list:
    """Search YouTube and return up to 10 result dicts (cached for CACHE_TTL s)."""
    if query in search_cache:
        timestamp, cached = search_cache[query]
        if time.time() - timestamp < CACHE_TTL:
            return cached

    results  = []
    ydl_opts = {
        **COMMON_OPTS,
        "extract_flat":   True,
        "default_search": "ytsearch10",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info   = ydl.extract_info(f"ytsearch10:{query}", download=False)
            videos = info.get("entries", [])

            for video in videos:
                try:
                    video_id  = video.get("id", "")
                    title     = video.get("title", "")
                    url       = video.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                    duration  = str(video.get("duration", "")) or ""
                    channel   = video.get("channel") or video.get("uploader", "Unknown")
                    views     = str(video.get("view_count") or "")
                    thumbnail = (
                        (video.get("thumbnails") or [{}])[-1].get("url")
                        or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    )
                    if title and video_id:
                        results.append({
                            "title":     title,
                            "url":       url,
                            "thumbnail": thumbnail,
                            "channel":   channel,
                            "duration":  duration,
                            "views":     views,
                            "video_id":  video_id,
                        })
                except Exception:
                    pass

    except Exception as e:
        print(f"❌ Search error: {e}")

    search_cache[query] = (time.time(), results)
    return results


def download_video_task(video_id: str, url: str, quality: str) -> None:
    """Background task: download video and update download_status."""

    def progress_hook(d):
        if d["status"] == "downloading":
            pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
            try:
                download_status[video_id]["progress"] = float(pct_str)
            except Exception:
                pass
        elif d["status"] == "finished":
            download_status[video_id]["status"]   = "finished"
            download_status[video_id]["progress"] = 100
            download_status[video_id]["filename"] = os.path.basename(d["filename"])

    format_map = {
        "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
        "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
        "360p":  "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]",
        "audio": "bestaudio/best",
    }

    ydl_opts = {
        **COMMON_OPTS,
        "format":         format_map.get(quality, format_map["best"]),
        "outtmpl":        os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "noplaylist":     True,
        "postprocessors": [
            {
                "key":            "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ] if quality != "audio" else [
            {
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        download_status[video_id]["status"] = "error"
        download_status[video_id]["error"]  = str(e)


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def read_root():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/style.css")
def read_css():
    return FileResponse(
        os.path.join(BASE_DIR, "style.css"),
        media_type="text/css",
    )


@app.get("/terms.html", response_class=HTMLResponse)
def read_terms():
    return FileResponse(os.path.join(BASE_DIR, "terms.html"))


@app.get("/search")
async def search_videos(q: str = Query(..., description="Search query")):
    loop    = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, scrape_youtube_sync, q)
    return results


@app.get("/fetch_url")
async def fetch_url(url: str = Query(..., description="Video URL")):
    loop  = asyncio.get_event_loop()
    video = await loop.run_in_executor(None, fetch_video_by_url_sync, url)
    if video:
        return [video]
    return {"error": "Could not fetch video info from that URL."}


@app.get("/sizes")
async def video_sizes(urls: str = Query(..., description="Comma-separated video URLs")):
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    loop     = asyncio.get_event_loop()

    async def fetch_one(url: str):
        if "v=" in url:
            vid_id = url.split("v=")[1].split("&")[0]
        else:
            try:
                ydl_opts = {
                    **COMMON_OPTS,
                    "extract_flat": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info   = ydl.extract_info(url, download=False)
                    vid_id = info.get("id", url)
            except Exception:
                vid_id = url

        sizes = await loop.run_in_executor(None, get_video_info_sync, url)
        return vid_id, sizes

    tasks   = [fetch_one(u) for u in url_list]
    results = await asyncio.gather(*tasks)
    return dict(results)


@app.get("/download")
async def download_video(
    id:               str,
    url:              str,
    background_tasks: BackgroundTasks,
    quality:          str = "best",
):
    if (
        id in download_status
        and download_status[id].get("status") == "downloading"
    ):
        return {"message": "Already downloading", "id": id}

    download_status[id] = {"status": "downloading", "progress": 0, "filename": ""}
    background_tasks.add_task(download_video_task, id, url, quality)
    return {"message": "Download started", "id": id}


@app.get("/progress")
def get_progress(id: str):
    return download_status.get(id, {"status": "not_started", "progress": 0})


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ── Local dev only ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)