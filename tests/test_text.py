from sentiment_radar.utils.text import (
    content_hash,
    normalize_url,
    strip_html,
    truncate,
)


def test_strip_html_removes_tags_and_entities():
    assert strip_html("<b>삼성전자</b> &quot;급등&quot;") == '삼성전자 "급등"'
    assert strip_html(None) == ""


def test_normalize_url_removes_tracking_and_www():
    a = "https://www.example.com/news?id=1&utm_source=naver&utm_medium=rss"
    b = "http://example.com/news/?id=1"
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_strips_fragment():
    assert normalize_url("https://x.com/a#section") == "https://x.com/a"


def test_content_hash_stable_and_title_insensitive_to_html():
    h1 = content_hash("<b>반도체</b> 강세", "https://x.com/a?utm_source=z")
    h2 = content_hash("반도체 강세", "https://x.com/a")
    assert h1 == h2


def test_truncate():
    assert truncate("abcdef", 3) == "abc"
    assert truncate(None, 3) == ""
