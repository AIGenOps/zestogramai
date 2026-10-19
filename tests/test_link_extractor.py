import pytest
from src.link_extractor import extract_instagram_urls, parse_text_urls, is_supported_url

def test_extract_single_post():
    text = "Check out this post: https://instagram.com/p/C1234567890/"
    urls = extract_instagram_urls(text)
    # Posts and carousels are no longer supported
    assert urls == []

def test_extract_reel():
    text = "Here is a reel: https://www.instagram.com/reel/C1234567890/"
    urls = extract_instagram_urls(text)
    assert urls == ["https://www.instagram.com/reel/C1234567890/"]

def test_extract_youtube_shorts():
    text = "Check this short: https://www.youtube.com/shorts/ABC123DEF45"
    urls = extract_instagram_urls(text)
    assert urls == ["https://www.youtube.com/shorts/ABC123DEF45"]

def test_extract_multiple_links():
    text = """
    Look at these two links:
    https://instagram.com/p/ABC/
    and
    https://instagram.com/reel/DEF/
    """
    urls = extract_instagram_urls(text)
    # Only reel link is extracted as /p/ is excluded
    assert len(urls) == 1
    assert "https://instagram.com/reel/DEF/" in urls

def test_extract_with_tracking_params():
    text = "https://www.instagram.com/reel/C1234567890/?igsh=MzRlODBiNWFlZA=="
    urls = extract_instagram_urls(text)
    # Tracking params should be stripped
    assert urls == ["https://www.instagram.com/reel/C1234567890/"]

def test_extract_shortlink():
    text = "https://instagr.am/reel/XYZ123/"
    urls = extract_instagram_urls(text)
    assert urls == ["https://instagr.am/reel/XYZ123/"]

def test_extract_stories_unsupported():
    text = "https://instagram.com/stories/username/1234567890/"
    supported, unsupported = parse_text_urls(text)
    assert len(supported) == 0
    assert len(unsupported) == 1

def test_ignore_invalid_links():
    text = "Check out https://google.com and https://instagram.com/about/"
    supported, unsupported = parse_text_urls(text)
    assert len(supported) == 0

def test_deduplication():
    text = "Here is a link https://instagram.com/reel/ABC/ and again https://instagram.com/reel/ABC/"
    urls = extract_instagram_urls(text)
    assert len(urls) == 1

def test_profile_link_unsupported():
    text = "https://instagram.com/zuck/"
    supported, unsupported = parse_text_urls(text)
    assert len(supported) == 0
    assert len(unsupported) == 1

def test_extract_facebook_reels():
    text = """
    Reel 1: https://www.facebook.com/reel/1234567890/
    Reel 2: https://www.facebook.com/reels/0987654321/
    Reel 3: https://www.facebook.com/share/r/14iL9mJ/
    """
    urls = extract_instagram_urls(text)
    assert len(urls) == 3
    assert "https://www.facebook.com/reel/1234567890/" in urls
    assert "https://www.facebook.com/reels/0987654321/" in urls
    assert "https://www.facebook.com/share/r/14iL9mJ/" in urls

def test_extract_facebook_watch_and_videos():
    text = """
    Watch 1: https://www.facebook.com/watch/?v=1122334455
    Watch 2: https://www.facebook.com/video.php?v=2233445566
    Video page: https://www.facebook.com/someuser/videos/3344556677/
    Share video: https://www.facebook.com/share/v/4455667788/
    Short link: https://fb.watch/abcXYZ123/
    """
    urls = extract_instagram_urls(text)
    assert len(urls) == 5
    assert "https://www.facebook.com/watch/?v=1122334455" in urls
    assert "https://www.facebook.com/video.php?v=2233445566" in urls
    assert "https://www.facebook.com/someuser/videos/3344556677/" in urls
    assert "https://www.facebook.com/share/v/4455667788/" in urls
    assert "https://fb.watch/abcXYZ123/" in urls

def test_extract_facebook_tracking_params():
    text = "https://www.facebook.com/watch/?v=987654321&mibextid=0cALme&fbclid=IwAR123"
    urls = extract_instagram_urls(text)
    assert urls == ["https://www.facebook.com/watch/?v=987654321"]

    reel_text = "https://www.facebook.com/reel/1234567890/?s=yWDuG2&fs=e&mibextid=0cALme"
    reel_urls = extract_instagram_urls(reel_text)
    assert reel_urls == ["https://www.facebook.com/reel/1234567890/"]

def test_facebook_unsupported_links():
    text = """
    Profile: https://www.facebook.com/zuck/
    Group: https://www.facebook.com/groups/123456789/
    Photo: https://www.facebook.com/photo.php?fbid=123456789
    Watch without ID: https://www.facebook.com/watch/
    """
    supported, unsupported = parse_text_urls(text)
    assert len(supported) == 0
    assert len(unsupported) == 4

