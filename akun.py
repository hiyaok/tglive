#
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Telethon Authentication Script
# This script is used to authorize your Telethon client for sending videos
# Run this script first before running the main bot

import os
import sys
import logging
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, ApiIdInvalidError

# Configure logging
logging.basicConfig(
    format='[*] %(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telethon API info - REPLACE WITH YOUR OWN
API_ID = 25649945  # Replace with your Telegram API ID
API_HASH = "d91f3e307f5ee75e57136421f2c3adc6"  # Replace with your Telegram API Hash

async def main():
    """Authenticate Telethon client"""
    logger.info("======= TELETHON AUTHENTICATION =======")
    logger.info("This script will create a session file to use with the main bot")
    
    # Check if API credentials are set
    if API_ID == 12345678 or API_HASH == "your_api_hash_here":
        logger.error("ERROR: Please edit this script and set your API ID and API Hash first!")
        logger.info("1. Go to https://my.telegram.org and log in")
        logger.info("2. Click on 'API Development tools'")
        logger.info("3. Create a new application")
        logger.info("4. Copy the API ID and API Hash to this script")
        logger.info("5. Run this script again")
        return
    
    # Ask for phone number in terminal
    print("\nPlease enter your Telegram account phone number:")
    phone_number = input("Phone number (with country code, e.g. +6281234567890): ")
    
    if not phone_number or not phone_number.startswith("+"):
        logger.error("Invalid phone number format. Please include the country code with '+' prefix")
        return
    
    # Create client
    try:
        logger.info(f"Initializing Telethon client with API ID: {API_ID}")
        client = TelegramClient('tiktok_recorder_session', API_ID, API_HASH)
    
        # Connect to Telegram
        await client.connect()
        
        # Check if already authorized
        if await client.is_user_authorized():
            logger.info("Account already authorized! Session file exists and is valid.")
            logger.info("You can now run the main bot.")
            return
        
        # Send code request
        try:
            logger.info(f"Sending verification code to {phone_number}...")
            await client.send_code_request(phone_number)
            
            # Ask for the verification code
            print("\nA verification code has been sent to your Telegram account.")
            verification_code = input("Enter the verification code you received: ")
            
            # Sign in
            try:
                await client.sign_in(phone_number, verification_code)
                logger.info("Successfully signed in!")
                logger.info("Session file created successfully!")
                logger.info("You can now run the main bot.")
            
            except SessionPasswordNeededError:
                # Two-step verification is enabled
                print("\nTwo-step verification is enabled on your account.")
                password = input("Please enter your Telegram account password: ")
                await client.sign_in(password=password)
                logger.info("Successfully signed in with two-step verification!")
                logger.info("Session file created successfully!")
                logger.info("You can now run the main bot.")
                
        except Exception as e:
            logger.error(f"Error during authentication: {e}")
            logger.info("Please try again or use a different phone number.")
            return
    
    except ApiIdInvalidError:
        logger.error("Invalid API ID or API Hash. Please check your credentials.")
        logger.info("Make sure you've entered the correct values from https://my.telegram.org")
        return
    except Exception as e:
        logger.error(f"Error initializing Telethon client: {e}")
        return
        
    # Disconnect
    await client.disconnect()
    logger.info("Authentication process completed.")

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Authentication cancelled by user.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
