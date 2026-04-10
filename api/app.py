import os
import re
import asyncio
import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from youtubesearchpython import VideosSearch
import uvicorn

# Try to import playwright
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Try to import chromium-binary for serverless
try:
    import chromium_binary
    CHROMIUM_BINARY_AVAILABLE = True
except ImportError:
    CHROMIUM_BINARY_AVAILABLE = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Set Playwright browsers path
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/render/.cache/ms-playwright"

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

def find_chromium_executable():
    """
    Find Chromium executable using multiple methods.
    Priority: chromium-binary > playwright headless shell > playwright chromium > system chromium
    """
    # Method 1: Use chromium-binary package (most reliable for serverless)
    if CHROMIUM_BINARY_AVAILABLE:
        try:
            chromium_path = chromium_binary.chromium_path
            if chromium_path and os.path.exists(chromium_path):
                print(f"✅ Using chromium-binary: {chromium_path}")
                return chromium_path
        except:
            pass
    
    # Method 2: Playwright headless shell (1.58+)
    headless_paths = [
        "/opt/render/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell",
        "/opt/render/.cache/ms-playwright/chromium_headless_shell-1200/chrome-headless-shell-linux64/chrome-headless-shell",
        os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell"),
        os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-1200/chrome-headless-shell-linux64/chrome-headless-shell"),
    ]
    
    for path in headless_paths:
        if os.path.exists(path):
            print(f"✅ Found Playwright headless shell: {path}")
            return path
    
    # Method 3: Regular Playwright Chromium
    chromium_paths = [
        "/opt/render/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
        "/opt/render/.cache/ms-playwright/chromium-1200/chrome-linux64/chrome",
        os.path.expanduser("~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium-1200/chrome-linux64/chrome"),
    ]
    
    for path in chromium_paths:
        if os.path.exists(path):
            print(f"✅ Found Playwright Chromium: {path}")
            return path
    
    # Method 4: System Chromium
    system_paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    
    for path in system_paths:
        if os.path.exists(path):
            print(f"✅ Found system Chromium: {path}")
            return path
    
    # Method 5: Try to find any chromium in PATH
    try:
        import shutil
        chromium_in_path = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if chromium_in_path:
            print(f"✅ Found Chromium in PATH: {chromium_in_path}")
            return chromium_in_path
    except:
        pass
    
    print("❌ No Chromium found")
    return None

async def scrape_with_playwright(url: str, platform: str) -> dict:
    """Use Playwright to extract video info"""
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright not installed"}
    
    chromium_path = find_chromium_executable()
    
    if not chromium_path:
        return {"error": "Chromium not found. Please install chromium-binary or run: python -m playwright install chromium"}
    
    try:
        async with async_playwright() as p:
            launch_options = {
                "headless": True,
                "args": [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--no-zygote',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-blink-features=AutomationControlled',
                ]
            }
            
            # Use explicit path
            launch_options["executable_path"] = chromium_path
            
            # Check if using headless shell (new in 1.58+)
            if "headless_shell" in chromium_path or "headless-shell" in chromium_path:
                print("🚀 Using Chromium Headless Shell (optimized for serverless)")
            else:
                print("🚀 Using regular Chromium")
            
            try:
                browser = await p.chromium.launch(**launch_options)
            except Exception as launch_error:
                print(f"❌ Launch failed: {launch_error}")
                return {"error": f"Failed to launch browser: {str(launch_error)}"}
            
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York',
            )
            
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
            """)
            
            page = await context.new_page()
            
            # Navigate with longer timeout for slow loading
            try:
                await page.goto(url, wait_until='networkidle', timeout=45000)
            except:
                # Fallback to domcontentloaded if networkidle times out
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            
            await asyncio.sleep(3)  # Wait for JS to execute
            
            result = {}
            
            if platform == 'youtube':
                result = await page.evaluate('''() => {
                    const title = document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent || 
                                 document.querySelector('h1.title.ytd-video-primary-info-renderer')?.textContent ||
                                 document.querySelector('h1')?.textContent || 'Unknown';
                    const channel = document.querySelector('ytd-channel-name a')?.textContent || 
                                   document.querySelector('.ytd-channel-name a')?.textContent || 'Unknown';
                    const html = document.documentElement.innerHTML;
                    const match = html.match(/"url":"(https:\\/\\/[^"]*googlevideo\\.com[^"]*)"/);
                    return {
                        title: title.trim(),
                        channel: channel.trim(),
                        videoUrl: match ? match[1].replace(/\\\\u0026/g, '&') : null,
                    };
                }''')
                
                if not result.get('videoUrl'):
                    # Try ytInitialPlayerResponse
                    player_response = await page.evaluate('''() => {
                        const scripts = Array.from(document.querySelectorAll('script'));
                        const playerScript = scripts.find(s => s.textContent.includes('ytInitialPlayerResponse'));
                        if (playerScript) {
                            const match = playerScript.textContent.match(/ytInitialPlayerResponse\\s*=\\s*({.+?});/);
                            if (match) return JSON.parse(match[1]);
                        }
                        return window.ytInitialPlayerResponse;
                    }''')
                    
                    if player_response and 'streamingData' in player_response:
                        formats = player_response['streamingData'].get('formats', [])
                        if formats:
                            best_format = max(formats, key=lambda x: x.get('height', 0))
                            result['videoUrl'] = best_format['url']
                            result['quality'] = f"{best_format.get('height', 'unknown')}p"
            
            elif platform == 'tiktok':
                result = await page.evaluate('''() => {
                    const title = document.querySelector('[data-e2e="video-desc"]')?.textContent || 'TikTok Video';
                    const author = document.querySelector('[data-e2e="video-author-username"]')?.textContent || 'Unknown';
                    const videoEl = document.querySelector('video');
                    return {
                        title: title.trim(),
                        channel: author.trim(),
                        videoUrl: videoEl?.src || null,
                    };
                }''')
            
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
        print(f"❌ Playwright error: {e}")
        return {"error": f"Browser automation failed: {str(e)}"}

def fetch_from_invidious(video_id: str) -> dict:
    """Fallback to Invidious API"""
    for instance in INVIDIOUS_INSTANCES:
        try:
            res = requests.get(f"{instance}/api/v1/videos/{video_id}", timeout=10)
            if res.status_code == 200:
                data = res.json()
                formats = data.get("formatStreams", [])
                if formats:
                    best = formats[-1]
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
    if not url.startswith(('http://', 'https://')):
        return {"error": "Invalid URL"}
    
    platform, video_id, thumbnail = get_platform(url)
    
    # Try Playwright first
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
    
    return {"error": f"Could not fetch video from {platform}. All methods failed."}

@app.get("/sizes")
async def video_sizes(urls: str = Query(...)):
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
    platform, video_id, _ = get_platform(url)
    
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
    
    return {
        "status": "error",
        "error": "Could not generate download URL. Browser automation failed."
    }

@app.get("/progress")
def get_progress(id: str):
    return {"status": "finished", "progress": 100, "message": "Ready"}

@app.get("/health")
def health_check():
    chromium_path = find_chromium_executable()
    
    # List all files in playwright cache for debugging
    cache_files = []
    try:
        import glob
        patterns = [
            "/opt/render/.cache/ms-playwright/**/chrome*",
            os.path.expanduser("~/.cache/ms-playwright/**/chrome*"),
        ]
        for pattern in patterns:
            cache_files.extend(glob.glob(pattern, recursive=True))
    except:
        pass
    
    return {
        "status": "ok",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "chromium_binary_available": CHROMIUM_BINARY_AVAILABLE,
        "chromium_found": chromium_path is not None,
        "chromium_path": chromium_path,
        "playwright_browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        "cache_dir_listing": cache_files[:10],  # First 10 matches
        "supported_platforms": list(PLATFORM_PATTERNS.keys())
    }

if __name__ == "__main__":
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)