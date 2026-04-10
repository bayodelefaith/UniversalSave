import os
import re
import asyncio
import glob
import subprocess
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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Set Playwright browsers path for Render
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
    """Detect platform and extract video ID from URL"""
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
    Find Chromium executable - updated for Playwright 1.58+ headless shell structure
    """
    # Base directories to search
    base_paths = [
        "/opt/render/.cache/ms-playwright",
        os.path.expanduser("~/.cache/ms-playwright"),
        "/home/render/.cache/ms-playwright",
        "/tmp/playwright-browsers",
    ]
    
    # 1. Check for headless shell first (Playwright 1.58+ default)
    for base in base_paths:
        if not os.path.exists(base):
            continue
            
        # Look for chromium_headless_shell directories
        try:
            for item in os.listdir(base):
                if "headless_shell" in item.lower() or "headless-shell" in item.lower():
                    headless_dir = os.path.join(base, item)
                    # Check for chrome-headless-shell-linux64 subdirectory
                    linux64_dir = os.path.join(headless_dir, "chrome-headless-shell-linux64")
                    if os.path.exists(linux64_dir):
                        executable = os.path.join(linux64_dir, "chrome-headless-shell")
                        if os.path.exists(executable):
                            print(f"✅ Found headless shell: {executable}")
                            return executable
                    
                    # Check direct subdirectory
                    for subdir in ["chrome-linux64", "chrome-linux"]:
                        subdir_path = os.path.join(headless_dir, subdir)
                        if os.path.exists(subdir_path):
                            for exe_name in ["chrome-headless-shell", "chrome"]:
                                executable = os.path.join(subdir_path, exe_name)
                                if os.path.exists(executable):
                                    print(f"✅ Found headless shell: {executable}")
                                    return executable
        except Exception as e:
            print(f"⚠️ Error searching {base}: {e}")
    
    # 2. Check for regular Chromium installations
    for base in base_paths:
        if not os.path.exists(base):
            continue
            
        try:
            for item in os.listdir(base):
                if item.startswith("chromium-") and "headless" not in item.lower():
                    chromium_dir = os.path.join(base, item)
                    for subdir in ["chrome-linux64", "chrome-linux"]:
                        subdir_path = os.path.join(chromium_dir, subdir)
                        if os.path.exists(subdir_path):
                            executable = os.path.join(subdir_path, "chrome")
                            if os.path.exists(executable):
                                print(f"✅ Found regular Chromium: {executable}")
                                return executable
        except Exception as e:
            print(f"⚠️ Error searching {base}: {e}")
    
    # 3. Use glob to find any chrome executable
    for base in base_paths:
        if not os.path.exists(base):
            continue
            
        try:
            pattern = os.path.join(base, "**", "chrome*")
            matches = glob.glob(pattern, recursive=True)
            for match in matches:
                # Skip .zip, .so, and other non-executable files
                if os.path.isfile(match) and not match.endswith(('.zip', '.so', '.tar.gz')):
                    # Check if executable
                    if os.access(match, os.X_OK):
                        print(f"✅ Found via glob: {match}")
                        return match
                    # Try to make executable
                    try:
                        os.chmod(match, 0o755)
                        if os.access(match, os.X_OK):
                            print(f"✅ Found via glob (fixed permissions): {match}")
                            return match
                    except:
                        pass
        except Exception as e:
            print(f"⚠️ Glob error in {base}: {e}")
    
    # 4. Check system paths
    system_paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chrome",
    ]
    
    for path in system_paths:
        if os.path.exists(path):
            print(f"✅ Found system Chromium: {path}")
            return path
    
    # 5. Try to find in PATH
    try:
        result = subprocess.run(["which", "chromium"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            print(f"✅ Found in PATH: {path}")
            return path
    except:
        pass
    
    try:
        result = subprocess.run(["which", "google-chrome"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            print(f"✅ Found in PATH: {path}")
            return path
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
        return {"error": "Chromium not found. Please check installation."}
    
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
            
            # Use explicit executable path
            launch_options["executable_path"] = chromium_path
            
            # Log which browser we're using
            if "headless" in chromium_path.lower():
                print("🚀 Using Chromium Headless Shell (optimized)")
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
            
            # Navigate with timeout handling
            try:
                await page.goto(url, wait_until='networkidle', timeout=45000)
            except:
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            
            await asyncio.sleep(3)
            
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
    """Fetch video info using Playwright or fallback"""
    if not url.startswith(('http://', 'https://')):
        return {"error": "Invalid URL. Must start with http:// or https://"}
    
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
    """Generate fresh download URL"""
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
    """Check system health"""
    chromium_path = find_chromium_executable()
    
    # Check cache directory structure
    cache_info = {}
    for base in ["/opt/render/.cache/ms-playwright", os.path.expanduser("~/.cache/ms-playwright")]:
        if os.path.exists(base):
            try:
                cache_info[base] = os.listdir(base)
            except Exception as e:
                cache_info[base] = f"Error: {str(e)}"
        else:
            cache_info[base] = "Not found"
    
    return {
        "status": "ok",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "chromium_found": chromium_path is not None,
        "chromium_path": chromium_path,
        "playwright_browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        "cache_directories": cache_info,
        "supported_platforms": list(PLATFORM_PATTERNS.keys())
    }

@app.get("/debug/chromium")
def debug_chromium():
    """Debug endpoint to inspect Chromium installation"""
    import glob
    
    results = {
        "searched_paths": [],
        "found_executables": [],
        "cache_contents": {},
    }
    
    # Search all possible locations
    base_paths = [
        "/opt/render/.cache/ms-playwright",
        os.path.expanduser("~/.cache/ms-playwright"),
        "/home/render/.cache/ms-playwright",
        "/tmp/playwright-browsers",
    ]
    
    for base in base_paths:
        results["searched_paths"].append(base)
        
        if not os.path.exists(base):
            results["cache_contents"][base] = "Directory does not exist"
            continue
        
        try:
            items = os.listdir(base)
            results["cache_contents"][base] = items
            
            # Look for chrome executables
            for pattern in ["**/chrome*", "**/headless*"]:
                full_pattern = os.path.join(base, pattern)
                matches = glob.glob(full_pattern, recursive=True)
                for match in matches:
                    if os.path.isfile(match):
                        is_executable = os.access(match, os.X_OK)
                        results["found_executables"].append({
                            "path": match,
                            "executable": is_executable,
                            "size": os.path.getsize(match) if is_executable else None
                        })
        except Exception as e:
            results["cache_contents"][base] = f"Error: {str(e)}"
    
    # Also check which command
    try:
        which_result = subprocess.run(["which", "chromium"], capture_output=True, text=True)
        results["which_chromium"] = which_result.stdout.strip() if which_result.returncode == 0 else "Not in PATH"
    except:
        results["which_chromium"] = "which command failed"
    
    return results

if __name__ == "__main__":
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)