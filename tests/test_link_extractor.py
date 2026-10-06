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
