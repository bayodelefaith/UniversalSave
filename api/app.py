import os
import re
import asyncio
import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from youtubesearchpython import VideosSearch
import uvicorn

# Try to import playwright, fallback to requests if not available
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Invidious fallback instances
INVIDIOUS_INSTANCES = [
    "https://y.com.sb",
    "https://vid.puffyan.us", 
    "https://inv.vern.cc",
    "https://iv.nboeck.de"
]

PLATFORM_PATTERNS = {
    'youtube': {
        'domains': ['youtube.com', 'youtu.be'],
        'id_pattern': r'(?:v=|\/)([0-9A-Za-z_-]{11})',
        'thumbnail': lambda vid: f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"
    },
    'tiktok': {
        'domains': ['tiktok.com'],
        'id_pattern': r'\/video\/(\d+)',
        'thumbnail': lambda vid: f"https://placehold.co/320x180/000000/FFFFFF?text=TikTok"
    },
    'instagram': {
        'domains': ['instagram.com'],
        'id_pattern': r'\/(p|reel|tv|stories)\/([A-Za-z0-9_-]+)',
        'thumbnail': lambda vid: f"https://placehold.co/320x180/E1306C/FFFFFF?text=Instagram"
    },
    'facebook': {
        'domains': ['facebook.com', 'fb.watch'],
        'id_pattern': r'(?:videos\/|v=|watch\?v=|\/)(\d+)',
        'thumbnail': lambda vid: f"https://placehold.co/320x180/1877F2/FFFFFF?text=Facebook"
    },
    'twitter': {
        'domains': ['twitter.com', 'x.com'],
        'id_pattern': r'\/status\/(\d+)',
        'thumbnail': lambda vid: f"https://placehold.co/320x180/1DA1F2/FFFFFF?text=Twitter"
    },
}

def get_platform(url: str) -> tuple:
    url_lower = url.lower()
    for platform, config in PLATFORM_PATTERNS.items():
        if any(domain in url_lower for domain in config['domains']):
            match = re.search(config['id_pattern'], url)
            if match:
                video_id = match.group(2) if platform == 'instagram' and match.lastindex > 1 else match.group(1)
                return platform, video_id, config['thumbnail'](video_id)
            return platform, None, config['thumbnail']("unknown")
    return 'unknown', None, "https://placehold.co/320x180/1e293b/94a3b8?text=Video"

# ══════════════════════════════════════════════════════════════════════════════
#  PLAYWRIGHT BROWSER AUTOMATION
# ══════════════════════════════════════════════════════════════════════════════

async def scrape_with_playwright(url: str, platform: str) -> dict:
    """
    Use Playwright to open a real browser and extract video info
    This mimics real user behavior and bypasses bot detection
    """
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright not installed"}
    
    try:
        async with async_playwright() as p:
            # Launch browser with stealth options
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--no-zygote',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
            
            # Create context with realistic user agent
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York',
            )
            
            page = await context.new_page()
            
            # Navigate to the video URL
            await page.goto(url, wait_until='networkidle', timeout=30000)
            
            # Wait a bit to seem human-like
            await asyncio.sleep(2)
            
            result = {}
            
            if platform == 'youtube':
                # Extract YouTube video info
                result = await page.evaluate('''() => {
                    const title = document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent || 
                                 document.querySelector('h1.title.ytd-video-primary-info-renderer')?.textContent ||
                                 document.querySelector('h1')?.textContent || 'Unknown';
                    
                    const channel = document.querySelector('ytd-channel-name a')?.textContent || 
                                   document.querySelector('.ytd-channel-name a')?.textContent ||
                                   document.querySelector('[class*="channel"] a')?.textContent || 'Unknown';
                    
                    // Try to find video URL in page source or network
                    const html = document.documentElement.innerHTML;
                    const match = html.match(/"url":"(https:\/\/[^"]*googlevideo\.com[^"]*)"/);
                    const videoUrl = match ? match[1].replace(/\\\\u0026/g, '&') : null;
                    
                    return {
                        title: title.trim(),
                        channel: channel.trim(),
                        videoUrl: videoUrl,
                        pageUrl: window.location.href
                    };
                }''')
                
                # If we didn't get direct URL, try alternative method
                if not result.get('videoUrl'):
                    # Look for ytInitialPlayerResponse
                    player_response = await page.evaluate('''() => {
                        const scripts = Array.from(document.querySelectorAll('script'));
                        const playerScript = scripts.find(s => s.textContent.includes('ytInitialPlayerResponse'));
                        if (playerScript) {
                            const match = playerScript.textContent.match(/ytInitialPlayerResponse\s*=\s*({.+?});/);
                            if (match) return JSON.parse(match[1]);
                        }
                        return window.ytInitialPlayerResponse;
                    }''')
                    
                    if player_response and 'streamingData' in player_response:
                        formats = player_response['streamingData'].get('formats', [])
                        if formats:
                            # Get best quality MP4
                            best_format = None
                            for fmt in formats:
                                if 'mp4' in fmt.get('mimeType', '').lower():
                                    if not best_format or fmt.get('height', 0) > best_format.get('height', 0):
                                        best_format = fmt
                            
                            if best_format:
                                result['videoUrl'] = best_format['url']
                                result['quality'] = f"{best_format.get('height', 'unknown')}p"
            
            elif platform == 'tiktok':
                # Extract TikTok video info
                result = await page.evaluate('''() => {
                    const title = document.querySelector('[data-e2e="video-desc"]')?.textContent || 
                                 document.querySelector('h1')?.textContent || 'TikTok Video';
                    
                    const author = document.querySelector('[data-e2e="video-author-username"]')?.textContent ||
                                   document.querySelector('[class*="author"]')?.textContent || 'Unknown';
                    
                    // Look for video element
                    const videoEl = document.querySelector('video');
                    const videoUrl = videoEl?.src || null;
                    
                    return {
                        title: title.trim(),
                        channel: author.trim(),
                        videoUrl: videoUrl,
                        pageUrl: window.location.href
                    };
                }''')
                
                # Try to get from SSR data
                if not result.get('videoUrl'):
                    ssr_data = await page.evaluate('''() => {
                        const scripts = Array.from(document.querySelectorAll('script'));
                        const ssrScript = scripts.find(s => s.id === 'RENDER_DATA' || s.textContent.includes('SSR'));
                        return ssrScript ? ssrScript.textContent : null;
                    }''')
                    
                    if ssr_data:
                        # Parse SSR JSON
                        try:
                            import json
                            # TikTok embeds data in script tags
                            match = re.search(r'<script[^>]*>window\._SSR_HYDRATED_DATA\s*=\s*({.+?})<\/script>', await page.content())
                            if match:
                                data = json.loads(match.group(1))
                                video_info = data.get('videoInfo', {}).get('video', {})
                                result['videoUrl'] = video_info.get('playAddr', '')
                                result['title'] = video_info.get('desc', 'TikTok Video')
                        except:
                            pass
            
            await browser.close()
            
            if result.get('videoUrl'):
                return {
                    "status": "success",
                    "title": result.get('title', f'{platform.capitalize()} Video'),
                    "channel": result.get('channel', 'Unknown'),
                    "download_url": result['videoUrl'],
                    "quality": result.get('quality', 'unknown'),
                    "platform": platform
                }
            else:
                return {"error": f"Could not extract video URL from {platform}"}
                
    except Exception as e:
        return {"error": f"Browser automation failed: {str(e)}"}

# ══════════════════════════════════════════════════════════════════════════════
#  FALLBACK METHODS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_from_invidious(video_id: str) -> dict:
    """Fallback to Invidious API"""
    for instance in INVIDIOUS_INSTANCES:
        try:
            res = requests.get(f"{instance}/api/v1/videos/{video_id}", timeout=10)
            if res.status_code == 200:
                data = res.json()
                formats = data.get("formatStreams", [])
                if formats:
                    best = formats[-1]  # Highest quality
                    return {
                        "status": "success",
                        "title": data.get("title", "YouTube Video"),
                        "channel": data.get("author", "Unknown"),
                        "download_url": best["url"],
                        "thumbnail": data.get("videoThumbnails", [{}])[0].get("url", ""),
                        "quality": best.get("qualityLabel", "unknown"),
                        "platform": "youtube"
                    }
        except:
            continue
    return {"error": "Invidious failed"}

# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def read_root():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/style.css")
def read_css():
    return FileResponse(os.path.join(BASE_DIR, "style.css"), media_type="text/css")

@app.get("/terms.html", response_class=HTMLResponse)
def read_terms():
    return FileResponse(os.path.join(BASE_DIR, "terms.html"))

@app.get("/search")
async def search_videos(q: str = Query(...)):
    """Search YouTube using youtube-search-python"""
    try:
        search = VideosSearch(q, limit=10)
        results = search.result()["result"]
        
        formatted = []
        for video in results:
            video_id = video["id"]
            formatted.append({
                "title": video["title"],
                "url": video["link"],
                "thumbnail": video["thumbnails"][0]["url"] if video["thumbnails"] else f"https://img.youtube.com/vi/{video_id}/0.jpg",
                "channel": video["channel"]["name"] if video.get("channel") else "Unknown",
                "duration": video.get("duration", ""),
                "views": video.get("viewCount", {}).get("text", "") if video.get("viewCount") else "",
                "video_id": video_id,
                "platform": "youtube"
            })
        
        return formatted
        
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}

@app.get("/fetch_url")
async def fetch_url(url: str = Query(...)):
    """
    Fetch video info using multiple methods:
    1. Try Playwright browser automation (most reliable)
    2. Fallback to Invidious API
    """
    if not url.startswith(('http://', 'https://')):
        return {"error": "Invalid URL"}
    
    platform, video_id, thumbnail = get_platform(url)
    
    # Try Playwright first for YouTube and TikTok
    if PLAYWRIGHT_AVAILABLE and platform in ['youtube', 'tiktok']:
        result = await scrape_with_playwright(url, platform)
        
        if result.get("status") == "success":
            return [{
                "title": result["title"],
                "url": url,
                "video_id": video_id or str(hash(url))[:12],
                "thumbnail": thumbnail,
                "channel": result["channel"],
                "duration": "",
                "platform": platform,
                "type": "stream",
                "download_url": result["download_url"],
                "quality": result.get("quality", "unknown"),
                "method": "playwright"
            }]
        
        # If Playwright fails, log it and try fallback
        print(f"Playwright failed: {result.get('error')}, trying fallback...")
    
    # Fallback to Invidious for YouTube
    if platform == 'youtube' and video_id:
        result = fetch_from_invidious(video_id)
        
        if result.get("status") == "success":
            return [{
                "title": result["title"],
                "url": url,
                "video_id": video_id,
                "thumbnail": result.get("thumbnail", thumbnail),
                "channel": result["channel"],
                "duration": "",
                "platform": platform,
                "type": "stream",
                "download_url": result["download_url"],
                "quality": result["quality"],
                "method": "invidious"
            }]
    
    # For other platforms or if all fails
    return {"error": f"Could not fetch video from {platform}. Try a different URL or platform."}

@app.get("/sizes")
async def video_sizes(urls: str = Query(...)):
    """Return estimated file sizes"""
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    results = {}
    
    for url in url_list:
        platform, video_id, _ = get_platform(url)
        
        estimates = {
            "best": "~50 MB", "1080p": "~50 MB", "720p": "~30 MB", 
            "480p": "~15 MB", "audio": "~5 MB",
        }
        
        if platform == "tiktok":
            estimates = {"best": "~15 MB", "1080p": "~15 MB", "720p": "~10 MB", "480p": "~5 MB", "audio": "~2 MB"}
        
        results[video_id or url] = estimates
    
    return results

@app.get("/download")
async def download_video(id: str, url: str, quality: str = "best"):
    """
    Get fresh download URL (bypasses expiration)
    """
    platform, video_id, _ = get_platform(url)
    
    # Try to get fresh URL
    if PLAYWRIGHT_AVAILABLE and platform in ['youtube', 'tiktok']:
        result = await scrape_with_playwright(url, platform)
        if result.get("status") == "success":
            return {
                "status": "ready",
                "download_url": result["download_url"],
                "filename": f"{result['title']}.mp4",
                "quality": result.get("quality", quality),
                "type": "direct"
            }
    
    # Fallback to stored URL from fetch_url (might be expired)
    return {
        "status": "error",
        "error": "Could not generate fresh download URL. Please re-fetch the video."
    }

@app.get("/progress")
def get_progress(id: str):
    return {"status": "finished", "progress": 100, "message": "Ready"}

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "supported_platforms": list(PLATFORM_PATTERNS.keys())
    }

if __name__ == "__main__":
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)