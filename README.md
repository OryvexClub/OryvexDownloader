# 🎬 Oryvex Media Downloader

**Oryvex Media Downloader** is a powerful, modern, and lightweight GUI application designed to extract and download high-quality media from the internet. Built with a sleek dark-glass interface, it provides a seamless experience for downloading videos, audio, and images from multiple popular platforms.

![Version](https://img.shields.io/badge/Version-3.9.4-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%2010/11-lightgrey)
![Status](https://img.shields.io/badge/Status-Stable-green)

---

## ✨ Key Features

- **Multi-Platform Support**: Download from YouTube, Spotify, SoundCloud, TikTok, Instagram, Twitter/X, Pinterest, and RadioJavan.
- **Advanced YouTube Engine**: 
  - Uses a combined multi-client approach (Web, TV, Android, iOS) to bypass restrictions and fetch the maximum number of formats.
  - Smart fallback system: If a specific quality fails, it automatically steps down to the next available resolution.
- **Granular Format Selection**: A dedicated UI dialog lets you inspect and choose exact resolutions, framerates, and bitrates before downloading.
- **Guaranteed Cleanup**: If a download is canceled or fails, Oryvex automatically cleans up all temporary and partial files, leaving no junk behind.
- **Smart Cookie Management**: Easily import `cookies.txt` or extract cookies directly from your browser to bypass age-restrictions, bot-checks, and YouTube's PO Token throttling.
- **Audio Extraction**: Built-in support for converting videos to high-quality MP3s (requires FFmpeg).
- **Modern Dark UI**: A beautiful, frameless, glass-morphism design with custom title bars, smooth animations, and SweetAlert-style notifications.

---

## 🌐 Supported Platforms

| Platform | Supported Content |
| :--- | :--- |
| 🎬 **YouTube** | Videos, Shorts, Playlists, Music |
| 🎵 **Spotify** | Tracks, Albums, Playlists (Matched via YouTube) |
| ☁️ **SoundCloud** | Tracks, Playlists |
| 🎶 **TikTok** | Videos, Slideshows |
| 📷 **Instagram** | Posts, Reels, Stories, IGTV |
| 🐦 **Twitter / X** | Tweets, Media, Spaces (Audio) |
| 📌 **Pinterest** | Images, Videos |
| 🎧 **RadioJavan** | MP3s, Music Videos, Podcasts, Albums |

---

## ⚙️ System Requirements & Prerequisites

To ensure Oryvex functions at 100% capacity, the following external tools must be installed on your system:

### 1. FFmpeg (Required for Merging & Audio)
FFmpeg is required to merge separate video and audio streams (which is how YouTube delivers high qualities like 1080p/4K) and to convert files to MP3.
* **How to install:** 
  * Via Windows Package Manager: `winget install Gyan.FFmpeg`
  * Or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add it to your system's `PATH`.

### 2. JavaScript Runtime (Required for YouTube)
YouTube uses complex JavaScript to sign video URLs. Oryvex needs a JS runtime to decrypt these signatures.
* **Supported Runtimes:** **Deno** (Recommended), **Node.js**, or **QuickJS**.
* **How to install Deno:** `winget install DenoLand.Deno` or via [deno.com](https://deno.com).
* *Note: If no JS runtime is found, Oryvex will still work, but some YouTube formats may be hidden or fall back to lower qualities.*

---

## 🚀 Installation & Usage

### Installation
1. Go to the **Releases** section of this repository.
2. Download the latest `OryvexDownloader-Setup.exe`.
3. Run the installer and follow the on-screen prompts. 
4. Launch **Oryvex Media Downloader** from your Start Menu or Desktop shortcut.

### Basic Usage
1. **Copy a URL** from any supported platform.
2. **Paste it** into the "Media URL" box on the Download page.
3. The app will automatically detect the platform and fetch available formats.
4. Select your desired quality from the popup dialog (or let it download the best available).
5. Click **Start Download** and monitor the progress in the Activity Log.

---

## 🍪 Managing YouTube Cookies

YouTube aggressively throttles downloads and hides high-quality formats if you are not logged in. Using cookies solves this.

### Why use cookies?
* Bypass "Sign in to confirm you're not a bot" errors.
* Access age-restricted content.
* Prevent YouTube from forcing lower-quality formats (PO Token issues).

### How to set up cookies:
1. **Option A (Browser Extraction):** Go to **Settings** -> Check **"Use cookies directly from a browser"** and select your browser (Chrome, Firefox, Edge, etc.). *Note: Close the browser completely before downloading.*
2. **Option B (cookies.txt):** 
   * Install a browser extension like **"Get cookies.txt LOCALLY"**.
   * Log into YouTube in your browser.
   * Export the cookies to a `.txt` file.
   * In Oryvex, go to the **Cookies** tab and click **Import cookies.txt**.

---

## 🛠️ Troubleshooting

**Q: The download fails with a "403 Forbidden" or "Bot Check" error.**
> **A:** YouTube has blocked the request. Go to the **Cookies** tab and import a fresh `cookies.txt` from a logged-in browser session, or enable browser cookie extraction in Settings.

**Q: I only see low-quality formats (e.g., 360p or 720p) for YouTube videos.**
> **A:** This usually happens for two reasons:
> 1. You don't have a JS Runtime (Deno/Node) installed, so Oryvex can't decrypt the high-quality signatures.
> 2. You are downloading without cookies. YouTube throttles anonymous users heavily. Add cookies to fix this.

**Q: The video has no sound, or the MP3 conversion failed.**
> **A:** FFmpeg is not installed or not added to your system's `PATH`. Please install FFmpeg and restart the application.

---

## ⚖️ Legal Disclaimer

*Oryvex Media Downloader is provided for educational and personal use only. The developers do not host any of the media content downloaded through this application. Users are solely responsible for ensuring they have the right to download the content and must comply with the Terms of Service of the respective platforms and local copyright laws.*

---

**© 2024 Oryvex Team. All Rights Reserved.**
