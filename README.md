# UniversalSave - Universal Video Downloader

UniversalSave is a fast, free, and secure web application to download videos from popular platforms including YouTube, TikTok, Twitter, Instagram, and more. 

Built with a premium modern frontend and a powerful FastAPI + `yt-dlp` backend, it allows you to search for videos directly or paste URLs to download them locally to your machine.

## Features

- **Multi-Platform Support**: Download videos from over 1,000+ websites globally.
- **Search & Fetch**: Search for videos directly from the interface or paste a URL to retrieve metadata.
- **Quality Selection**: Choose your preferred video quality (e.g., Best, 720p, 480p) or extract audio (MP3).
- **Real-Time Progress**: View live download progress directly on the UI.
- **Modern UI/UX**: Responsive design with Dark/Light mode toggle, dynamic feedback, and premium aesthetics.
- **Privacy-Focused**: No registration required, 100% safe and secure.

## Prerequisites

- [Python 3.8+](https://www.python.org/downloads/)
- [pip](https://pip.pypa.io/en/stable/installation/)

## Installation

1. **Clone or Download the Repository**
2. **Navigate to the Directory:**
   ```bash
   cd UniversalSave
   ```
3. **Create a Virtual Environment (Optional but recommended):**
   ```bash
   python -m venv .venv
   # On Windows:
   .venv\Scripts\activate
   # On Mac/Linux:
   source .venv/bin/activate
   ```
4. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   *Note: This will install `fastapi`, `uvicorn`, and `yt-dlp`.*

## Usage

1. **Start the Server:**
   Run the backend server using Python:
   ```bash
   python server.py
   ```

2. **Access the Application:**
   Open your modern web browser and navigate to:
   ```text
   http://localhost:8000
   ```

3. **Download Videos:**
   - **Search**: Enter a keyword and click "Search Videos" or press Enter.
   - **Download via URL**: Paste a valid video link, wait for validation ("🔗 URL detected"), and click "Download Now".
   - Select your desired video quality and hit "Download".
   - Videos will be automatically saved to your user downloads path: `~/Downloads/UniversalVideoDownloader`.

## Disclaimer

This tool is for educational and personal use only. Please respect the copyright of content creators and comply with the terms of service of the platforms you download from.
