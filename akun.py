#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Telethon Authentication Script
# This script is used to authorize your Telethon client for sending videos
# Run this script first before running the main bot

import os
import sys
import logging
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

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
PHONE_NUMBER = "+6285695499059"  # Replace with your phone number in international format

async def main():
    """Authenticate Telethon client"""
    logger.info("Starting Telethon authentication")
    
    # Check if API credentials are set
    if API_ID == 25649945 or API_HASH == "d91f3e307f5ee75e57136421f2c3adc6":
        logger.error("Please set your API ID and API Hash in the script")
        logger.info("You can get API ID and API Hash from https://my.telegram.org")
        return
    
    # Create client
    client = TelegramClient('tiktok_recorder_session', API_ID, API_HASH)
    
    # Connect to Telegram
    await client.connect()
    
    # Check if already authorized
    if await client.is_user_authorized():
        logger.info("Already authorized")
        return
    
    # Send code request
    try:
        logger.info("Sending code request...")
        await client.send_code_request(PHONE_NUMBER)
        
        # Ask for the verification code
        verification_code = input("Enter the verification code you received: ")
        
        # Sign in
        try:
            await client.sign_in(PHONE_NUMBER, verification_code)
            logger.info("Successfully signed in!")
        
        except SessionPasswordNeededError:
            # Two-step verification is enabled
            password = input("Two-step verification is enabled. Please enter your password: ")
            await client.sign_in(password=password)
            logger.info("Successfully signed in with two-step verification!")
            
    except Exception as e:
        logger.error(f"Error during authentication: {e}")
        return
    
    # Disconnect
    await client.disconnect()
    logger.info("Authentication complete. You can now use the main bot.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
