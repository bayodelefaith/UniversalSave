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
    Find Chromium executable - searches all possible Playwright installation paths
    """
    # All possible base directories
    base_paths = [
        "/opt/render/.cache/ms-playwright",
        os.path.expanduser("~/.cache/ms-playwright"),
        "/home/render/.cache/ms-playwright",
        "/tmp/playwright-browsers",
        "/opt/render/.local/share/ms-playwright",
        os.path.expanduser("~/.local/share/ms-playwright"),
    ]
    
    # First, try to use Playwright's built-in path detection via CLI
    try:
        result = subprocess.run(
            ["python", "-m", "playwright", "chromium", "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        # If this works, Playwright knows where Chromium is
        print("✅ Playwright CLI is working")
    except Exception as e:
        print(f"⚠️ Playwright CLI check failed: {e}")
    
    # Search for headless shell (Playwright 1.58+ default)
    for base in base_paths:
        if not os.path.exists(base):
            continue
        
        print(f"🔍 Searching in: {base}")
        
        try:
            # Look for any directory containing "headless"
            for item in os.listdir(base):
                item_path = os.path.join(base, item)
                if not os.path.isdir(item_path):
                    continue
                
                # Check if this is a headless shell directory
                if "headless" in item.lower():
                    print(f"  Found headless dir: {item}")
                    
                    # Check subdirectories
                    for subdir in os.listdir(item_path):
                        subdir_path = os.path.join(item_path, subdir)
                        if not os.path.isdir(subdir_path):
                            continue
                        
                        # Look for chrome-headless-shell executable
                        for exe_name in ["chrome-headless-shell", "chrome"]:
                            exe_path = os.path.join(subdir_path, exe_name)
                            if os.path.exists(exe_path) and os.path.isfile(exe_path):
                                # Make sure it's executable
                                if not os.access(exe_path, os.X_OK):
                                    try:
                                        os.chmod(exe_path, 0o755)
                                    except:
                                        pass
                                if os.access(exe_path, os.X_OK):
                                    print(f"✅ Found headless shell: {exe_path}")
                                    return exe_path
        except Exception as e:
            print(f"  Error: {e}")
    
    # Search for regular Chromium
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
                            exe_path = os.path.join(subdir_path, "chrome")
                            if os.path.exists(exe_path):
                                if not os.access(exe_path, os.X_OK):
                                    try:
                                        os.chmod(exe_path, 0o755)
                                    except:
                                        pass
                                if os.access(exe_path, os.X_OK):
                                    print(f"✅ Found regular Chromium: {exe_path}")
                                    return exe_path
        except Exception as e:
            print(f"Error: {e}")
    
    # Use glob as last resort
    for base in base_paths:
        if not os.path.exists(base):
            continue
        
        try:
            pattern = os.path.join(base, "**", "chrome*")
            matches = glob.glob(pattern, recursive=True)
            for match in matches:
                if os.path.isfile(match) and not match.endswith(('.zip', '.so', '.tar.gz', '.json')):
                    if "headless" in match.lower() or match.endswith("/chrome"):
                        if not os.access(match, os.X_OK):
                            try:
                                os.chmod(match, 0o755)
                            except:
                                pass
                        if os.access(match, os.X_OK):
                            print(f"✅ Found via glob: {match}")
                            return match
        except Exception as e:
            print(f"Glob error: {e}")
    
    # System paths
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
    
    # Try which command
    try:
        for cmd in ["chromium", "chromium-browser", "google-chrome", "chrome"]:
            result = subprocess.run(["which", cmd], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                print(f"✅ Found via which: {path}")
                return path
    except:
        pass
    
    print("❌ No Chromium found anywhere")
    return None

async def scrape_with_playwright(url: str, platform: str) -> dict:
    """Use Playwright to extract video info"""
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright not installed"}
    
    chromium_path = find_chromium_executable()
    
    if not chromium_path:
        return {"error": "Chromium not found. Please run: python -m playwright install chromium"}
    
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
            
            # Always use explicit path
            launch_options["executable_path"] = chromium_path
            
            print(f"🚀 Launching browser: {chromium_path}")
            
            try:
                browser = await p.chromium.launch(**launch_options)
            except Exception as launch_error:
                print(f"❌ Launch failed: {launch_error}")
                return {"error": f"Failed to launch browser: {str(launch_error)}"}
            
            print("✅ Browser launched successfully")
            
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
    
    # Debug: list what's in the cache directory
    cache_debug = {}
    for base in ["/opt/render/.cache/ms-playwright", os.path.expanduser("~/.cache/ms-playwright")]:
        if os.path.exists(base):
            try:
                cache_debug[base] = os.listdir(base)
            except Exception as e:
                cache_debug[base] = str(e)
        else:
            cache_debug[base] = "NOT_FOUND"
    
    return {
        "status": "ok",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "chromium_found": chromium_path is not None,
        "chromium_path": chromium_path,
        "cache_debug": cache_debug,
        "supported_platforms": list(PLATFORM_PATTERNS.keys())
    }

@app.get("/debug/chromium")
def debug_chromium():
    """Detailed debug of Chromium installation"""
    import glob
    
    results = {
        "environment": {
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
            "HOME": os.environ.get("HOME"),
            "PWD": os.environ.get("PWD"),
        },
        "searched_paths": [],
        "found_items": [],
    }
    
    base_paths = [
        "/opt/render/.cache/ms-playwright",
        os.path.expanduser("~/.cache/ms-playwright"),
        "/home/render/.cache/ms-playwright",
        "/tmp/playwright-browsers",
    ]
    
    for base in base_paths:
        results["searched_paths"].append(base)
        
        if not os.path.exists(base):
            results["found_items"].append(f"{base}: DOES_NOT_EXIST")
            continue
        
        try:
            items = os.listdir(base)
            results["found_items"].append(f"{base}: {items}")
            
            # Deep search for executables
            for root, dirs, files in os.walk(base):
                for file in files:
                    if "chrome" in file.lower() and not file.endswith(('.zip', '.so', '.tar.gz')):
                        full_path = os.path.join(root, file)
                        is_exe = os.access(full_path, os.X_OK)
                        results["found_items"].append(f"  EXE: {full_path} (executable: {is_exe})")
        except Exception as e:
            results["found_items"].append(f"{base}: ERROR - {str(e)}")
    
    # Also try to find using find command
    try:
        result = subprocess.run(
            ["find", "/opt/render", "-name", "chrome*", "-type", "f", "2>/dev/null"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.stdout:
            results["find_command"] = result.stdout.strip().split("\n")[:20]
    except Exception as e:
        results["find_command"] = f"Error: {str(e)}"
    
    return results

if __name__ == "__main__":
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)