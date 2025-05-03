#
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# TikTok Livestream Tracker & Recorder Bot (Enhanced Admin-Only Version)
# Features:
# - Admin-only access control
# - Full inline button support
# - Record list and active recording features
# - Multiple simultaneous recording support
# - Enhanced compression for large files
# - Detailed notifications and logging
# - Telethon integration for sending videos

# Token bot Telegram (ganti dengan token Anda)
TELEGRAM_BOT_TOKEN = "7839177497:AAF4kpg0ezNzIS32NyYHTWunQVU3TRtJcWQ"

# Telethon API info (add your own)
API_ID = 25649945  # Replace with your Telegram API ID
API_HASH = "d91f3e307f5ee75e57136421f2c3adc6"  # Replace with your Telegram API Hash

# List of admin user IDs (ganti dengan ID admin Anda)
ADMIN_IDS = [5988451717]  # Replace with your actual admin Telegram user IDs

import os
import json
import time
import logging
import asyncio
import requests
import subprocess
import re
import threading
import shutil
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from functools import wraps

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

# Telethon imports for sending videos
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo
import telethon.sync

# Konfigurasi logging
logging.basicConfig(
    format='[*] %(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Pastikan direktori yang diperlukan ada
RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(exist_ok=True)

FINISHED_RECORDINGS_DIR = Path("finished_recordings") 
FINISHED_RECORDINGS_DIR.mkdir(exist_ok=True)

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

DB_FILE = Path("database.json")
COOKIES_FILE = Path("cookies.json")

# Initialize Telethon client
telethon_client = None
telethon_loop = None

# Class buat handle Telethon loop di background
class TelethonLoopRunner:
    def __init__(self):
        self.thread = None
        self.running = False
        
    def start(self):
        if self.running:
            return
        
        def run_loop():
            global telethon_loop
            asyncio.set_event_loop(telethon_loop)
            telethon_loop.run_forever()
            
        self.thread = threading.Thread(target=run_loop, daemon=True)
        self.thread.start()
        self.running = True
        
    def stop(self):
        if not self.running:
            return
        telethon_loop.call_soon_threadsafe(telethon_loop.stop)
        self.thread.join(timeout=5)
        self.running = False

telethon_runner = None

# Function buat jalanin fungsi async di loop Telethon dari thread lain
def run_in_telethon_loop(coro):
    if asyncio.get_event_loop() == telethon_loop:
        # Jika sudah di telethon_loop, jalankan langsung
        return telethon_loop.run_until_complete(coro)
    else:
        # Kalau dari thread lain, jadwalkan ke telethon_loop
        future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
        return future.result(timeout=180)  # 3 menit timeout untuk handle file gede

def initialize_telethon():
    global telethon_client, telethon_loop
    # Buat satu event loop dedicated khusus untuk Telethon
    telethon_loop = asyncio.new_event_loop()
    # Buat client dengan loop yang dedicated
    telethon_client = TelegramClient('tiktok_recorder_session', API_ID, API_HASH, loop=telethon_loop)
    # Connect client di loop yang sama
    telethon_loop.run_until_complete(telethon_client.connect())
    if not telethon_loop.run_until_complete(telethon_client.is_user_authorized()):
        logger.warning("Telethon client not authorized. Please run the auth script first.")
    else:
        logger.info("Telethon client connected and authorized successfully!")

# Enum Mode
class Mode:
    MANUAL = 0
    AUTOMATIC = 1

# Enum TimeOut
class TimeOut:
    ONE_MINUTE = 60
    CONNECTION_CLOSED = 2

# HTTP Status Codes
class StatusCode:
    OK = 200
    REDIRECT = 302
    MOVED = 301

# TikTok Error Messages
class TikTokError:
    USER_NOT_CURRENTLY_LIVE = "The user is not hosting a live stream at the moment."
    RETRIEVE_LIVE_URL = "Unable to retrieve live streaming url. Please try again later."
    ROOM_ID_ERROR = "Error extracting RoomID"
    ACCOUNT_PRIVATE = "Account is private, login required."
    COUNTRY_BLACKLISTED = "Captcha required or country blocked. Use a VPN, room_id, or authenticate with cookies."
    WAF_BLOCKED = "Your IP is blocked by TikTok WAF. Please change your IP address."
    LIVE_RESTRICTION = "Live is private, login required."

# Custom Exceptions
class TikTokException(Exception):
    pass

class UserLiveException(Exception):
    pass

class LiveNotFound(Exception):
    pass

class IPBlockedByWAF(Exception):
    pass

# Inisialisasi database
def init_database():
    if not DB_FILE.exists():
        with open(DB_FILE, "w") as f:
            json.dump({
                "users": {},
                "tracked_accounts": {},
                "finished_recordings": []
            }, f, indent=2)
    
    with open(DB_FILE, "r") as f:
        return json.load(f)

# Simpan database
def save_database(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

# Inisialisasi cookies
def init_cookies():
    if not COOKIES_FILE.exists():
        with open(COOKIES_FILE, "w") as f:
            json.dump({
                "sessionid_ss": "",
                "tt-target-idc": "useast2a"
            }, f, indent=2)
    
    with open(COOKIES_FILE, "r") as f:
        return json.load(f)

# Check if user is admin
def is_admin(user_id):
    return user_id in ADMIN_IDS

# Admin-only decorator for command handlers
# Fixed to handle variable number of arguments
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("⛔ This bot is for admin use only.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# Format time duration
def format_duration(seconds):
    """Format duration in seconds to HH:MM:SS format"""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"

# Helper to create event loop for threads
def get_or_create_eventloop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError as ex:
        if "There is no current event loop in thread" in str(ex):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop
        raise

# Helper function to run async tasks from non-async code
def run_async(coro):
    loop = get_or_create_eventloop()
    return loop.run_until_complete(coro)

def is_recording_active(username):
    """Check if there's an actual recording process running for this username"""
    try:
        if os.name == 'nt':  # Windows
            output = subprocess.check_output(f'tasklist /FI "IMAGENAME eq ffmpeg.exe"', shell=True).decode()
            return username in output
        else:  # Linux/Mac
            # Use more specific grep to avoid false positives
            output = subprocess.check_output(f"ps aux | grep ffmpeg | grep {username} | grep -v grep", shell=True).decode()
            return bool(output.strip())  # If any output, recording is active
    except subprocess.CalledProcessError:
        # This happens when grep doesn't find anything - not an error
        return False
    except Exception as e:
        logger.error(f"Error checking recording status for {username}: {e}")
        return False  # If error, assume not recording

async def check_active_recordings_on_startup(application):
    """Setup job queue and check for any recordings that should be running"""
    # Set up the regular check job first
    await setup_job_queue(application)
    
    # Now check for any recordings that were active before restart
    db = init_database()
    
    logger.info("Checking for active recordings after startup...")
    
    for username, account_info in db["tracked_accounts"].items():
        # If marked as recording but process not running
        if account_info.get("is_recording", False) and not is_recording_active(username):
            logger.info(f"Found interrupted recording for {username}. Verifying live status...")
            
            try:
                # Initialize API
                cookies = init_cookies()
                api = TikTokAPI(cookies=cookies)
                
                # Get room ID
                room_id = account_info.get("room_id", "") or api.get_room_id_from_user(username)
                
                if not room_id:
                    logger.error(f"Could not find room ID for {username}")
                    # Update database to mark as not recording
                    account_info["is_recording"] = False
                    account_info["last_error"] = "Room ID not found after restart"
                    save_database(db)
                    continue
                
                # Check if still live
                is_live = api.is_room_alive(room_id)
                
                if is_live:
                    logger.info(f"{username} is still live. Restarting recording...")
                    
                    # Update room ID in case it changed
                    account_info["room_id"] = room_id
                    save_database(db)
                    
                    # Start recording in a new thread
                    await start_recording_for_account(username, application)
                    
                    # Notify admins
                    for admin_id in ADMIN_IDS:
                        try:
                            await application.bot.send_message(
                                chat_id=admin_id,
                                text=f"🔄 Bot restarted and found @{username} is still live!\n"
                                     f"📹 Recording has been resumed automatically."
                            )
                        except Exception as e:
                            logger.error(f"Error sending restart notification: {e}")
                else:
                    logger.info(f"{username} is no longer live. Updating status...")
                    # Update database to mark as not recording and not live
                    account_info["is_recording"] = False
                    account_info["is_live"] = False
                    account_info["last_error"] = "Stream ended while bot was down"
                    save_database(db)
                    
                    # Notify admins
                    for admin_id in ADMIN_IDS:
                        try:
                            await application.bot.send_message(
                                chat_id=admin_id,
                                text=f"ℹ️ Bot restarted and found @{username} is no longer live.\n"
                                     f"📋 Recording status has been updated."
                            )
                        except Exception as e:
                            logger.error(f"Error sending restart notification: {e}")
            except Exception as e:
                logger.error(f"Error checking {username} after restart: {e}")
                # Update status to avoid stuck recordings
                account_info["is_recording"] = False
                account_info["last_error"] = f"Error after restart: {str(e)}"
                save_database(db)
    
    # Schedule a check for all tracked accounts after startup
    application.job_queue.run_once(check_tracked_accounts_job, 30)
    
    logger.info("Startup check complete")
        

# HTTP Client
class HttpClient:
    def __init__(self, proxy=None, cookies=None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.127 Safari/537.36",
            "Accept-Language": "en-US",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Ch-Ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\"",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": "\"Linux\"",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Referer": "https://www.tiktok.com/"
        })
        
        if cookies:
            self.session.cookies.update(cookies)
            
        if proxy:
            logger.info(f"Testing proxy: {proxy}...")
            try:
                response = requests.get(
                    "https://ifconfig.me/ip",
                    proxies={"http": proxy, "https": proxy},
                    timeout=10
                )
                
                if response.status_code == StatusCode.OK:
                    self.session.proxies.update({"http": proxy, "https": proxy})
                    logger.info("Proxy set up successfully")
            except Exception as e:
                logger.error(f"Error setting up proxy: {e}")

# TikTok API
class TikTokAPI:
    def __init__(self, proxy=None, cookies=None):
        self.BASE_URL = 'https://www.tiktok.com'
        self.WEBCAST_URL = 'https://webcast.tiktok.com'
        self.http_client = HttpClient(proxy, cookies).session
    
    def is_country_blacklisted(self) -> bool:
        """Checks if the user is in a blacklisted country that requires login"""
        response = self.http_client.get(
            f"{self.BASE_URL}/live",
            allow_redirects=False
        )
        
        return response.status_code == StatusCode.REDIRECT
    
    def is_room_alive(self, room_id: str) -> bool:
        """Checking whether the user is live."""
        if not room_id:
            raise UserLiveException(TikTokError.USER_NOT_CURRENTLY_LIVE)

        try:
            data = self.http_client.get(
                f"{self.WEBCAST_URL}/webcast/room/check_alive/"
                f"?aid=1988&region=CH&room_ids={room_id}&user_is_login=true"
            ).json()
            
            if 'data' not in data or len(data['data']) == 0:
                return False
                
            return data['data'][0].get('alive', False)
        except Exception as e:
            logger.error(f"Error checking if room {room_id} is alive: {e}")
            return False
    
    def get_user_from_room_id(self, room_id: str) -> str:
        """Given a room_id, I get the username"""
        try:
            data = self.http_client.get(
                f"{self.WEBCAST_URL}/webcast/room/info/?aid=1988&room_id={room_id}"
            ).json()
            
            if 'This account is private' in str(data):
                raise UserLiveException(TikTokError.ACCOUNT_PRIVATE)
                
            display_id = data.get("data", {}).get("owner", {}).get("display_id")
            if display_id is None:
                raise TikTokException("Username error")
                
            return display_id
        except Exception as e:
            logger.error(f"Error getting user from room ID {room_id}: {e}")
            return ""
    
    def expand_shortlink(self, url: str) -> str:
        """Expand TikTok short URL to full URL"""
        try:
            response = self.http_client.get(url, allow_redirects=False)
            if response.status_code in (StatusCode.REDIRECT, StatusCode.MOVED):
                return response.headers.get('Location', url)
            return url
        except Exception as e:
            logger.error(f"Error expanding short URL {url}: {e}")
            return url
    
    def get_room_and_user_from_url(self, live_url: str):
        """Given a url, get user and room_id."""
        try:
            # Check if it's a short URL and expand it
            if "vt.tiktok.com" in live_url:
                live_url = self.expand_shortlink(live_url)
            
            response = self.http_client.get(live_url, allow_redirects=True)
            content = response.text
            
            if response.status_code == StatusCode.REDIRECT:
                raise UserLiveException(TikTokError.COUNTRY_BLACKLISTED)
                
            # https://www.tiktok.com/@<username>/live
            match = re.search(r"tiktok\.com/@([^/?&]+)(?:/live)?", live_url)
            if not match:
                raise LiveNotFound(TikTokError.RETRIEVE_LIVE_URL)
                
            user = match.group(1)
            room_id = self.get_room_id_from_user(user)
            
            return user, room_id
        except Exception as e:
            logger.error(f"Error getting room and user from URL {live_url}: {e}")
            raise
    
    def get_room_id_from_user(self, user: str) -> str:
        """Given a username, I get the room_id"""
        try:
            content = self.http_client.get(
                url=f'{self.BASE_URL}/@{user}/live'
            ).text
            
            if 'Please wait...' in content:
                raise IPBlockedByWAF()
                
            pattern = re.compile(
                r'<script id="SIGI_STATE" type="application/json">(.*?)</script>',
                re.DOTALL)
            match = pattern.search(content)
            
            if match is None:
                raise UserLiveException(TikTokError.ROOM_ID_ERROR)
                
            data = json.loads(match.group(1))
            
            if 'LiveRoom' not in data and 'CurrentRoom' in data:
                return ""
                
            room_id = data.get('LiveRoom', {}).get('liveRoomUserInfo', {}).get(
                'user', {}).get('roomId', None)
                
            if room_id is None:
                raise UserLiveException(TikTokError.ROOM_ID_ERROR)
                
            return room_id
        except Exception as e:
            if isinstance(e, (UserLiveException, IPBlockedByWAF)):
                raise
            logger.error(f"Error getting room ID from user {user}: {e}")
            return ""
    
    def get_live_url(self, room_id: str) -> str:
        """Return the cdn (flv or m3u8) of the streaming"""
        try:
            data = self.http_client.get(
                f"{self.WEBCAST_URL}/webcast/room/info/?aid=1988&room_id={room_id}"
            ).json()
            
            if 'This account is private' in str(data):
                raise UserLiveException(TikTokError.ACCOUNT_PRIVATE)
                
            stream_url = data.get('data', {}).get('stream_url', {})
            
            # Get the best quality available
            live_url_flv = (
                stream_url.get('flv_pull_url', {}).get('FULL_HD1') or
                stream_url.get('flv_pull_url', {}).get('HD1') or
                stream_url.get('flv_pull_url', {}).get('SD2') or
                stream_url.get('flv_pull_url', {}).get('SD1')
            )
            
            # If flv_pull_url is not available, use rtmp_pull_url
            if not live_url_flv:
                live_url_flv = stream_url.get('rtmp_pull_url', None)
                
            if not live_url_flv and data.get('status_code') == 4003110:
                raise UserLiveException(TikTokError.LIVE_RESTRICTION)
                
            logger.info(f"Live URL: {live_url_flv}")
            
            return live_url_flv
        except Exception as e:
            if isinstance(e, UserLiveException):
                raise
            logger.error(f"Error getting live URL for room {room_id}: {e}")
            return ""
    
    def download_live_stream(self, live_url: str):
        """Generator yang mengembalikan streaming live untuk URL yang diberikan."""
        try:
            stream = self.http_client.get(live_url, stream=True)
            for chunk in stream.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                    
                yield chunk
        except Exception as e:
            logger.error(f"Error downloading live stream: {e}")
            raise

# Video Management
class VideoManagement:
    @staticmethod
    def convert_flv_to_mp4(file_path: str):
        """Convert FLV to MP4 format"""
        logger.info(f"Converting {file_path} to MP4 format...")
        
        try:
            output_file = file_path.replace('_flv.mp4', '.mp4')
            
            # Use ffmpeg to convert
            subprocess.run([
                "ffmpeg",
                "-i", file_path,
                "-c", "copy",
                "-y", output_file
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Remove original file
            os.remove(file_path)
            
            logger.info(f"Finished converting to {output_file}")
            return output_file
        except Exception as e:
            logger.error(f"Error converting video: {e}")
            return file_path
    
    @staticmethod
    def get_video_info(input_file: str):
        """Get video info using ffprobe"""
        try:
            # Get video info
            probe_cmd = [
                "ffprobe", 
                "-v", "error", 
                "-show_entries", "format=duration,size:stream=width,height,codec_type", 
                "-of", "json", 
                input_file
            ]
            
            probe_result = subprocess.run(
                probe_cmd, 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            probe_data = json.loads(probe_result.stdout)
            duration = float(probe_data["format"]["duration"])
            size_bytes = int(probe_data["format"]["size"])
            
            # Find video stream
            video_stream = None
            for stream in probe_data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break
            
            if video_stream:
                width = int(video_stream.get("width", 0))
                height = int(video_stream.get("height", 0))
            else:
                width = 0
                height = 0
            
            return {
                "duration": duration,
                "size_bytes": size_bytes,
                "width": width,
                "height": height
            }
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            return {
                "duration": 0,
                "size_bytes": os.path.getsize(input_file) if os.path.exists(input_file) else 0,
                "width": 0,
                "height": 0
            }
    
    @staticmethod
    def compress_video(input_file: str, target_size_mb: int = 2000):
        """Compress video to target size in MB while maintaining quality"""
        logger.info(f"Compressing {input_file} to target size {target_size_mb}MB...")
        
        try:
            # Get video info
            video_info = VideoManagement.get_video_info(input_file)
            duration = video_info["duration"]
            original_size = video_info["size_bytes"]
            original_size_mb = original_size / (1024 * 1024)
            width = video_info["width"]
            height = video_info["height"]
            
            # Calculate target bitrate (90% of ideal to account for overhead)
            target_size_bytes = target_size_mb * 1024 * 1024 * 0.9
            target_bitrate = int((target_size_bytes * 8) / duration)
            
            logger.info(f"Original: {original_size_mb:.2f}MB, Duration: {duration:.2f}s, Resolution: {width}x{height}")
            logger.info(f"Target bitrate: {target_bitrate/1000:.2f}kbps")
            
            # Output file name
            output_file = input_file.replace('.mp4', f'_compressed_{target_size_mb}MB.mp4')
            
            # Adjust resolution if needed for better compression
            resolution_args = []
            if width > 1280 and height > 720:
                # Scale down to 720p for better compression
                resolution_args = ["-vf", "scale=1280:720"]
                logger.info("Scaling down to 720p for better compression")
            
            # Compress with calculated bitrate
            compress_cmd = [
                "ffmpeg",
                "-i", input_file,
                "-c:v", "libx264",
                "-b:v", f"{target_bitrate}",
                "-maxrate", f"{target_bitrate * 1.5}",
                "-bufsize", f"{target_bitrate * 2}",
                "-preset", "medium",  # Balance between quality and speed
                "-c:a", "aac",
                "-b:a", "128k",
            ]
            
            # Add resolution args if needed
            if resolution_args:
                compress_cmd.extend(resolution_args)
                
            # Add output file
            compress_cmd.extend(["-y", output_file])
            
            subprocess.run(compress_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Verify compressed size
            compressed_size = os.path.getsize(output_file)
            compressed_size_mb = compressed_size / (1024 * 1024)
            
            logger.info(f"Compressed file size: {compressed_size_mb:.2f}MB")
            
            # If still too large, compress again with more aggressive settings
            if compressed_size_mb > target_size_mb and target_size_mb < 1900:
                logger.info("File still too large, trying more aggressive compression...")
                os.remove(output_file)  # Remove the first attempt
                
                # Try more aggressive compression
                more_aggressive_cmd = [
                    "ffmpeg",
                    "-i", input_file,
                    "-c:v", "libx264",
                    "-b:v", f"{target_bitrate * 0.6}",  # 60% of original target
                    "-maxrate", f"{target_bitrate}",
                    "-bufsize", f"{target_bitrate * 1.5}",
                    "-preset", "medium",
                    "-c:a", "aac",
                    "-b:a", "96k",
                    "-vf", "scale=960:540",  # Scale down more aggressively
                    "-y", output_file
                ]
                
                subprocess.run(more_aggressive_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                # Check new size
                compressed_size = os.path.getsize(output_file)
                compressed_size_mb = compressed_size / (1024 * 1024)
                logger.info(f"More aggressively compressed size: {compressed_size_mb:.2f}MB")
            
            return output_file
        except Exception as e:
            logger.error(f"Error compressing video: {e}")
            return input_file

# TikTok Recorder
class TikTokRecorder:
    def __init__(
        self,
        url,
        user,
        room_id,
        mode,
        automatic_interval,
        cookies,
        proxy,
        output,
        duration,
        use_telegram,
        context=None
    ):
        # Setup TikTok API client
        self.tiktok = TikTokAPI(proxy=proxy, cookies=cookies)
        
        # TikTok Data
        self.url = url
        self.user = user
        self.room_id = room_id
        
        # Tool Settings
        self.mode = mode
        self.automatic_interval = automatic_interval
        self.duration = duration
        self.output = output
        
        # Telegram context
        self.context = context
        
        # Upload Settings
        self.use_telegram = use_telegram
        
        # Cleanup class variables
        self.buffer = bytearray()
        self.buffer_size = 512 * 1024  # 512 KB
        self.stop_recording = False
        self.output_file = None
        
        # Check if the user's country is blacklisted
        self.check_country_blacklisted()
        
        # Get live information based on the provided user data
        if self.url:
            self.user, self.room_id = self.tiktok.get_room_and_user_from_url(self.url)
            
        if not self.user and self.room_id:
            self.user = self.tiktok.get_user_from_room_id(self.room_id)
            
        if not self.room_id and self.user:
            self.room_id = self.tiktok.get_room_id_from_user(self.user)
            
        logger.info(f"USERNAME: {self.user}")
        if self.room_id:
            logger.info(f"ROOM_ID: {self.room_id}")
            is_live = self.tiktok.is_room_alive(self.room_id)
            logger.info(f"Status: {'LIVE' if is_live else 'OFFLINE'}")
        
        # If proxy is provided, set up the HTTP client without the proxy for recording
        if proxy:
            self.tiktok = TikTokAPI(proxy=None, cookies=cookies)
            
    def run(self):
        """Runs the program in the selected mode."""
        if self.mode == Mode.MANUAL:
            return self.manual_mode()
            
        if self.mode == Mode.AUTOMATIC:
            return self.automatic_mode()
            
    def manual_mode(self):
        """Check once and record if live"""
        if not self.tiktok.is_room_alive(self.room_id):
            raise UserLiveException(
                f"@{self.user}: {TikTokError.USER_NOT_CURRENTLY_LIVE}"
            )
            
        return self.start_recording()
        
    def automatic_mode(self):
        """Continuously check if user is live"""
        while True:
            try:
                self.room_id = self.tiktok.get_room_id_from_user(self.user)
                output_file = self.manual_mode()
                return output_file
                
            except UserLiveException as ex:
                logger.info(str(ex))
                logger.info(f"Waiting {self.automatic_interval} minutes before recheck\n")
                time.sleep(self.automatic_interval * TimeOut.ONE_MINUTE)
                
            except ConnectionError:
                logger.error("Connection closed in automatic mode")
                time.sleep(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)
                
            except Exception as ex:
                logger.error(f"Unexpected error: {ex}\n")
                time.sleep(TimeOut.ONE_MINUTE)
                
    def start_recording(self):
        """Start recording live"""
        live_url = self.tiktok.get_live_url(self.room_id)
        if not live_url:
            raise LiveNotFound(TikTokError.RETRIEVE_LIVE_URL)
            
        current_date = time.strftime("%Y.%m.%d_%H-%M-%S", time.localtime())
        
        if isinstance(self.output, str) and self.output != '':
            if not (self.output.endswith('/') or self.output.endswith('\\')):
                if os.name == 'nt':
                    self.output = self.output + "\\"
                else:
                    self.output = self.output + "/"
                    
        output = f"{self.output if self.output else ''}TK_{self.user}_{current_date}_flv.mp4"
        self.output_file = output
        
        if self.duration:
            logger.info(f"Started recording for {self.duration} seconds")
        else:
            logger.info("Started recording...")
            
        self.buffer = bytearray()
        self.stop_recording = False
        
        logger.info("[PRESS CTRL + C ONCE TO STOP]")
        
        # Update database to mark user as recording
        db = init_database()
        if self.user in db["tracked_accounts"]:
            db["tracked_accounts"][self.user]["is_recording"] = True
            db["tracked_accounts"][self.user]["recording_start_time"] = datetime.now().isoformat()
            db["tracked_accounts"][self.user]["current_recording_file"] = output
            save_database(db)
        
        # Send notification to admin users
        if self.context:
            for admin_id in ADMIN_IDS:
                try:
                    # Create proper notification message
                    notification_message = (
                        f"🔴 Started recording livestream for @{self.user}\n\n"
                        f"🆔 Room ID: {self.room_id}\n"
                        f"⏱️ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"📂 Output file: {os.path.basename(self.output_file)}"
                    )
                    
                    # Use run_async to safely send notification
                    async def send_notification():
                        await self.context.bot.send_message(
                            chat_id=admin_id,
                            text=notification_message
                        )
                    
                    # Send notification from main thread
                    loop = get_or_create_eventloop()
                    loop.run_until_complete(send_notification())
                except Exception as e:
                    logger.error(f"Error sending recording start notification: {e}")
        
        start_recording_time = datetime.now()
        recording_duration = 0
        
        with open(output, "wb") as out_file:
            stop_recording = False
            while not stop_recording:
                try:
                    if not self.tiktok.is_room_alive(self.room_id):
                        logger.info("User is no longer live. Stopping recording.")
                        break
                        
                    start_time = time.time()
                    for chunk in self.tiktok.download_live_stream(live_url):
                        self.buffer.extend(chunk)
                        if len(self.buffer) >= self.buffer_size:
                            out_file.write(self.buffer)
                            self.buffer.clear()
                            
                        elapsed_time = time.time() - start_time
                        if self.duration and elapsed_time >= self.duration:
                            stop_recording = True
                            break
                            
                except ConnectionError:
                    if self.mode == Mode.AUTOMATIC:
                        logger.error("Connection closed in automatic mode")
                        time.sleep(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)
                        
                except Exception as ex:
                    if "RequestException" in str(type(ex)) or "HTTPException" in str(type(ex)):
                        time.sleep(2)
                    else:
                        logger.error(f"Unexpected error: {ex}")
                        stop_recording = True
                        
                except KeyboardInterrupt:
                    logger.info("Recording stopped by user.")
                    stop_recording = True
                    
                finally:
                    if self.buffer:
                        out_file.write(self.buffer)
                        self.buffer.clear()
                    out_file.flush()
        
        recording_end_time = datetime.now()
        recording_duration = (recording_end_time - start_recording_time).total_seconds()
        
        logger.info(f"Recording finished: {output}")
        
        # Update database to mark recording as finished
        db = init_database()
        if self.user in db["tracked_accounts"]:
            db["tracked_accounts"][self.user]["is_recording"] = False
            db["tracked_accounts"][self.user]["recording_end_time"] = datetime.now().isoformat()
            db["tracked_accounts"][self.user]["last_recording_file"] = output
            
            # Add to finished recordings list
            recording_info = {
                "username": self.user,
                "room_id": self.room_id,
                "file_path": output,
                "size_bytes": os.path.getsize(output),
                "start_time": start_recording_time.isoformat(),
                "end_time": recording_end_time.isoformat(),
                "duration_seconds": recording_duration,
                "date": current_date
            }
            
            db["finished_recordings"].append(recording_info)
            save_database(db)
        
        # Convert FLV to MP4
        output_mp4 = VideoManagement.convert_flv_to_mp4(output)
        
        # Move to finished recordings directory
        final_path = os.path.join(FINISHED_RECORDINGS_DIR, os.path.basename(output_mp4))
        shutil.move(output_mp4, final_path)
        output_mp4 = final_path
        
        # Update the file path in the database
        db = init_database()
        for i, recording in enumerate(db["finished_recordings"]):
            if recording["file_path"] == output:
                db["finished_recordings"][i]["file_path"] = output_mp4
                break
        save_database(db)
        
        # Send to Telegram if enabled
        if self.use_telegram and self.context:
            # Start a new thread for sending to Telegram
            telegram_thread = threading.Thread(
                target=self.send_telegram_video_sync,
                args=(output_mp4, recording_duration)
            )
            telegram_thread.daemon = True
            telegram_thread.start()
            
        return output_mp4
            
    def check_country_blacklisted(self):
        """Check if user's country is blacklisted"""
        is_blacklisted = self.tiktok.is_country_blacklisted()
        if not is_blacklisted:
            return False
            
        if self.room_id is None:
            raise TikTokException(TikTokError.COUNTRY_BLACKLISTED)
            
        if self.mode == Mode.AUTOMATIC:
            raise TikTokException(TikTokError.COUNTRY_BLACKLISTED)
    
    def send_telegram_video_sync(self, file_path, recording_duration):
        """Synchronous wrapper for send_to_telegram to be called in a thread"""
        try:
            # Jalankan fungsi async di telethon_loop yang sudah ada
            run_in_telethon_loop(self.send_to_telegram(file_path, recording_duration))
        except Exception as e:
            logger.error(f"Error in send_telegram_video_sync: {e}")
            # Coba lagi dengan pendekatan alternatif jika gagal
            try:
                async def send_fallback():
                    try:
                        for admin_id in ADMIN_IDS:
                            await self.context.bot.send_message(
                                chat_id=admin_id,
                                text=f"⚠️ Error sending video with Telethon: {e}\n"
                                     f"Video saved at: {os.path.basename(file_path)}"
                            )
                    except Exception as inner_e:
                        logger.error(f"Error sending fallback message: {inner_e}")
                
                # Run fallback in separate asyncio loop (not Telethon loop)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(send_fallback())
                loop.close()
            except Exception as fallback_e:
                logger.error(f"Fallback notification also failed: {fallback_e}")
    
    async def send_to_telegram(self, file_path, recording_duration):
        """Send recorded file to Telegram users using Telethon"""
        global telethon_client
        
        db = init_database()
        
        if self.user not in db["tracked_accounts"]:
            logger.error(f"Account {self.user} not found in tracked_accounts")
            return
            
        account_info = db["tracked_accounts"][self.user]
        
        # Format duration
        duration_str = format_duration(recording_duration)
        
        # Get file size
        file_size_bytes = os.path.getsize(file_path)
        file_size_mb = file_size_bytes / (1024 * 1024)
        
        logger.info(f"File size: {file_size_mb:.2f} MB")
        
        # Compress if too large (2GB limit for Telegram)
        max_size = 2000 * 1024 * 1024  # 2GB in bytes
        compressed_file = None
        
        if file_size_bytes > max_size:
            logger.info(f"File too large for Telegram ({file_size_mb:.2f} MB). Compressing...")
            
            # Use smart compression to target size
            compressed_file = VideoManagement.compress_video(file_path, 1900)  # Target ~1.9GB
            
            # Check compressed size
            compressed_size_bytes = os.path.getsize(compressed_file)
            compressed_size_mb = compressed_size_bytes / (1024 * 1024)
            
            logger.info(f"Compressed file size: {compressed_size_mb:.2f} MB")
            
            send_file = compressed_file
            send_size_mb = compressed_size_mb
        else:
            send_file = file_path
            send_size_mb = file_size_mb
        
        # Get video info for Telethon
        video_info = VideoManagement.get_video_info(send_file)
        width = video_info["width"]
        height = video_info["height"]
        duration = video_info["duration"]
        
        # Cek lagi Telethon client
        if not telethon_client.is_connected():
            await telethon_client.connect()
            
        is_authorized = await telethon_client.is_user_authorized()
        
        # Send to all admin users
        for admin_id in ADMIN_IDS:
            try:
                # Notify admin with bot API first
                status_msg = await self.context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📤 Sending recording of @{self.user}'s livestream ({send_size_mb:.2f} MB)...\n"
                         f"🕒 This may take a few minutes."
                )
                
                if is_authorized:
                    try:
                        # Send with Telethon as a video
                        caption = (
                            f"🎬 Recording of @{self.user}'s livestream\n\n"
                            f"🆔 Room ID: {self.room_id}\n"
                            f"⏱️ Duration: {duration_str}\n"
                            f"📦 Size: {send_size_mb:.2f} MB\n"
                            f"🗓️ Recorded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            f"{' (compressed)' if compressed_file else ''}"
                        )
                        
                        # Use attributes to ensure it's sent as a video
                        attributes = [
                            DocumentAttributeVideo(
                                duration=int(duration),
                                w=width,
                                h=height,
                                supports_streaming=True
                            )
                        ]
                        
                        # Send the video using Telethon
                        await telethon_client.send_file(
                            admin_id,
                            file=send_file,
                            caption=caption,
                            attributes=attributes,
                            supports_streaming=True
                        )
                        
                        # Update status
                        await self.context.bot.edit_message_text(
                            text=f"✅ Recording of @{self.user}'s livestream sent successfully!",
                            chat_id=admin_id,
                            message_id=status_msg.message_id
                        )
                        
                    except Exception as e:
                        logger.error(f"Error sending with Telethon: {e}")
                        
                        # Fallback to bot API jika Telethon gagal
                        await self.context.bot.edit_message_text(
                            text=f"⚠️ Error with Telethon: {str(e)}. Trying alternative method...",
                            chat_id=admin_id,
                            message_id=status_msg.message_id
                        )
                        
                        # Fallback to bot API untuk file kecil
                        if send_size_mb <= 50:
                            with open(send_file, 'rb') as video_file:
                                await self.context.bot.send_video(
                                    chat_id=admin_id,
                                    video=video_file,
                                    caption=caption,
                                    supports_streaming=True
                                )
                        else:
                            await self.context.bot.send_message(
                                chat_id=admin_id,
                                text=f"❌ File too large for Bot API. Video saved at: {os.path.basename(send_file)}"
                            )
                else:
                    # Telethon not authorized, use Bot API
                    await self.context.bot.edit_message_text(
                        text=f"⚠️ Telethon not authorized. Using Bot API instead.",
                        chat_id=admin_id,
                        message_id=status_msg.message_id
                    )
                    
                    if send_size_mb <= 50:
                        with open(send_file, 'rb') as video_file:
                            await self.context.bot.send_video(
                                chat_id=admin_id,
                                video=video_file,
                                caption=caption,
                                supports_streaming=True
                            )
                    else:
                        await self.context.bot.send_message(
                            chat_id=admin_id,
                            text=f"❌ File too large for Bot API. Video saved at: {os.path.basename(send_file)}"
                        )
                        
            except Exception as e:
                logger.error(f"Error sending to admin {admin_id}: {e}")
                
                try:
                    await self.context.bot.send_message(
                        chat_id=admin_id,
                        text=f"❌ Error sending the recording: {str(e)}"
                    )
                except:
                    pass
                    
        # Clean up compressed file
        if compressed_file and os.path.exists(compressed_file):
            try:
                os.remove(compressed_file)
            except Exception as e:
                logger.error(f"Error removing compressed file: {e}")

    async def _send_via_bot_api(self, file_path, file_size_mb, admin_id=None):
        """Fallback method to send using Bot API"""
        if admin_id is None:
            # If no specific admin, send to all admins
            admin_ids = ADMIN_IDS
        else:
            admin_ids = [admin_id]
                
        # Bot API limit is 50MB
        if file_size_mb > 50:
            for admin_id in admin_ids:
                await self.context.bot.send_message(
                    chat_id=admin_id,
                    text=f"⚠️ File is too large for direct sending ({file_size_mb:.2f} MB). "
                         f"The recording is saved locally at: {os.path.basename(file_path)}"
                )
            return
                
        # Send as document with Bot API if larger than 10MB (more reliable than video for large files)
        if file_size_mb > 10:
            for admin_id in admin_ids:
                try:
                    # Send as document instead of video for larger files
                    with open(file_path, 'rb') as video_file:
                        await self.context.bot.send_document(
                            chat_id=admin_id,
                            document=video_file,
                            caption=(
                                f"🎬 Recording of @{self.user}'s livestream\n\n"
                                f"Room ID: {self.room_id}\n"
                                f"Size: {file_size_mb:.2f} MB\n"
                                f"Recorded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                        )
                except Exception as e:
                    logger.error(f"Error sending via Bot API as document: {e}")
                    await self.context.bot.send_message(
                        chat_id=admin_id,
                        text=f"❌ Could not send file. Recording is saved at: {os.path.basename(file_path)}"
                    )
        else:
            # Send as video for smaller files
            for admin_id in admin_ids:
                try:
                    with open(file_path, 'rb') as video_file:
                        await self.context.bot.send_video(
                            chat_id=admin_id,
                            video=video_file,
                            caption=(
                                f"🎬 Recording of @{self.user}'s livestream\n\n"
                                f"Room ID: {self.room_id}\n"
                                f"Size: {file_size_mb:.2f} MB\n"
                                f"Recorded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            ),
                            supports_streaming=True
                        )
                except Exception as e:
                    logger.error(f"Error sending via Bot API as video: {e}")
                    try:
                        # Try as document if video fails
                        with open(file_path, 'rb') as video_file:
                            await self.context.bot.send_document(
                                chat_id=admin_id,
                                document=video_file,
                                caption=(
                                    f"🎬 Recording of @{self.user}'s livestream\n\n"
                                    f"Room ID: {self.room_id}\n"
                                    f"Size: {file_size_mb:.2f} MB\n"
                                    f"Recorded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                                )
                            )
                    except Exception as e2:
                        logger.error(f"Error sending via Bot API as document: {e2}")
                        await self.context.bot.send_message(
                            chat_id=admin_id,
                            text=f"❌ Could not send file. Recording is saved at: {os.path.basename(file_path)}"
                        )

# Bot Helper Functions
def ensure_user(db, user_id):
    """Ensure the user exists in the database"""
    user_id_str = str(user_id)
    if user_id_str not in db["users"]:
        db["users"][user_id_str] = {
            "tracked_accounts": [],
            "settings": {
                "notify_on_live": True,
                "record_quality": "best",
                "auto_compress": True,
                "check_interval": 5  # Default check interval in minutes
            }
        }
        save_database(db)
    return db["users"][user_id_str]

@admin_only
async def add_account(update: Update, context: CallbackContext):
    """Add a TikTok account to track"""
    if not context.args:
        await update.message.reply_text(
            "📝 Please enter the TikTok username or URL you want to track:\n\n"
            "Example: /add username or /add https://www.tiktok.com/@username/live"
        )
        return

    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Get username or URL
    input_value = context.args[0]
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    status_msg = await update.message.reply_text("🔍 Checking TikTok account...")
    
    try:
        username = ""
        room_id = ""
        
        # Check if input is URL or username
        if input_value.startswith("http"):
            # Input is URL
            username, room_id = api.get_room_and_user_from_url(input_value)
        else:
            # Input is username (remove @ if present)
            username = input_value.replace("@", "").strip()
            room_id = api.get_room_id_from_user(username)
        
        if not username:
            await status_msg.edit_text("❌ Could not find username from the provided input.")
            return
            
        # Check if already tracking
        if username in user["tracked_accounts"]:
            await status_msg.edit_text(f"⚠️ You are already tracking @{username}.")
            return
        
        # Add the account to user's tracked accounts
        user["tracked_accounts"].append(username)
        
        # Add the account to global tracked accounts
        if username not in db["tracked_accounts"]:
            db["tracked_accounts"][username] = {
                "is_live": False,
                "is_recording": False,
                "room_id": room_id,
                "last_checked": None,
                "last_live": None,
                "tracked_by": [user_id]
            }
        elif user_id not in db["tracked_accounts"][username]["tracked_by"]:
            db["tracked_accounts"][username]["tracked_by"].append(user_id)
            db["tracked_accounts"][username]["room_id"] = room_id
        
        save_database(db)
        
        # Check if the account is currently live
        is_live = False
        if room_id:
            is_live = api.is_room_alive(room_id)
            
        if is_live:
            db["tracked_accounts"][username]["is_live"] = True
            db["tracked_accounts"][username]["last_live"] = datetime.now().isoformat()
            save_database(db)
            
            await status_msg.edit_text(
                f"✅ Successfully added @{username} to your tracked accounts.\n\n"
                f"🔴 @{username} is currently LIVE! Recording has started automatically."
            )
            
            # Start the recording
            await start_recording_for_account(username, context)
        else:
            await status_msg.edit_text(
                f"✅ Successfully added @{username} to your tracked accounts.\n\n"
                f"The bot will automatically check if @{username} goes live and notify you."
            )
    except Exception as e:
        logger.error(f"Error adding account {input_value}: {e}")
        await status_msg.edit_text(f"❌ Error adding account: {str(e)}")

@admin_only
async def remove_account(update: Update, context: CallbackContext):
    """Remove a TikTok account from tracking"""
    if not context.args:
        await update.message.reply_text(
            "📝 Please enter the TikTok username you want to remove:\n\n"
            "Example: /remove username"
        )
        return
        
    username = context.args[0].replace("@", "").strip()
    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Check if tracking this account
    if username not in user["tracked_accounts"]:
        await update.message.reply_text(f"⚠️ You are not tracking @{username}.")
        return
    
    # Remove the account from user's tracked accounts
    user["tracked_accounts"].remove(username)
    
    # Update the global tracked accounts
    if username in db["tracked_accounts"]:
        if user_id in db["tracked_accounts"][username]["tracked_by"]:
            db["tracked_accounts"][username]["tracked_by"].remove(user_id)
        
        # If no one is tracking this account anymore, remove it
        if not db["tracked_accounts"][username]["tracked_by"]:
            del db["tracked_accounts"][username]
    
    save_database(db)
    
    await update.message.reply_text(f"✅ Successfully removed @{username} from your tracked accounts.")

@admin_only
async def list_accounts(update: Update, context: CallbackContext):
    """List tracked TikTok accounts"""
    user_id = str(update.effective_user.id if update.effective_user else update.callback_query.from_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    if not user["tracked_accounts"]:
        message = "📋 You are not tracking any TikTok accounts yet.\n\n" \
                 "Use /add username to start tracking an account."
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text(message)
        else:
            await update.callback_query.edit_message_text(message)
        return
    
    # Create a list of tracked accounts with their status
    account_list = "📋 *Your Tracked Accounts*:\n\n"
    
    for username in user["tracked_accounts"]:
        account_info = db["tracked_accounts"].get(username, {})
        is_live = "🔴 LIVE" if account_info.get("is_live", False) else "⚫ Offline"
        is_recording = "📹 Recording..." if account_info.get("is_recording", False) else ""
        room_id = account_info.get("room_id", "N/A")
        
        account_list += f"@{username} - {is_live} {is_recording}\n"
        account_list += f"  └ Room ID: {room_id}\n"
    
    account_list += f"\nTotal: {len(user['tracked_accounts'])} accounts"
    
    # Create inline keyboard for management
    keyboard = []
    # Add buttons for each account
    for username in user["tracked_accounts"]:
        keyboard.append([
            InlineKeyboardButton(f"🔍 Check @{username}", callback_data=f"check_{username}"),
            InlineKeyboardButton(f"🗑️ Remove @{username}", callback_data=f"remove_{username}")
        ])
    
    # Add management buttons
    keyboard.append([
        InlineKeyboardButton("🔄 Check All", callback_data="check_all"),
        InlineKeyboardButton("➕ Add Account", callback_data="add_account")
    ])
    
    # Add additional feature buttons
    keyboard.append([
        InlineKeyboardButton("📹 Active Recordings", callback_data="active_recordings"),
        InlineKeyboardButton("📋 Recordings List", callback_data="recordings_list")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(account_list, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(account_list, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def check_account(update: Update, context: CallbackContext, username: str):
    """Check if a TikTok account is currently live"""
    # Directly access user ID without depending on admin_only decorator
    user_id = str(update.effective_user.id if update.effective_user else update.callback_query.from_user.id)
    
    # Check if user is admin
    if not is_admin(int(user_id)):
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text("⛔ This bot is for admin use only.")
        else:
            await update.callback_query.message.reply_text("⛔ This bot is for admin use only.")
        return
    
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Remove @ if present
    username = username.replace("@", "").strip()
    
    # Check if tracking this account
    if username not in user["tracked_accounts"]:
        if hasattr(update, 'message') and update.message:
            message = await update.message.reply_text(f"⚠️ You are not tracking @{username}.")
        else:
            message = await update.callback_query.message.reply_text(f"⚠️ You are not tracking @{username}.")
        return message
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    # Check if the account is live
    if hasattr(update, 'message') and update.message:
        status_msg = await update.message.reply_text(f"🔍 Checking if @{username} is live...")
    else:
        status_msg = await update.callback_query.message.reply_text(f"🔍 Checking if @{username} is live...")
    
    account_info = db["tracked_accounts"][username]
    room_id = account_info.get("room_id", "") or api.get_room_id_from_user(username)
    
    if not room_id:
        await status_msg.edit_text(f"❌ Could not find room ID for @{username}.")
        return status_msg
    
    is_live = api.is_room_alive(room_id)
    
    # Update the database
    account_info["room_id"] = room_id
    account_info["is_live"] = is_live
    account_info["last_checked"] = datetime.now().isoformat()
    
    if is_live:
        account_info["last_live"] = datetime.now().isoformat()
    
    save_database(db)
    
    if is_live:
        # Check if already recording
        if account_info.get("is_recording", False):
            await status_msg.edit_text(
                f"🔴 @{username} is currently LIVE!\n"
                f"📹 Already recording this livestream.\n"
                f"🆔 Room ID: {room_id}"
            )
        else:
            await status_msg.edit_text(
                f"🔴 @{username} is currently LIVE!\n"
                f"📹 Starting to record this livestream...\n"
                f"🆔 Room ID: {room_id}"
            )
            
            # Start the recording
            await start_recording_for_account(username, context)
    else:
        await status_msg.edit_text(
            f"⚫ @{username} is not currently live.\n"
            f"🆔 Room ID: {room_id}\n"
            f"⏱️ Last checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    
    return status_msg

@admin_only
async def check_all_accounts(update: Update, context: CallbackContext):
    """Check all tracked accounts if they are live"""
    user_id = str(update.effective_user.id if update.effective_user else update.callback_query.from_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    if not user["tracked_accounts"]:
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text("⚠️ You are not tracking any TikTok accounts yet.")
        else:
            await update.callback_query.edit_message_text("⚠️ You are not tracking any TikTok accounts yet.")
        return
    
    # Send or edit status message
    if hasattr(update, 'message') and update.message:
        status_msg = await update.message.reply_text("🔍 Checking if any of your tracked accounts are live...")
    else:
        status_msg = await update.callback_query.edit_message_text("🔍 Checking if any of your tracked accounts are live...")
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    live_count = 0
    results = ""
    
    for username in user["tracked_accounts"]:
        account_info = db["tracked_accounts"][username]
        
        # Get room ID
        room_id = account_info.get("room_id", "") or api.get_room_id_from_user(username)
        
        if not room_id:
            results += f"❌ Could not find room ID for @{username}.\n"
            continue
        
        # Check if live
        is_live = api.is_room_alive(room_id)
        
        # Update the database
        account_info["room_id"] = room_id
        account_info["is_live"] = is_live
        account_info["last_checked"] = datetime.now().isoformat()
        
        if is_live:
            live_count += 1
            account_info["last_live"] = datetime.now().isoformat()
            # Hindari pake backtick di message biar gak error markdown parsing
            results += f"🔴 @{username} is LIVE! (Room ID: {room_id})\n"
            
            # Check if already recording
            if account_info.get("is_recording", False):
                results += f"   📹 Already recording this livestream.\n"
            else:
                results += f"   📹 Starting to record this livestream...\n"
                
                # Start the recording
                await start_recording_for_account(username, context)
        else:
            # Hindari pake backtick di message
            results += f"⚫ @{username} is offline. (Room ID: {room_id})\n"
    
    save_database(db)
    
    # Update the status message
    live_status = f"🎉 {live_count} account(s) are currently live!" if live_count > 0 else "😴 None of your tracked accounts are currently live."

    # Add refresh button
    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="check_all")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Update status message - JANGAN pake ParseMode.MARKDOWN di sini
    await status_msg.edit_text(
        f"📊 Status of your tracked accounts:\n\n{results}\n{live_status}",
        reply_markup=reply_markup
    )

async def show_active_recordings(update: Update, context: CallbackContext):
    """Show all active recordings"""
    # Directly access user ID without depending on admin_only decorator
    user_id = str(update.effective_user.id if update.effective_user else update.callback_query.from_user.id)
    
    # Check if user is admin
    if not is_admin(int(user_id)):
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text("⛔ This bot is for admin use only.")
        else:
            await update.callback_query.message.reply_text("⛔ This bot is for admin use only.")
        return
    
    db = init_database()
    
    active_recordings = []
    for username, account_info in db["tracked_accounts"].items():
        if account_info.get("is_recording", False):
            active_recordings.append({
                "username": username,
                "room_id": account_info.get("room_id", "N/A"),
                "start_time": account_info.get("recording_start_time", "N/A"),
                "file": account_info.get("current_recording_file", "N/A")
            })
    
    message = "📹 No active recordings at the moment."
    
    if active_recordings:
        # Create message with active recordings
        message = "📹 *Active Recordings*:\n\n"
        
        for i, recording in enumerate(active_recordings, 1):
            start_time = recording["start_time"]
            if start_time != "N/A":
                try:
                    start_datetime = datetime.fromisoformat(start_time)
                    elapsed = datetime.now() - start_datetime
                    hours, remainder = divmod(elapsed.total_seconds(), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    elapsed_str = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
                except:
                    elapsed_str = "N/A"
            else:
                elapsed_str = "N/A"
            
            message += f"{i}. @{recording['username']}\n"
            message += f"   🆔 Room ID: {recording['room_id']}\n"
            message += f"   ⏱️ Recording for: {elapsed_str}\n"
            message += f"   📂 File: {os.path.basename(recording['file'])}\n\n"
    
    # Create inline keyboard
    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="active_recordings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def show_recordings_list(update: Update, context: CallbackContext, page: int = 0):
    """Show list of completed recordings with pagination"""
    # Directly access user ID without depending on admin_only decorator
    user_id = str(update.effective_user.id if update.effective_user else update.callback_query.from_user.id)
    
    # Check if user is admin
    if not is_admin(int(user_id)):
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text("⛔ This bot is for admin use only.")
        else:
            await update.callback_query.message.reply_text("⛔ This bot is for admin use only.")
        return
    
    db = init_database()
    
    finished_recordings = db.get("finished_recordings", [])
    
    message = "📋 No completed recordings yet."
    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="recordings_list")]]
    
    if finished_recordings:
        # Sort by end time (newest first)
        finished_recordings.sort(key=lambda x: x.get("end_time", ""), reverse=True)
        
        # Calculate total pages
        recordings_per_page = 10
        total_recordings = len(finished_recordings)
        total_pages = (total_recordings + recordings_per_page - 1) // recordings_per_page
        
        # Ensure page is within valid range
        if page < 0:
            page = 0
        elif page >= total_pages:
            page = total_pages - 1
        
        # Get recordings for the current page
        start_idx = page * recordings_per_page
        end_idx = min(start_idx + recordings_per_page, total_recordings)
        current_page_recordings = finished_recordings[start_idx:end_idx]
        
        # Create message with finished recordings
        message = f"📋 *Recent Recordings* (Page {page+1}/{total_pages}):\n\n"
        
        for i, recording in enumerate(current_page_recordings, start_idx + 1):
            username = recording.get("username", "Unknown")
            room_id = recording.get("room_id", "N/A")
            duration = recording.get("duration_seconds", 0)
            file_path = recording.get("file_path", "")
            file_name = os.path.basename(file_path)
            size_bytes = recording.get("size_bytes", 0)
            size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
            
            # Format duration
            duration_str = format_duration(duration)
            
            # Format date
            end_time = recording.get("end_time", "")
            date_str = "N/A"
            if end_time:
                try:
                    end_datetime = datetime.fromisoformat(end_time)
                    date_str = end_datetime.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass
            
            message += f"{i}. @{username}\n"
            message += f"   🆔 Room ID: {room_id}\n"
            message += f"   ⏱️ Duration: {duration_str}\n"
            message += f"   📂 File: {file_name}\n"
            message += f"   📦 Size: {size_mb:.2f} MB\n"
            message += f"   🗓️ Recorded: {date_str}\n\n"
        
        # Create inline keyboard with buttons for each recording
        keyboard = []
        for i, recording in enumerate(current_page_recordings, start_idx + 1):
            username = recording.get("username", "Unknown")
            keyboard.append([
                InlineKeyboardButton(f"Send #{i}: @{username}", callback_data=f"send_recording_{i-1}")
            ])
        
        # Add pagination buttons if needed
        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(
                InlineKeyboardButton("« Previous", callback_data=f"recordings_page_{page-1}")
            )
        
        pagination_buttons.append(
            InlineKeyboardButton("🔄 Refresh", callback_data=f"recordings_page_{page}")
        )
        
        if page < total_pages - 1:
            pagination_buttons.append(
                InlineKeyboardButton("Next »", callback_data=f"recordings_page_{page+1}")
            )
        
        keyboard.append(pagination_buttons)
        keyboard.append([InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def send_recording(update: Update, context: CallbackContext, index: int):
    """Send a specific recording to the user via Telethon"""
    global telethon_client
    
    # Directly access user ID without depending on admin_only decorator
    query = update.callback_query
    user_id = query.from_user.id
    
    # Check if user is admin
    if not is_admin(user_id):
        await query.answer("⛔ This bot is for admin use only.")
        return
    
    db = init_database()
    finished_recordings = db.get("finished_recordings", [])
    
    # Sort by end time (newest first)
    finished_recordings.sort(key=lambda x: x.get("end_time", ""), reverse=True)
    
    # Limit to 10 most recent recordings
    recent_recordings = finished_recordings[:10]
    
    if index >= len(recent_recordings):
        await query.answer("Recording not found")
        return
    
    recording = recent_recordings[index]
    file_path = recording.get("file_path", "")
    
    if not file_path or not os.path.exists(file_path):
        await query.answer("File not found")
        await query.message.reply_text(f"❌ File not found: {file_path}")
        return
    
    await query.answer("Preparing to send recording...")
    
    # Send a status message
    status_msg = await query.message.reply_text("📤 Preparing to send recording as video. This may take a moment...")
    
    # Get recording details
    username = recording.get("username", "Unknown")
    room_id = recording.get("room_id", "N/A")
    duration = recording.get("duration_seconds", 0)
    size_bytes = recording.get("size_bytes", 0)
    size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
    
    # Format duration
    duration_str = format_duration(duration)
    
    # Format date
    end_time = recording.get("end_time", "")
    date_str = "N/A"
    if end_time:
        try:
            end_datetime = datetime.fromisoformat(end_time)
            date_str = end_datetime.strftime("%Y-%m-%d %H:%M:%S")
        except:
            pass
    
    try:
        # Check if file is too large (2GB Telegram limit)
        max_size = 2000 * 1024 * 1024
        
        if size_bytes > max_size:
            # Try to compress
            await status_msg.edit_text(f"📦 File is too large ({size_mb:.2f} MB). Compressing to under 2GB...")
            
            # Compress the video
            compressed_file = VideoManagement.compress_video(file_path, 1900)  # Target ~1.9GB to be safe
            
            # Get the new file size
            compressed_size = os.path.getsize(compressed_file)
            compressed_size_mb = compressed_size / (1024 * 1024)
            
            # Use the compressed file
            send_file = compressed_file
            send_size_mb = compressed_size_mb
            
            await status_msg.edit_text(f"📤 Sending compressed recording ({compressed_size_mb:.2f} MB)...")
        else:
            # Use original file
            send_file = file_path
            send_size_mb = size_mb
            
            await status_msg.edit_text(f"📤 Sending recording ({size_mb:.2f} MB)...")
        
        # Get video info for Telethon
        video_info = VideoManagement.get_video_info(send_file)
        width = video_info["width"]
        height = video_info["height"]
        video_duration = video_info["duration"]
        
        # Prepare caption
        caption = (
            f"🎬 Recording of @{username}'s livestream\n\n"
            f"🆔 Room ID: {room_id}\n"
            f"⏱️ Duration: {duration_str}\n"
            f"📦 Size: {send_size_mb:.2f} MB\n"
            f"🗓️ Recorded on: {date_str}"
            f"{' (compressed)' if size_bytes > max_size else ''}"
        )
        
        # Cek koneksi Telethon client
        if not telethon_client.is_connected():
            await telethon_client.connect()
            
        # Check if authorized
        if not await telethon_client.is_user_authorized():
            # Fall back to Bot API
            await status_msg.edit_text("⚠️ Telethon client not authorized. Using alternative method...")
            
            # Only try if under 50MB (Bot API limit)
            if send_size_mb <= 50:
                with open(send_file, "rb") as video_file:
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=video_file,
                        caption=caption,
                        supports_streaming=True
                    )
                    await status_msg.edit_text("✅ Recording sent successfully!")
            else:
                await status_msg.edit_text(
                    f"❌ File is too large for Bot API ({send_size_mb:.2f} MB) and Telethon is not authorized.\n"
                    f"File is available at: {os.path.basename(send_file)}"
                )
        else:
            # Set up video attributes
            attributes = [
                DocumentAttributeVideo(
                    duration=int(video_duration),
                    w=width,
                    h=height,
                    supports_streaming=True
                )
            ]
            
            # Define function to send via Telethon in its proper loop
            async def send_via_telethon():
                await telethon_client.send_file(
                    user_id,
                    file=send_file,
                    caption=caption,
                    attributes=attributes,
                    supports_streaming=True
                )
            
            # Run in Telethon loop
            await run_in_telethon_loop(send_via_telethon())
            
            # Update status message
            await status_msg.edit_text("✅ Recording sent successfully as video!")
            
        # Clean up temporary file if we compressed
        if size_bytes > max_size and send_file != file_path and os.path.exists(send_file):
            os.remove(send_file)
            
    except Exception as e:
        logger.error(f"Error sending recording: {e}")
        
        # Try fallback to Bot API if possible
        try:
            if send_size_mb <= 50:
                await status_msg.edit_text(f"⚠️ Error sending video. Trying alternative method...")
                
                with open(send_file, "rb") as video_file:
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=video_file,
                        caption=caption,
                        supports_streaming=True
                    )
                    await status_msg.edit_text("✅ Recording sent successfully using alternative method!")
            else:
                await status_msg.edit_text(
                    f"❌ Error sending the recording: {str(e)}\n"
                    f"The file is available at: {os.path.basename(send_file)}"
                )
        except Exception as inner_e:
            logger.error(f"Error in fallback sending: {inner_e}")
            await status_msg.edit_text(
                f"❌ Error sending the recording: {str(e)}\n"
                f"The file is available at: {os.path.basename(send_file)}"
            )

async def start_recording_for_account(username: str, context: CallbackContext):
    """Start recording for a specific account"""
    db = init_database()
    
    if username not in db["tracked_accounts"]:
        logger.error(f"Account {username} not found in tracked_accounts")
        return
    
    account_info = db["tracked_accounts"][username]
    
    # Skip if already recording
    if account_info.get("is_recording", False):
        logger.info(f"Already recording {username}")
        return
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    # Get room ID if not available
    room_id = account_info.get("room_id", "") or api.get_room_id_from_user(username)
    
    if not room_id:
        logger.error(f"Could not find room ID for {username}")
        
        # Notify admin users
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"❌ Could not find room ID for @{username}."
            )
        return
    
    # Check if the account is live
    is_live = api.is_room_alive(room_id)
    
    if not is_live:
        logger.info(f"{username} is not live")
        account_info["is_live"] = False
        save_database(db)
        return
    
    # Mark as recording
    account_info["is_recording"] = True
    account_info["is_live"] = True
    account_info["recording_start_time"] = datetime.now().isoformat()
    save_database(db)
    
    # Notify admin users
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🔴 @{username} is now LIVE!\n"
                f"🆔 Room ID: {room_id}\n"
                f"📹 Recording started automatically."
            )
        )
    
    # Start recording in a separate thread
    thread = threading.Thread(
        target=run_recording_thread,
        args=(username, room_id, context)
    )
    thread.daemon = True
    thread.start()

def run_recording_thread(username, room_id, context):
    """Run recording in a separate thread"""
    try:
        # Initialize for recording
        db = init_database()
        account_info = db["tracked_accounts"][username]
        cookies = init_cookies()
        
        # Create recorder
        recorder = TikTokRecorder(
            url=None,
            user=username,
            room_id=room_id,
            mode=Mode.MANUAL,
            automatic_interval=5,
            cookies=cookies,
            proxy=None,
            output="recordings/",
            duration=None,
            use_telegram=True,
            context=context
        )
        
        # Start recording
        try:
            output_file = recorder.start_recording()
            logger.info(f"Recording completed: {output_file}")
        except Exception as e:
            logger.error(f"Error recording {username}: {e}")
            
            # Update database
            db = init_database()
            if username in db["tracked_accounts"]:
                account_info = db["tracked_accounts"][username]
                account_info["is_recording"] = False
                account_info["last_error"] = str(e)
                save_database(db)
                
                # Send error notifications
                for admin_id in ADMIN_IDS:
                    try:
                        # Create async notification in the thread's event loop
                        async def send_error_notification():
                            await context.bot.send_message(
                                chat_id=admin_id,
                                text=f"❌ Error recording @{username}'s livestream: {str(e)}"
                            )
                        
                        # Get or create event loop for this thread
                        loop = get_or_create_eventloop()
                        loop.run_until_complete(send_error_notification())
                    except Exception as notify_err:
                        logger.error(f"Error sending notification: {notify_err}")
    
    except Exception as e:
        logger.error(f"Thread error for {username}: {e}")

async def check_tracked_accounts_job(context: CallbackContext):
    """Check all tracked accounts to see if they're live (periodic job)"""
    db = init_database()
    
    if not db["tracked_accounts"]:
        logger.info("No accounts are being tracked.")
        return
    
    logger.info(f"Checking {len(db['tracked_accounts'])} tracked accounts...")
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    for username, account_info in list(db["tracked_accounts"].items()):
        try:
            logger.info(f"Checking if {username} is live...")
            
            # Get room ID
            room_id = account_info.get("room_id", "") or api.get_room_id_from_user(username)
            
            if not room_id:
                logger.error(f"Could not find room ID for {username}")
                continue
            
            # Update room ID in database
            account_info["room_id"] = room_id
            account_info["last_checked"] = datetime.now().isoformat()
            save_database(db)
            
            # Check if live
            is_live = api.is_room_alive(room_id)
            was_live = account_info.get("is_live", False)
            was_recording = account_info.get("is_recording", False)
            
            # Update live status
            account_info["is_live"] = is_live
            
            if is_live:
                account_info["last_live"] = datetime.now().isoformat()
                logger.info(f"{username} is LIVE!")
                
                # If they're live now but not recording
                if not was_recording:
                    # Check if this is a new stream (was not live before or the previous stream ended)
                    # We consider it a new stream if:
                    # 1. They weren't live before (was_live == False)
                    # 2. OR They were live before but the recording stopped for some reason
                    if not was_live or (was_live and not was_recording):
                        logger.info(f"Starting new recording for {username}")
                        await start_recording_for_account(username, context)
            else:
                # If they're not live now but marked as recording, update status
                if was_recording:
                    logger.info(f"{username} is no longer live but still marked as recording. Fixing status.")
                    account_info["is_recording"] = False
                    save_database(db)
                
                logger.info(f"{username} is not live.")
            
            save_database(db)
            
        except Exception as e:
            logger.error(f"Error checking {username}: {e}")
            
            # Try to fix recording status if there was an error
            if "is_recording" in account_info and account_info["is_recording"]:
                # Check if the recording process is actually running
                if not is_recording_active(username):
                    logger.info(f"Recording status mismatch for {username}. Fixing.")
                    account_info["is_recording"] = False
                    save_database(db)

# Telegram Bot Command Handlers
@admin_only
async def start(update: Update, context: CallbackContext):
    """Handle the /start command"""
    user_id = update.effective_user.id
    db = init_database()
    ensure_user(db, user_id)
    
    keyboard = [
        [
            InlineKeyboardButton("📌 Add Account", callback_data="add_account"),
            InlineKeyboardButton("📋 List Accounts", callback_data="list_accounts")
        ],
        [
            InlineKeyboardButton("🔄 Check All", callback_data="check_all"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings")
        ],
        [
            InlineKeyboardButton("📹 Active Recordings", callback_data="active_recordings"),
            InlineKeyboardButton("📋 Recordings List", callback_data="recordings_list")
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Welcome to the TikTok Livestream Tracker & Recorder Bot!\n\n"
        f"This bot allows you to track TikTok accounts and automatically records their livestreams.\n\n"
        f"📌 Use /add username to track a TikTok account\n"
        f"📋 Use /list to see your tracked accounts\n"
        f"🗑️ Use /remove username to stop tracking an account\n"
        f"⚙️ Use /settings to configure bot settings\n"
        f"❓ Use /help to see all available commands",
        reply_markup=reply_markup
    )

@admin_only
async def help_command(update: Update, context: CallbackContext):
    """Handle the /help command"""
    message = "📚 *Available Commands:*\n\n" \
             "📌 /add username - Track a TikTok account\n" \
             "📋 /list - Show your tracked accounts\n" \
             "🗑️ /remove username - Stop tracking an account\n" \
             "⚙️ /settings - Configure bot settings\n" \
             "🔄 /check - Manually check if tracked accounts are live\n" \
             "📹 /active - Show active recordings\n" \
             "📋 /recordings - Show completed recordings list\n" \
             "❓ /help - Show this help message\n\n" \
             "The bot automatically checks for livestreams based on your settings interval. " \
             "When a tracked account goes live, it will notify you and start recording. " \
             "When the livestream ends, it will send you the recording as a video."
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

@admin_only
async def settings_command(update: Update, context: CallbackContext):
    """Handle the /settings command"""
    user_id = str(update.effective_user.id if update.effective_user else update.callback_query.from_user.id)
    
    db = init_database()
    user = ensure_user(db, user_id)
    
    settings = user["settings"]
    
    message = f"⚙️ *Settings*\n\n" \
             f"🔔 Notify when account goes live: {'✅ ON' if settings.get('notify_on_live', True) else '❌ OFF'}\n" \
             f"🎥 Recording quality: {settings.get('record_quality', 'best')}\n" \
             f"🗜️ Auto-compress large files: {'✅ ON' if settings.get('auto_compress', True) else '❌ OFF'}\n" \
             f"⏱️ Check interval: {settings.get('check_interval', 5)} minutes\n\n" \
             f"Select a setting to change:"
    
    keyboard = [
        [InlineKeyboardButton(
            f"🔔 Notifications: {'ON' if settings.get('notify_on_live', True) else 'OFF'}", 
            callback_data="toggle_notifications"
        )],
        [InlineKeyboardButton(
            f"🗜️ Auto-compress: {'ON' if settings.get('auto_compress', True) else 'OFF'}", 
            callback_data="toggle_autocompress"
        )],
        [InlineKeyboardButton(
            f"🎥 Quality: {settings.get('record_quality', 'best')}", 
            callback_data="cycle_quality"
        )],
        [InlineKeyboardButton(
            f"⏱️ Check Interval: {settings.get('check_interval', 5)} min", 
            callback_data="cycle_interval"
        )],
        [InlineKeyboardButton(
            "🔙 Back to Main Menu", 
            callback_data="back_to_main"
        )]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

@admin_only
async def active_command(update: Update, context: CallbackContext):
    """Handle the /active command"""
    await show_active_recordings(update, context)

@admin_only
async def recordings_command(update: Update, context: CallbackContext):
    """Handle the /recordings command"""
    await show_recordings_list(update, context)

async def callback_handler(update: Update, context: CallbackContext):
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Check if user is admin
    if not is_admin(user_id):
        await query.answer("⛔ This bot is for admin use only.")
        return
    
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Initialize with empty acknowledgment
    await query.answer()
    
    # Handle different callback queries
    if query.data == "add_account":
        await query.message.reply_text(
            "📝 Please enter the TikTok username or URL you want to track:\n\n"
            "Example: /add username or /add https://www.tiktok.com/@username/live"
        )
    
    elif query.data == "list_accounts":
        await list_accounts(update, context)
    
    elif query.data == "check_all":
        await check_all_accounts(update, context)
    
    elif query.data == "settings":
        await settings_command(update, context)
    
    elif query.data == "help":
        await help_command(update, context)
    
    elif query.data == "active_recordings":
        await show_active_recordings(update, context)
    
    elif query.data == "recordings_list":
        await show_recordings_list(update, context, 0)  # Start at first page
    
    # New pagination handler for recordings list
    elif query.data.startswith("recordings_page_"):
        page = int(query.data.replace("recordings_page_", ""))
        await show_recordings_list(update, context, page)
    
    elif query.data == "back_to_main":
        # Go back to main menu
        keyboard = [
            [
                InlineKeyboardButton("📌 Add Account", callback_data="add_account"),
                InlineKeyboardButton("📋 List Accounts", callback_data="list_accounts")
            ],
            [
                InlineKeyboardButton("🔄 Check All", callback_data="check_all"),
                InlineKeyboardButton("⚙️ Settings", callback_data="settings")
            ],
            [
                InlineKeyboardButton("📹 Active Recordings", callback_data="active_recordings"),
                InlineKeyboardButton("📋 Recordings List", callback_data="recordings_list")
            ],
            [
                InlineKeyboardButton("❓ Help", callback_data="help")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"👋 Welcome to the TikTok Livestream Tracker & Recorder Bot!\n\n"
            f"This bot allows you to track TikTok accounts and automatically records their livestreams.\n\n"
            f"📌 Use /add username to track a TikTok account\n"
            f"📋 Use /list to see your tracked accounts\n"
            f"🗑️ Use /remove username to stop tracking an account\n"
            f"⚙️ Use /settings to configure bot settings\n"
            f"❓ Use /help to see all available commands",
            reply_markup=reply_markup
        )
    
    elif query.data.startswith("remove_"):
        username = query.data.replace("remove_", "")
        context.args = [username]
        await remove_account(update, context)
        # Refresh list after removal
        await list_accounts(update, context)
    
    elif query.data.startswith("check_"):
        username = query.data.replace("check_", "")
        # Call directly without admin_only decorator
        await check_account(update, context, username)
    
    elif query.data.startswith("send_recording_"):
        index = int(query.data.replace("send_recording_", ""))
        await send_recording(update, context, index)
    
    elif query.data == "toggle_notifications":
        settings = user["settings"]
        settings["notify_on_live"] = not settings.get("notify_on_live", True)
        save_database(db)
        
        # Update settings message
        await settings_command(update, context)
    
    elif query.data == "toggle_autocompress":
        settings = user["settings"]
        settings["auto_compress"] = not settings.get("auto_compress", True)
        save_database(db)
        
        # Update settings message
        await settings_command(update, context)
    
    elif query.data == "cycle_quality":
        settings = user["settings"]
        
        # Cycle through quality options
        if settings.get("record_quality", "best") == "best":
            settings["record_quality"] = "high"
        elif settings["record_quality"] == "high":
            settings["record_quality"] = "medium"
        else:
            settings["record_quality"] = "best"
        
        save_database(db)
        
        # Update settings message
        await settings_command(update, context)
    
    elif query.data == "cycle_interval":
        settings = user["settings"]
        
        # Cycle through interval options: 1, 3, 5, 10, 15, 30, 60 minutes
        intervals = [1, 3, 5, 10, 15, 30, 60]
        current = settings.get("check_interval", 5)
        
        # Find next interval
        next_index = 0
        for i, interval in enumerate(intervals):
            if interval > current:
                next_index = i
                break
        
        settings["check_interval"] = intervals[next_index % len(intervals)]
        save_database(db)
        
        # Restart the job with new interval
        for job in context.job_queue.get_jobs_by_name("check_accounts"):
            job.schedule_removal()
        
        context.job_queue.run_repeating(
            check_tracked_accounts_job,
            interval=settings["check_interval"] * 60,
            first=10,
            name="check_accounts"
        )
        
        # Update settings message
        await settings_command(update, context)

@admin_only
async def text_handler(update: Update, context: CallbackContext):
    """Handle regular text messages"""
    try:
        text = update.message.text.lower()
        
        if text.startswith("add "):
            username = text.split(" ", 1)[1].strip()
            context.args = [username]
            await add_account(update, context)
        
        elif text.startswith("remove "):
            username = text.split(" ", 1)[1].strip()
            context.args = [username]
            await remove_account(update, context)
        
        elif text.startswith("check "):
            username = text.split(" ", 1)[1].strip()
            await check_account(update, context, username)
        
        elif text == "list":
            await list_accounts(update, context)
        
        elif text == "check":
            await check_all_accounts(update, context)
        
        elif text == "settings":
            await settings_command(update, context)
        
        elif text == "help":
            await help_command(update, context)
        
        elif text == "active":
            await active_command(update, context)
        
        elif text == "recordings":
            await recordings_command(update, context)
    except Exception as e:
        logger.error(f"Error in text handler: {e}")
        await update.message.reply_text(f"❌ Error processing command: {str(e)}")

async def setup_job_queue(application):
    """Set up job queue for checking accounts"""
    job_queue = application.job_queue
    
    # Get check interval from database settings
    db = init_database()
    
    # Set default check interval to 5 minutes
    check_interval = 5
    
    # Check if any admin has a custom interval
    for admin_id in ADMIN_IDS:
        admin_id_str = str(admin_id)
        if admin_id_str in db["users"]:
            settings = db["users"][admin_id_str].get("settings", {})
            custom_interval = settings.get("check_interval", None)
            if custom_interval:
                check_interval = custom_interval
                break
    
    # Set up the job
    job_queue.run_repeating(
        check_tracked_accounts_job,
        interval=check_interval * 60,
        first=10,
        name="check_accounts"
    )

def main():
    """Start the bot"""
    global telethon_runner
    
    try:
        # Initialize Telethon first
        initialize_telethon()
        
        # Start Telethon event loop di background
        telethon_runner = TelethonLoopRunner()
        telethon_runner.start()
        
        # Create the application and pass it your bot's token
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Register command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("add", add_account))
        application.add_handler(CommandHandler("remove", remove_account))
        application.add_handler(CommandHandler("list", list_accounts))
        application.add_handler(CommandHandler("check", check_all_accounts))
        application.add_handler(CommandHandler("settings", settings_command))
        application.add_handler(CommandHandler("active", active_command))
        application.add_handler(CommandHandler("recordings", recordings_command))
        
        # Register callback query handler
        application.add_handler(CallbackQueryHandler(callback_handler))
        
        # Register text message handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        
        # Set up job queue
        application.job_queue.run_once(lambda context: asyncio.create_task(setup_job_queue(application)), 0)
        
        logger.info("Starting bot...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Critical error in main function: {e}")
        # Stop Telethon event loop
        if telethon_runner:
            telethon_runner.stop()
        # Try to restart the bot after a delay
        logger.info("Restarting bot in 10 seconds...")
        time.sleep(10)
        main()

if __name__ == "__main__":
    # Initialize event loop for main thread
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Ensure the main event loop is set
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        # If no event loop exists, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    # Run the bot
    main()
