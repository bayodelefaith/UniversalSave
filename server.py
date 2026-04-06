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

DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "UniversalVideoDownloader")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

download_status = {}
search_cache    = {}
CACHE_TTL       = 300

def get_video_info_sync(url):
    ydl_opts = {"quiet": True, "no_warnings": True}
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

def fetch_video_by_url_sync(url):
    ydl_opts = {"quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info      = ydl.extract_info(url, download=False)
            video_id  = info.get("id", str(uuid.uuid4()))
            title     = info.get("title", "Unknown Title")
            channel   = info.get("channel") or info.get("uploader", "Unknown")
            duration  = str(info.get("duration", "")) or ""
            views     = str(info.get("view_count") or "")
            thumbnails = info.get("thumbnails", [])
            thumbnail = thumbnails[-1]["url"] if thumbnails else f"https://placehold.co/180x101/16213e/555?text=No+Preview"

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

def scrape_youtube_sync(query):
    if query in search_cache:
        timestamp, cached = search_cache[query]
        if time.time() - timestamp < CACHE_TTL:
            return cached
    results = []
    ydl_opts = {
        "quiet": True, "no_warnings": True, "extract_flat": True, "default_search": "ytsearch10"
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info   = ydl.extract_info(f"ytsearch10:{query}", download=False)
            videos = info.get("entries", [])
            for i, video in enumerate(videos):
                try:
                    video_id  = video.get("id", "")
                    title     = video.get("title", "")
                    url       = video.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                    duration  = str(video.get("duration", "")) or ""
                    channel   = video.get("channel") or video.get("uploader", "Unknown")
                    views     = str(video.get("view_count") or "")
                    thumbnail = (video.get("thumbnails") or [{}])[-1].get("url") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    if title and video_id:
                        results.append({
                            "title": title, "url": url, "thumbnail": thumbnail,
                            "channel": channel, "duration": duration, "views": views, "video_id": video_id
                        })
                except Exception as e:
                    pass
    except Exception as e:
        print(f"❌ Search error: {e}")
    search_cache[query] = (time.time(), results)
    return results

def download_video_task(video_id: str, url: str, quality: str):
    def progress_hook(d):
        if d["status"] == "downloading":
            percent = d.get("_percent_str", "0%").strip().replace("%", "")
            try:
                download_status[video_id]["progress"] = float(percent)
            except:
                pass
        elif d["status"] == "finished":
            download_status[video_id]["status"]   = "finished"
            download_status[video_id]["progress"] = 100
            download_status[video_id]["filename"] = os.path.basename(d["filename"])

    format_map = {
        "best":  "best",
        "720p":  "best[height<=720]",
        "480p":  "best[height<=480]",
        "360p":  "best[height<=360]",
        "audio": "bestaudio/best",
    }
    ydl_opts = {
        "format": format_map.get(quality, format_map["best"]),
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "quiet": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        download_status[video_id]["status"] = "error"
        download_status[video_id]["error"]  = str(e)

@app.get("/")
def read_root():
    return FileResponse("index.html")

@app.get("/style.css")
def read_css():
    return FileResponse("style.css")

@app.get("/terms.html")
def read_terms():
    return FileResponse("terms.html")

@app.get("/search")
async def search_videos(q: str = Query(..., description="Search query")):
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, scrape_youtube_sync, q)
    return results

@app.get("/fetch_url")
async def fetch_url(url: str = Query(..., description="Video URL")):
    loop = asyncio.get_event_loop()
    video = await loop.run_in_executor(None, fetch_video_by_url_sync, url)
    if video:
        return [video]
    return {"error": "Could not fetch video info from that URL."}

@app.get("/sizes")
async def video_sizes(urls: str = Query(..., description="Comma-separated URLs")):
    url_list = [u for u in urls.split(",") if u]
    loop = asyncio.get_event_loop()

    async def fetch_one(url):
        # We need the video ID to construct the map correctly for the UI
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                vid_id = info.get("id", url)
        except:
             vid_id = url
             
        if "v=" in url:
            vid_id = url.split("v=")[1].split("&")[0]

        sizes = await loop.run_in_executor(None, get_video_info_sync, url)
        return vid_id, sizes

    tasks = [fetch_one(u) for u in url_list]
    results = await asyncio.gather(*tasks)
    return dict(results)

@app.get("/download")
async def download_video(id: str, url: str, background_tasks: BackgroundTasks, quality: str = "best"):
    if id in download_status and download_status[id]["status"] == "downloading":
        return {"message": "Already downloading"}
    
    download_status[id] = {"status": "downloading", "progress": 0, "filename": ""}
    background_tasks.add_task(download_video_task, id, url, quality)
    return {"message": "Download started", "id": id}

@app.get("/progress")
def get_progress(id: str):
    return download_status.get(id, {"status": "not_started", "progress": 0})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)