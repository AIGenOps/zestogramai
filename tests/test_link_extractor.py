import pytest
from src.link_extractor import extract_instagram_urls

def test_extract_single_post():
    text = "Check out this post: https://instagram.com/p/C1234567890/"
    urls = extract_instagram_urls(text)
    assert urls == ["https://instagram.com/p/C1234567890/"]

def test_extract_reel():
    text = "Here is a reel: https://www.instagram.com/reel/C1234567890/"
    urls = extract_instagram_urls(text)
    assert urls == ["https://www.instagram.com/reel/C1234567890/"]

def test_extract_multiple_links():
    text = """
    Look at these two links:
    https://instagram.com/p/ABC/
    and
    https://instagram.com/reel/DEF/
    """
    urls = extract_instagram_urls(text)
    assert len(urls) == 2
    assert "https://instagram.com/p/ABC/" in urls
    assert "https://instagram.com/reel/DEF/" in urls

def test_extract_with_tracking_params():
    text = "https://www.instagram.com/p/C1234567890/?igsh=MzRlODBiNWFlZA=="
    urls = extract_instagram_urls(text)
    # Tracking params should be stripped
    assert urls == ["https://www.instagram.com/p/C1234567890/"]

def test_extract_shortlink():
    text = "https://instagr.am/p/XYZ123/"
    urls = extract_instagram_urls(text)
    assert urls == ["https://instagr.am/p/XYZ123/"]
    
def test_extract_share_link():
    text = "https://www.instagram.com/share/some_token"
    urls = extract_instagram_urls(text)
    assert urls == ["https://www.instagram.com/share/some_token"]

def test_extract_stories():
    text = "https://instagram.com/stories/username/1234567890/"
    urls = extract_instagram_urls(text)
    assert urls == ["https://instagram.com/stories/username/1234567890/"]

def test_ignore_invalid_links():
    text = "Check out https://google.com and https://instagram.com/about/"
    urls = extract_instagram_urls(text)
    # The /about/ page doesn't match our valid shapes
    assert len(urls) == 0

def test_deduplication():
    text = "Here is a link https://instagram.com/p/ABC/ and again https://instagram.com/p/ABC/"
    urls = extract_instagram_urls(text)
    assert len(urls) == 1

def test_profile_link():
    text = "https://instagram.com/zuck/"
    urls = extract_instagram_urls(text)
    assert urls == ["https://instagram.com/zuck/"]
