import os
import json
import time
import logging
import asyncio
import requests
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
import shutil

import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Ensure directories exist
RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(exist_ok=True)

DB_FILE = Path("database.json")
COOKIES_FILE = Path("cookies.json")

# Default cookies if needed
DEFAULT_COOKIES = {
    "sessionid_ss": "",
    "tt-target-idc": "useast2a"
}

# Initialize database
def init_database():
    if not DB_FILE.exists():
        with open(DB_FILE, "w") as f:
            json.dump({
                "users": {},
                "tracked_accounts": {}
            }, f, indent=2)
    
    with open(DB_FILE, "r") as f:
        return json.load(f)

# Save database
def save_database(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

# Initialize cookies
def init_cookies():
    if not COOKIES_FILE.exists():
        with open(COOKIES_FILE, "w") as f:
            json.dump(DEFAULT_COOKIES, f, indent=2)
    
    with open(COOKIES_FILE, "r") as f:
        return json.load(f)

# TikTok API Client
class TikTokAPI:
    def __init__(self, proxy=None, cookies=None):
        self.BASE_URL = 'https://www.tiktok.com'
        self.WEBCAST_URL = 'https://webcast.tiktok.com'
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
            "Accept-Language": "en-US",
            "Referer": "https://www.tiktok.com/"
        })
        
        if cookies:
            self.session.cookies.update(cookies)
            
        if proxy:
            self.session.proxies.update({
                'http': proxy,
                'https': proxy
            })
    
    def get_room_id_from_user(self, username: str) -> str:
        """Get TikTok live room ID from username"""
        try:
            response = self.session.get(f"{self.BASE_URL}/@{username}/live")
            
            if "Please wait..." in response.text:
                logger.error(f"IP blocked by TikTok WAF for {username}")
                return ""
                
            # Extract room ID from the page content
            pattern = re.compile(
                r'<script id="SIGI_STATE" type="application/json">(.*?)</script>',
                re.DOTALL
            )
            match = pattern.search(response.text)
            
            if not match:
                logger.error(f"Couldn't extract room ID for {username}")
                return ""
                
            data = json.loads(match.group(1))
            
            if 'LiveRoom' not in data and 'CurrentRoom' in data:
                return ""
                
            room_id = data.get('LiveRoom', {}).get('liveRoomUserInfo', {}).get(
                'user', {}).get('roomId', None)
                
            if not room_id:
                logger.error(f"Room ID not found for {username}")
                return ""
                
            return room_id
            
        except Exception as e:
            logger.error(f"Error getting room ID for {username}: {e}")
            return ""
    
    def is_room_alive(self, room_id: str) -> bool:
        """Check if the room is currently live"""
        if not room_id:
            return False
            
        try:
            response = self.session.get(
                f"{self.WEBCAST_URL}/webcast/room/check_alive/"
                f"?aid=1988&region=CH&room_ids={room_id}&user_is_login=true"
            )
            
            data = response.json()
            
            if 'data' not in data or len(data['data']) == 0:
                return False
                
            return data['data'][0].get('alive', False)
            
        except Exception as e:
            logger.error(f"Error checking if room {room_id} is live: {e}")
            return False
    
    def get_live_url(self, room_id: str) -> str:
        """Get the live streaming URL"""
        try:
            response = self.session.get(
                f"{self.WEBCAST_URL}/webcast/room/info/?aid=1988&room_id={room_id}"
            )
            
            data = response.json()
            
            if 'This account is private' in str(data):
                logger.error(f"Account with room ID {room_id} is private")
                return ""
                
            stream_url = data.get('data', {}).get('stream_url', {})
            
            # Get the best quality available
            live_url = (
                stream_url.get('flv_pull_url', {}).get('FULL_HD1') or
                stream_url.get('flv_pull_url', {}).get('HD1') or
                stream_url.get('flv_pull_url', {}).get('SD2') or
                stream_url.get('flv_pull_url', {}).get('SD1')
            )
            
            # If flv_pull_url is not available, use rtmp_pull_url
            if not live_url:
                live_url = stream_url.get('rtmp_pull_url', None)
                
            if not live_url:
                logger.error(f"Could not get live URL for room {room_id}")
                return ""
                
            logger.info(f"Got live URL for room {room_id}: {live_url}")
            return live_url
            
        except Exception as e:
            logger.error(f"Error getting live URL for room {room_id}: {e}")
            return ""

# TikTok Recorder
class TikTokRecorder:
    def __init__(self, api, username, room_id, output_path):
        self.api = api
        self.username = username
        self.room_id = room_id
        self.output_path = output_path
        self.recording_process = None
        self.is_recording = False
    
    def start_recording(self):
        """Start recording the live stream"""
        if self.is_recording:
            logger.info(f"Already recording {self.username}")
            return
            
        live_url = self.api.get_live_url(self.room_id)
        if not live_url:
            logger.error(f"Couldn't get live URL for {self.username}")
            return
            
        current_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_file = Path(self.output_path) / f"TK_{self.username}_{current_date}.mp4"
        
        logger.info(f"Starting recording for {self.username} to {output_file}")
        
        # Method 1: Use yt-dlp for recording (more reliable for TikTok)
        try:
            command = [
                "yt-dlp", 
                "-o", str(output_file),
                "--no-part",
                "--concurrent-fragments", "5",
                live_url
            ]
            
            self.recording_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            self.is_recording = True
            logger.info(f"Started recording {self.username} with yt-dlp")
            return output_file
            
        except Exception as e:
            logger.error(f"Error starting yt-dlp recording for {self.username}: {e}")
            
            # Method 2: Fallback to ffmpeg
            try:
                command = [
                    "ffmpeg",
                    "-y",
                    "-re",
                    "-i", live_url,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    str(output_file)
                ]
                
                self.recording_process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                self.is_recording = True
                logger.info(f"Started recording {self.username} with ffmpeg")
                return output_file
                
            except Exception as e:
                logger.error(f"Error starting ffmpeg recording for {self.username}: {e}")
                return None
    
    def stop_recording(self):
        """Stop the recording process"""
        if self.recording_process and self.is_recording:
            self.recording_process.terminate()
            self.is_recording = False
            logger.info(f"Stopped recording {self.username}")
            return True
        return False

# Bot Helper functions
def ensure_user(db, user_id):
    """Ensure the user exists in the database"""
    user_id_str = str(user_id)
    if user_id_str not in db["users"]:
        db["users"][user_id_str] = {
            "tracked_accounts": [],
            "settings": {
                "notify_on_live": True,
                "record_quality": "best",
                "auto_compress": True
            }
        }
        save_database(db)
    return db["users"][user_id_str]

async def add_account(update: Update, context: CallbackContext, username: str):
    """Add a TikTok account to track"""
    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Remove @ if present
    username = username.replace("@", "").strip()
    
    # Check if already tracking
    if username in user["tracked_accounts"]:
        await update.message.reply_text(f"⚠️ You are already tracking @{username}.")
        return
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    # Validate the TikTok account
    await update.message.reply_text(f"🔍 Checking if @{username} exists...")
    
    room_id = api.get_room_id_from_user(username)
    if not room_id:
        await update.message.reply_text(f"❌ Could not find TikTok account @{username} or the account has never been live.")
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
    is_live = api.is_room_alive(room_id)
    if is_live:
        db["tracked_accounts"][username]["is_live"] = True
        db["tracked_accounts"][username]["last_live"] = datetime.now().isoformat()
        save_database(db)
        
        await update.message.reply_text(
            f"✅ Successfully added @{username} to your tracked accounts.\n\n"
            f"🔴 @{username} is currently LIVE! Recording has started automatically."
        )
        
        # Start the recording
        await start_recording_for_account(username, context)
    else:
        await update.message.reply_text(
            f"✅ Successfully added @{username} to your tracked accounts.\n\n"
            f"The bot will automatically check if @{username} goes live and notify you."
        )

async def remove_account(update: Update, context: CallbackContext, username: str):
    """Remove a TikTok account from tracking"""
    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Remove @ if present
    username = username.replace("@", "").strip()
    
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

async def list_accounts(update: Update, context: CallbackContext):
    """List tracked TikTok accounts"""
    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    if not user["tracked_accounts"]:
        await update.message.reply_text(
            "📋 You are not tracking any TikTok accounts yet.\n\n"
            "Use /add username to start tracking an account."
        )
        return
    
    # Create a list of tracked accounts with their status
    account_list = "📋 *Your Tracked Accounts*:\n\n"
    
    for username in user["tracked_accounts"]:
        account_info = db["tracked_accounts"].get(username, {})
        is_live = "🔴 LIVE" if account_info.get("is_live", False) else "⚫ Offline"
        is_recording = "📹 Recording..." if account_info.get("is_recording", False) else ""
        
        account_list += f"@{username} - {is_live} {is_recording}\n"
    
    account_list += f"\nTotal: {len(user['tracked_accounts'])} accounts"
    
    # Create inline keyboard for management
    keyboard = []
    for username in user["tracked_accounts"]:
        keyboard.append([
            InlineKeyboardButton(f"Remove @{username}", callback_data=f"remove_{username}"),
            InlineKeyboardButton(f"Check @{username}", callback_data=f"check_{username}")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(account_list, parse_mode="Markdown", reply_markup=reply_markup)

async def check_account(update: Update, context: CallbackContext, username: str):
    """Check if a TikTok account is currently live"""
    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Remove @ if present
    username = username.replace("@", "").strip()
    
    # Check if tracking this account
    if username not in user["tracked_accounts"]:
        await update.message.reply_text(f"⚠️ You are not tracking @{username}.")
        return
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    # Check if the account is live
    await update.message.reply_text(f"🔍 Checking if @{username} is live...")
    
    account_info = db["tracked_accounts"][username]
    room_id = account_info.get("room_id") or api.get_room_id_from_user(username)
    
    if not room_id:
        await update.message.reply_text(f"❌ Could not find room ID for @{username}.")
        return
    
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
            await update.message.reply_text(
                f"🔴 @{username} is currently LIVE!\n"
                f"📹 Already recording this livestream."
            )
        else:
            await update.message.reply_text(
                f"🔴 @{username} is currently LIVE!\n"
                f"📹 Starting to record this livestream..."
            )
            
            # Start the recording
            await start_recording_for_account(username, context)
    else:
        await update.message.reply_text(f"⚫ @{username} is not currently live.")

async def check_all_accounts(update: Update, context: CallbackContext):
    """Check all tracked accounts if they are live"""
    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    if not user["tracked_accounts"]:
        await update.message.reply_text("⚠️ You are not tracking any TikTok accounts yet.")
        return
    
    status_msg = await update.message.reply_text("🔍 Checking if any of your tracked accounts are live...")
    
    # Initialize API
    cookies = init_cookies()
    api = TikTokAPI(cookies=cookies)
    
    live_count = 0
    results = ""
    
    for username in user["tracked_accounts"]:
        account_info = db["tracked_accounts"][username]
        
        # Get room ID
        room_id = account_info.get("room_id") or api.get_room_id_from_user(username)
        
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
            results += f"🔴 @{username} is LIVE!\n"
            
            # Check if already recording
            if account_info.get("is_recording", False):
                results += f"   📹 Already recording this livestream.\n"
            else:
                results += f"   📹 Starting to record this livestream...\n"
                
                # Start the recording
                await start_recording_for_account(username, context)
        else:
            results += f"⚫ @{username} is offline.\n"
    
    save_database(db)
    
    # Update the status message
    await status_msg.edit_text(
        f"📊 Status of your tracked accounts:\n\n{results}\n"
        f"{live_count > 0 ? f'🎉 {live_count} account(s) are currently live!' : '😴 None of your tracked accounts are currently live.'}"
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
    room_id = account_info.get("room_id") or api.get_room_id_from_user(username)
    
    if not room_id:
        logger.error(f"Could not find room ID for {username}")
        
        # Notify users
        for user_id in account_info["tracked_by"]:
            await context.bot.send_message(
                chat_id=user_id,
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
    
    # Create recorder
    recorder = TikTokRecorder(
        api=api,
        username=username,
        room_id=room_id,
        output_path=RECORDINGS_DIR
    )
    
    # Start recording in a separate thread
    output_file = recorder.start_recording()
    
    if not output_file:
        logger.error(f"Failed to start recording for {username}")
        account_info["is_recording"] = False
        save_database(db)
        
        # Notify users
        for user_id in account_info["tracked_by"]:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Failed to start recording for @{username}."
            )
        return
    
    # Notify users
    for user_id in account_info["tracked_by"]:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🔴 @{username} is now LIVE! Recording started automatically."
        )
    
    # Monitor recording in background
    asyncio.create_task(monitor_recording(username, recorder, output_file, context))

async def monitor_recording(username: str, recorder: TikTokRecorder, output_file: Path, context: CallbackContext):
    """Monitor recording and send when finished"""
    db = init_database()
    account_info = db["tracked_accounts"].get(username)
    
    if not account_info:
        logger.error(f"Account {username} not found in tracked_accounts during monitoring")
        return
    
    try:
        # Initialize API
        cookies = init_cookies()
        api = TikTokAPI(cookies=cookies)
        
        # Monitor until the process ends or the user stops streaming
        while recorder.is_recording:
            # Check if the account is still live
            is_live = api.is_room_alive(recorder.room_id)
            
            if not is_live:
                logger.info(f"{username} is no longer live. Stopping recording.")
                recorder.stop_recording()
                break
            
            # Wait before checking again
            await asyncio.sleep(30)
        
        # Update database
        db = init_database()  # Reload to get latest state
        if username in db["tracked_accounts"]:
            account_info = db["tracked_accounts"][username]
            account_info["is_recording"] = False
            account_info["is_live"] = False
            account_info["recording_end_time"] = datetime.now().isoformat()
            account_info["last_recording_file"] = str(output_file)
            save_database(db)
        
        logger.info(f"Recording for {username} finished. Sending to users...")
        
        # Get file size
        file_size_bytes = output_file.stat().st_size
        file_size_mb = file_size_bytes / (1024 * 1024)
        
        logger.info(f"Recording file size: {file_size_mb:.2f} MB")
        
        # Compress if too large for Telegram (50MB)
        compressed_file = None
        if file_size_bytes > 50 * 1024 * 1024:
            logger.info(f"File too large for Telegram ({file_size_mb:.2f} MB). Compressing...")
            
            # Notify users
            for user_id in account_info["tracked_by"]:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📦 The recording of @{username}'s livestream is too large ({file_size_mb:.2f} MB) for Telegram. Compressing while maintaining HD quality. This may take some time..."
                )
            
            # Compress the file
            compressed_file = output_file.with_name(f"{output_file.stem}_compressed{output_file.suffix}")
            
            # Use ffmpeg to compress
            subprocess.run([
                "ffmpeg",
                "-i", str(output_file),
                "-c:v", "libx264",
                "-crf", "23",
                "-preset", "medium",
                "-c:a", "aac",
                "-b:a", "128k",
                str(compressed_file)
            ], check=True)
            
            # Check compressed file size
            compressed_size_bytes = compressed_file.stat().st_size
            compressed_size_mb = compressed_size_bytes / (1024 * 1024)
            
            logger.info(f"Compressed file size: {compressed_size_mb:.2f} MB")
            
            # If still too large
            if compressed_size_bytes > 50 * 1024 * 1024:
                logger.warning(f"Compressed file still too large ({compressed_size_mb:.2f} MB)")
                
                # Try more aggressive compression
                more_compressed_file = output_file.with_name(f"{output_file.stem}_compressed_more{output_file.suffix}")
                
                subprocess.run([
                    "ffmpeg",
                    "-i", str(compressed_file),
                    "-c:v", "libx264",
                    "-crf", "28",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-b:a", "96k",
                    "-s", "854x480",  # Reduce resolution to 480p
                    str(more_compressed_file)
                ], check=True)
                
                # Replace compressed file
                compressed_file.unlink()
                compressed_file = more_compressed_file
                
                # Check new compressed file size
                compressed_size_bytes = compressed_file.stat().st_size
                compressed_size_mb = compressed_size_bytes / (1024 * 1024)
                
                logger.info(f"More compressed file size: {compressed_size_mb:.2f} MB")
        
        # Send file to all users who track this account
        for user_id in account_info["tracked_by"]:
            try:
                # Use the compressed file if available and needed
                send_file = compressed_file if compressed_file and file_size_bytes > 50 * 1024 * 1024 else output_file
                
                # If still too large, just notify
                if send_file.stat().st_size > 50 * 1024 * 1024:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ The recording of @{username}'s livestream is too large even after compression. The file has been saved locally. Contact the bot administrator to get it."
                    )
                    continue
                
                # Send a status message
                status_msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📤 Sending {'compressed ' if compressed_file and file_size_bytes > 50 * 1024 * 1024 else ''}recording of @{username}'s livestream..."
                )
                
                # Send the video file
                await context.bot.send_document(
                    chat_id=user_id,
                    document=send_file,
                    caption=f"🎬 Recording of @{username}'s livestream{'(compressed)' if compressed_file and file_size_bytes > 50 * 1024 * 1024 else ''}\nSize: {(compressed_size_mb if compressed_file and file_size_bytes > 50 * 1024 * 1024 else file_size_mb):.2f} MB\nRecorded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                
                # Update status message
                await context.bot.edit_message_text(
                    text=f"✅ Recording of @{username}'s livestream sent successfully!",
                    chat_id=user_id,
                    message_id=status_msg.message_id
                )
                
            except Exception as e:
                logger.error(f"Error sending recording to user {user_id}: {e}")
                
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"❌ Error sending the recording of @{username}'s livestream: {str(e)}"
                    )
                except:
                    pass
        
        # Clean up compressed file if it exists
        if compressed_file and compressed_file.exists():
            compressed_file.unlink()
            
    except Exception as e:
        logger.error(f"Error in monitor_recording for {username}: {e}")
        
        # Update database to mark recording as stopped
        db = init_database()
        if username in db["tracked_accounts"]:
            account_info = db["tracked_accounts"][username]
            account_info["is_recording"] = False
            account_info["is_live"] = False
            account_info["last_error"] = str(e)
            save_database(db)
            
            # Notify users
            for user_id in account_info["tracked_by"]:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"❌ Error recording @{username}'s livestream: {str(e)}"
                    )
                except:
                    pass

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
            # Skip accounts that are already being recorded
            if account_info.get("is_recording", False):
                logger.info(f"Skipping {username} - already recording")
                continue
            
            logger.info(f"Checking if {username} is live...")
            
            # Get room ID
            room_id = account_info.get("room_id") or api.get_room_id_from_user(username)
            
            if not room_id:
                logger.error(f"Could not find room ID for {username}")
                continue
            
            # Update room ID in database
            account_info["room_id"] = room_id
            account_info["last_checked"] = datetime.now().isoformat()
            save_database(db)
            
            # Check if live
            is_live = api.is_room_alive(room_id)
            
            # Update live status
            was_live = account_info.get("is_live", False)
            account_info["is_live"] = is_live
            
            if is_live:
                account_info["last_live"] = datetime.now().isoformat()
                logger.info(f"{username} is LIVE!")
                
                # Only start recording if not already recording and just went live
                if not was_live:
                    await start_recording_for_account(username, context)
            else:
                logger.info(f"{username} is not live.")
            
            save_database(db)
            
        except Exception as e:
            logger.error(f"Error checking {username}: {e}")

# Telegram Bot Command Handlers
async def start(update: Update, context: CallbackContext):
    """Handle the /start command"""
    user_id = update.effective_user.id
    db = init_database()
    ensure_user(db, user_id)
    
    keyboard = [
        [InlineKeyboardButton("📌 Add Account", callback_data="add_account")],
        [InlineKeyboardButton("📋 List Accounts", callback_data="list_accounts")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
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

async def help_command(update: Update, context: CallbackContext):
    """Handle the /help command"""
    await update.message.reply_text(
        f"📚 *Available Commands:*\n\n"
        f"📌 /add username - Track a TikTok account\n"
        f"📋 /list - Show your tracked accounts\n"
        f"🗑️ /remove username - Stop tracking an account\n"
        f"⚙️ /settings - Configure bot settings\n"
        f"🔄 /check - Manually check if tracked accounts are live\n"
        f"❓ /help - Show this help message\n\n"
        f"The bot automatically checks for livestreams every minute. When a tracked account goes live, "
        f"it will notify you and start recording. When the livestream ends, it will send you the recording.",
        parse_mode="Markdown"
    )

async def add_command(update: Update, context: CallbackContext):
    """Handle the /add command"""
    if not context.args or not context.args[0]:
        await update.message.reply_text(
            "📝 Please enter the TikTok username you want to track:\n\n"
            "Example: /add username"
        )
        return
    
    username = context.args[0]
    await add_account(update, context, username)

async def remove_command(update: Update, context: CallbackContext):
    """Handle the /remove command"""
    if not context.args or not context.args[0]:
        await update.message.reply_text(
            "📝 Please enter the TikTok username you want to remove:\n\n"
            "Example: /remove username"
        )
        return
    
    username = context.args[0]
    await remove_account(update, context, username)

async def list_command(update: Update, context: CallbackContext):
    """Handle the /list command"""
    await list_accounts(update, context)

async def check_command(update: Update, context: CallbackContext):
    """Handle the /check command"""
    if context.args and context.args[0]:
        username = context.args[0]
        await check_account(update, context, username)
    else:
        await check_all_accounts(update, context)

async def settings_command(update: Update, context: CallbackContext):
    """Handle the /settings command"""
    user_id = str(update.effective_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    settings = user["settings"]
    
    keyboard = [
        [InlineKeyboardButton(
            f"🔔 Notifications: {'ON' if settings['notify_on_live'] else 'OFF'}", 
            callback_data="toggle_notifications"
        )],
        [InlineKeyboardButton(
            f"🗜️ Auto-compress: {'ON' if settings['auto_compress'] else 'OFF'}", 
            callback_data="toggle_autocompress"
        )],
        [InlineKeyboardButton(
            f"🎥 Quality: {settings['record_quality']}", 
            callback_data="cycle_quality"
        )]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⚙️ *Settings*\n\n"
        f"🔔 Notify when account goes live: {'✅ ON' if settings['notify_on_live'] else '❌ OFF'}\n"
        f"🎥 Recording quality: {settings['record_quality']}\n"
        f"🗜️ Auto-compress large files: {'✅ ON' if settings['auto_compress'] else '❌ OFF'}\n\n"
        f"Select a setting to change:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def callback_handler(update: Update, context: CallbackContext):
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    db = init_database()
    user = ensure_user(db, user_id)
    
    # Initialize with empty acknowledgment
    await query.answer()
    
    # Handle different callback queries
    if query.data == "add_account":
        await query.message.reply_text(
            "📝 Please enter the TikTok username you want to track:\n\n"
            "Example: /add username"
        )
    
    elif query.data == "list_accounts":
        await list_accounts(update, context)
    
    elif query.data == "settings":
        await settings_command(update, context)
    
    elif query.data == "help":
        await help_command(update, context)
    
    elif query.data.startswith("remove_"):
        username = query.data.replace("remove_", "")
        await remove_account(update, context, username)
    
    elif query.data.startswith("check_"):
        username = query.data.replace("check_", "")
        await check_account(update, context, username)
    
    elif query.data == "toggle_notifications":
        settings = user["settings"]
        settings["notify_on_live"] = not settings["notify_on_live"]
        save_database(db)
        
        await query.edit_message_text(
            f"⚙️ *Settings*\n\n"
            f"🔔 Notify when account goes live: {'✅ ON' if settings['notify_on_live'] else '❌ OFF'}\n"
            f"🎥 Recording quality: {settings['record_quality']}\n"
            f"🗜️ Auto-compress large files: {'✅ ON' if settings['auto_compress'] else '❌ OFF'}\n\n"
            f"Setting updated! Notifications are now {'ON' if settings['notify_on_live'] else 'OFF'}.",
            parse_mode="Markdown",
            reply_markup=query.message.reply_markup
        )
    
    elif query.data == "toggle_autocompress":
        settings = user["settings"]
        settings["auto_compress"] = not settings["auto_compress"]
        save_database(db)
        
        await query.edit_message_text(
            f"⚙️ *Settings*\n\n"
            f"🔔 Notify when account goes live: {'✅ ON' if settings['notify_on_live'] else '❌ OFF'}\n"
            f"🎥 Recording quality: {settings['record_quality']}\n"
            f"🗜️ Auto-compress large files: {'✅ ON' if settings['auto_compress'] else '❌ OFF'}\n\n"
            f"Setting updated! Auto-compress is now {'ON' if settings['auto_compress'] else 'OFF'}.",
            parse_mode="Markdown",
            reply_markup=query.message.reply_markup
        )
    
    elif query.data == "cycle_quality":
        settings = user["settings"]
        
        # Cycle through quality options
        if settings["record_quality"] == "best":
            settings["record_quality"] = "high"
        elif settings["record_quality"] == "high":
            settings["record_quality"] = "medium"
        else:
            settings["record_quality"] = "best"
        
        save_database(db)
        
        await query.edit_message_text(
            f"⚙️ *Settings*\n\n"
            f"🔔 Notify when account goes live: {'✅ ON' if settings['notify_on_live'] else '❌ OFF'}\n"
            f"🎥 Recording quality: {settings['record_quality']}\n"
            f"🗜️ Auto-compress large files: {'✅ ON' if settings['auto_compress'] else '❌ OFF'}\n\n"
            f"Setting updated! Quality is now set to {settings['record_quality']}.",
            parse_mode="Markdown",
            reply_markup=query.message.reply_markup
        )

async def text_handler(update: Update, context: CallbackContext):
    """Handle regular text messages"""
    text = update.message.text.lower()
    
    if text.startswith("add "):
        username = text.split(" ")[1].strip()
        await add_account(update, context, username)
    
    elif text.startswith("remove "):
        username = text.split(" ")[1].strip()
        await remove_account(update, context, username)
    
    elif text.startswith("check "):
        username = text.split(" ")[1].strip()
        await check_account(update, context, username)
    
    elif text == "list":
        await list_accounts(update, context)
    
    elif text == "check":
        await check_all_accounts(update, context)
    
    elif text == "settings":
        await settings_command(update, context)
    
    elif text == "help":
        await help_command(update, context)

def main():
    """Start the bot"""
    # Get bot token from environment variable
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set. Please set it in the environment variables.")
        return
    
    # Create the application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("settings", settings_command))
    
    # Register callback query handler
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Register text message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    # Set up the job queue to periodically check tracked accounts
    job_queue = application.job_queue
    job_queue.run_repeating(check_tracked_accounts_job, interval=60, first=10)
    
    # Start the Bot
    application.run_polling()
    
    logger.info("Bot started!")

if __name__ == "__main__":
    main()
