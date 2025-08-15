import os 
import discord
from dotenv import load_dotenv
from src.bot import MyBot

load_dotenv()

def main():
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN is not set in the .env file.")
        return
    
    bot = MyBot()
    print("机器人正在启动...")
    bot.run(DISCORD_TOKEN)
if __name__ == "__main__":
    main()