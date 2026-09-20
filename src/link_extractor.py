import re
import urllib.parse
from typing import List

def extract_instagram_urls(text: str) -> List[str]:
    """
    Extracts and normalizes valid Instagram URLs from a text message.
    Deduplicates URLs and strips tracking query parameters.
    """
    # Base pattern matching Instagram domains and YouTube Shorts
    pattern = r"(https?://(?:www\.)?(?:instagram\.com|instagr\.am|youtube\.com/shorts|youtu\.be)[^\s]*)"
    raw_matches = re.findall(pattern, text)
    
    valid_urls = []
    
    for match in raw_matches:
        # Clean trailing punctuation
        match = match.rstrip(".,;:)'\"!]")
        
        try:
            parsed = urllib.parse.urlparse(match)
            # Remove query parameters to normalize the URL
            clean_url = urllib.parse.urlunparse((
                parsed.scheme if parsed.scheme else "https",
                parsed.netloc,
                parsed.path,
                "", "", ""
            ))
            
            # Allow all paths, yt-dlp will handle the specifics, but we enforce basic structures
            path = parsed.path
            
            # Supported shapes:
            # 1. /p/<code>/
            # 2. /reel/<code>/ or /reels/<code>/
            # 3. /tv/<code>/
            # 4. /stories/<username>/<id>/
            # 5. /<username>/reel/<code>/ or /<username>/p/<code>/
            # 6. instagr.am/... shortlinks
            # 7. /share/...
            
            is_valid_shape = False
            
            if parsed.netloc == "instagr.am" or parsed.netloc == "youtu.be":
                is_valid_shape = True
            elif re.match(r"^/shorts/.*", path):
                is_valid_shape = True
            elif re.match(r"^/(p|reel|reels|tv)/[^/]+/?", path):
                is_valid_shape = True
            elif re.match(r"^/stories/[^/]+/[^/]+/?", path):
                is_valid_shape = True
            elif re.match(r"^/[^/]+/(p|reel|reels|tv)/[^/]+/?", path):
                is_valid_shape = True
            elif re.match(r"^/share/.*", path):
                is_valid_shape = True
            elif re.match(r"^/(?!about|developer|explore|help|press|legal|privacy|terms)[^/]+/?$", path):
                # Potential profile link
                is_valid_shape = True
                
            if is_valid_shape and clean_url not in valid_urls:
                valid_urls.append(clean_url)
                
        except Exception:
            pass
            
    return valid_urls
