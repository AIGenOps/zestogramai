import re
import urllib.parse
from typing import List, Tuple

def is_supported_url(url: str) -> bool:
    """
    Checks if a URL belongs to a supported platform:
    1. Instagram Reels (/reel/, /reels/, /share/r/, /share/reel/, /<username>/reel/)
    2. YouTube Shorts (/shorts/, youtu.be shortlinks)
    """
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        path = parsed.path
        
        # Instagram Reel checks
        if any(domain in netloc for domain in ("instagram.com", "instagr.am")):
            if re.search(r"/(?:reels?|share/(?:r|reel))/([^/?#&]+)", path, re.IGNORECASE):
                return True
            if re.search(r"^/[^/]+/(?:reels?)/([^/?#&]+)", path, re.IGNORECASE):
                return True
            return False
            
        # YouTube Shorts checks
        if any(domain in netloc for domain in ("youtube.com", "youtu.be", "y2u.be")):
            if re.search(r"^/shorts/([^/?#&]+)", path, re.IGNORECASE):
                return True
            if netloc in ("youtu.be", "y2u.be") and path.strip("/"):
                return True
            return False
            
        return False
    except Exception:
        return False

def parse_text_urls(text: str) -> Tuple[List[str], List[str]]:
    """
    Parses URLs from text message and categorizes them into:
    - supported_urls: Instagram Reels & YouTube Shorts
    - unsupported_urls: All other URLs (Posts, Stories, Watch, TikTok, etc.)
    Deduplicates URLs and strips query parameters.
    """
    pattern = r"(https?://[^\s]+)"
    raw_matches = re.findall(pattern, text)
    
    supported_urls = []
    unsupported_urls = []
    
    for match in raw_matches:
        match = match.rstrip(".,;:)'\"!]")
        try:
            parsed = urllib.parse.urlparse(match)
            clean_url = urllib.parse.urlunparse((
                parsed.scheme if parsed.scheme else "https",
                parsed.netloc,
                parsed.path,
                "", "", ""
            ))
            
            if is_supported_url(clean_url):
                if clean_url not in supported_urls:
                    supported_urls.append(clean_url)
            else:
                if clean_url not in unsupported_urls:
                    unsupported_urls.append(clean_url)
        except Exception:
            pass
            
    return supported_urls, unsupported_urls

def extract_instagram_urls(text: str) -> List[str]:
    """
    Backward-compatible helper that returns only supported URLs.
    """
    supported, _ = parse_text_urls(text)
    return supported
