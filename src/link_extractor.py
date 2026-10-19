import re
import urllib.parse
from typing import List, Tuple

def is_supported_url(url: str) -> bool:
    """
    Checks if a URL belongs to a supported platform:
    1. Instagram Reels (/reel/, /reels/, /share/r/, /share/reel/, /<username>/reel/)
    2. YouTube Shorts (/shorts/, youtu.be shortlinks)
    3. Facebook Reels & Videos (/reel/, /reels/, /share/r/, /share/v/, /<username>/videos/, /watch, /video.php, fb.watch, fb.gg)
    """
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        
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

        # Facebook Reels & Videos checks
        if any(domain in netloc for domain in ("facebook.com", "fb.watch", "fb.gg")):
            if "fb.watch" in netloc and path.strip("/"):
                return True
            if re.search(r"/(?:reels?|share/(?:r|v))/([^/?#&]+)", path, re.IGNORECASE):
                return True
            if re.search(r"^/[^/]+/videos/(?:[^/]+/)?([^/?#&]+)", path, re.IGNORECASE):
                return True
            clean_path = path.rstrip("/")
            if clean_path in ("/watch", "/video.php") or clean_path.startswith("/watch/"):
                if qs.get("v") or qs.get("video_id"):
                    return True
            return False
            
        return False
    except Exception:
        return False

def parse_text_urls(text: str) -> Tuple[List[str], List[str]]:
    """
    Parses URLs from text message and categorizes them into:
    - supported_urls: Instagram Reels, YouTube Shorts, Facebook Reels & Videos
    - unsupported_urls: All other URLs (Posts, Stories, Watch without ID, TikTok, etc.)
    Deduplicates URLs and strips tracking query parameters.
    """
    pattern = r"(https?://[^\s]+)"
    raw_matches = re.findall(pattern, text)
    
    supported_urls = []
    unsupported_urls = []
    
    for match in raw_matches:
        match = match.rstrip(".,;:)'\"!]")
        try:
            parsed = urllib.parse.urlparse(match)
            netloc = parsed.netloc.lower()
            path = parsed.path
            
            clean_query = ""
            if any(domain in netloc for domain in ("facebook.com", "fb.watch", "fb.gg")):
                clean_path = path.rstrip("/")
                if clean_path in ("/watch", "/video.php") or clean_path.startswith("/watch/"):
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "v" in qs and qs["v"]:
                        clean_query = f"v={qs['v'][0]}"
                    elif "video_id" in qs and qs["video_id"]:
                        clean_query = f"video_id={qs['video_id'][0]}"

            clean_url = urllib.parse.urlunparse((
                parsed.scheme if parsed.scheme else "https",
                parsed.netloc,
                parsed.path,
                "", clean_query, ""
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
