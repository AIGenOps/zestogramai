import asyncio
import os
import sys

# Make sure we can import from src
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.downloader import _download_sync

async def main():
    url = "https://www.instagram.com/p/DAW0uQzSt4n/" # Known Instagram carousel (e.g., from a public page)
    output_dir = "/tmp/test_carousel"
    os.makedirs(output_dir, exist_ok=True)
    try:
        files = await asyncio.to_thread(_download_sync, url, output_dir, False, None)
        print("DOWNLOADED FILES:")
        for f in files:
            print(f)
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
