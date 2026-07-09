"""QQ Music & NetEase web API functions — search, lyrics, album art, LRC parsing.

Extracted from core.reader to keep the reader module focused on detection
and candidate gathering.
"""
import urllib.parse
import urllib.request
import json
import base64
import re
import io
import threading
from PIL import Image
from core.logs import get_logger

_api_log = get_logger("web_api")


# ========== Web API ==========

def _search_qqmusic(song_name, artist):
    """Search for song in QQ Music and return (songmid, albummid).

    并行搜索策略：如果歌名含括号，同时搜索完整名和去括号名，
    哪边先返回结果就用哪边。
    """
    try:
        def _do_search(keyword):
            """单次搜索请求 (timeout=5s 保障速度)。"""
            url = ("https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg?"
                   f"key={urllib.parse.quote(keyword)}&format=json")
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://y.qq.com/",
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))

        # 收集需要搜索的关键词 (多种 fallback 策略)
        keywords = [song_name]
        import re

        # 去括号
        stripped_paren = re.sub(r"\s*\(.*?\)", "", song_name).strip()
        if stripped_paren and stripped_paren != song_name:
            keywords.append(stripped_paren)

        # 去方括号
        stripped_bracket = re.sub(r"\s*\[.*?\]", "", song_name).strip()
        if stripped_bracket and stripped_bracket != song_name and stripped_bracket not in keywords:
            keywords.append(stripped_bracket)

        # 取斜杠前的英文部分 (如 "Komm, susser Tod / 甘き死よ")
        if "/" in song_name:
            before_slash = song_name.split("/")[0].strip()
            # 再去掉括号后缀
            before_slash = re.sub(r"\s*[\(\[].*$", "", before_slash).strip()
            if before_slash and before_slash != song_name and before_slash not in keywords:
                keywords.append(before_slash)

        # 纯 ASCII 部分 (去除 CJK 字符)
        ascii_part = re.sub(r'[^\x00-\x7F\s]', '', song_name).strip()
        ascii_part = re.sub(r'\s+', ' ', ascii_part).strip()
        # 去掉末尾标点
        ascii_part = re.sub(r'[,.\-/:;!\?\s]+$', '', ascii_part).strip()
        if ascii_part and len(ascii_part) > 3 and ascii_part not in keywords:
            keywords.append(ascii_part)

        # 并行搜索所有关键词
        raw_results = {}
        def _search(kw):
            try:
                raw_results[kw] = _do_search(kw)
            except Exception as e:
                _api_log.warn(f"search keyword {kw!r} failed: {e}")

        threads = [threading.Thread(target=_search, args=(kw,), daemon=True)
                   for kw in keywords]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=6)

        # 合并结果（去重）
        seen_song = set()
        songs = []
        seen_album = set()
        albums = []
        for kw in keywords:
            data = raw_results.get(kw)
            if not data:
                continue
            for s in data.get("data", {}).get("song", {}).get("itemlist", []):
                mid = s.get("mid")
                if mid and mid not in seen_song:
                    seen_song.add(mid)
                    songs.append(s)
            for a in data.get("data", {}).get("album", {}).get("itemlist", []):
                mid = a.get("mid")
                if mid and mid not in seen_album:
                    seen_album.add(mid)
                    albums.append(a)

        # 匹配最佳结果
        songmid = None
        matched_song = None
        if artist:
            for s in songs:
                if artist.lower() in s.get("singer", "").lower():
                    matched_song = s
                    break
        if not matched_song and songs:
            matched_song = songs[0]
        if matched_song:
            songmid = matched_song.get("mid")

        albummid = None
        if artist:
            for a in albums:
                if artist.lower() in a.get("singer", "").lower():
                    albummid = a.get("mid")
                    break
        if not albummid and albums:
            albummid = albums[0].get("mid")

        # Fallback: get albummid from song detail API
        if not albummid and songmid:
            albummid = _get_albummid_from_song(songmid)

        # ★ Extract interval from the matched song (already in search result,
        #   no additional API call needed).  Falls back to 0 if unavailable.
        interval = int(matched_song.get("interval", 0) or 0) if matched_song else 0

        _api_log.info(f"search_qqmusic: song={song_name!r} artist={artist!r} "
                   f"→ songmid={songmid} albummid={albummid} interval={interval}s "
                   f"matched_song={matched_song.get('name','') if matched_song else 'None'} "
                   f"n_songs={len(songs)} n_albums={len(albums)}")
        return songmid, albummid, interval
    except Exception:
        return None, None, 0


def _get_albummid_from_song(songmid, get_interval=False):
    if not songmid:
        return (None, 0) if get_interval else None
    try:
        url = (f"https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
               f"?songmid={songmid}&format=json&tpl=yqq_song_detail")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://y.qq.com/"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        songs = data.get("data", [])
        if songs:
            album = songs[0].get("album", {})
            am = album.get("mid")
            interval = songs[0].get("interval", 0) or 0
            if get_interval:
                return am, int(interval)
            return am
    except Exception as e:
        _api_log.warn(f"_fetch_qq_album_data failed: {e}")
    return (None, 0) if get_interval else None


def _fetch_album_art(albummid, size=300):
    if not albummid:
        return None
    url = f"https://y.gtimg.cn/music/photo_new/T002R{size}x{size}M000{albummid}.jpg"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://y.qq.com/"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return Image.open(io.BytesIO(resp.read()))
    except Exception:
        return None


def _fetch_lyrics(songmid):
    if not songmid:
        return "", ""
    try:
        url = ("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
               f"?songmid={songmid}&format=json")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://y.qq.com/"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        lyric_raw = data.get("lyric", "")
        trans_raw = data.get("trans", "")
        try:
            lyric = base64.b64decode(lyric_raw).decode("utf-8") if lyric_raw else ""
        except Exception:
            lyric = lyric_raw or ""
        try:
            trans = base64.b64decode(trans_raw).decode("utf-8") if trans_raw else ""
        except Exception:
            trans = trans_raw or ""
        return lyric, trans
    except Exception:
        return "", ""


# ========== NetEase Cloud Music API ==========

_NETEASE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36")
_NETEASE_REFERER = "https://music.163.com/"


def _search_netease(song_name, artist):
    """Search NetEase Cloud Music and return best match sorted by popularity.

    Returns (song_id, song_name, artist_name, album_name) or None.
    Uses the /api/search/pc endpoint which returns popularity data.
    """
    if not song_name:
        return None
    try:
        keyword = song_name
        if artist:
            keyword = f"{song_name} {artist}"
        url = ("https://music.163.com/api/search/pc?"
               f"s={urllib.parse.quote(keyword)}&type=1&limit=30")
        req = urllib.request.Request(url, headers={
            "User-Agent": _NETEASE_UA,
            "Referer": _NETEASE_REFERER,
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        songs = data.get("result", {}).get("songs", [])
        if not songs:
            return None

        # Score each result: popularity (play count) weighted + artist match bonus
        def _score(s):
            pop = s.get("popularity", 0) or 0
            s_name = s.get("name", "").lower()
            s_artists = " ".join(
                a.get("name", "") for a in s.get("artists", [])).lower()
            artist_lower = (artist or "").lower()
            song_lower = song_name.lower()

            score = float(pop)
            # Artist match bonus
            if artist_lower and artist_lower in s_artists:
                score += 200
            elif artist_lower and s_artists in artist_lower:
                score += 100
            # Song name exact match bonus
            if song_lower == s_name:
                score += 150
            elif song_lower in s_name or s_name in song_lower:
                score += 50
            # Penalty for very different names
            if song_lower not in s_name and s_name not in song_lower:
                score -= 30
            return score

        best = max(songs, key=_score)
        best_name = best.get("name", "")
        best_artist = " ".join(
            a.get("name", "") for a in best.get("artists", []))
        best_album = best.get("album", {}).get("name", "")
        _api_log.info(f"netease search: {song_name!r} / {artist!r} "
                   f"→ id={best['id']} name={best_name!r} "
                   f"artist={best_artist!r} pop={best.get('popularity',0)}")
        return best["id"], best_name, best_artist, best_album
    except Exception as e:
        _api_log.info(f"netease search error: {e}")
        return None


def _fetch_lyrics_netease(song_id):
    """Fetch lyrics + translation from NetEase Cloud Music API.

    Returns (lyric_lrc, trans_lrc) — both are raw LRC strings.
    NetEase's tlyric field contains human translations (always populated).
    """
    if not song_id:
        return "", ""
    try:
        url = (f"https://music.163.com/api/song/lyric?"
               f"id={song_id}&lv=1&kv=1&tv=-1")
        req = urllib.request.Request(url, headers={
            "User-Agent": _NETEASE_UA,
            "Referer": _NETEASE_REFERER,
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        lrc_obj = data.get("lrc", {}) or {}
        tlyric_obj = data.get("tlyric", {}) or {}
        lyric = lrc_obj.get("lyric", "") or ""
        trans = tlyric_obj.get("lyric", "") or ""
        return lyric, trans
    except Exception as e:
        _api_log.info(f"netease lyrics fetch error: {e}")
        return "", ""


def parse_lrc_timestamps(lrc_text):
    pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\]')
    lines = []
    for line in lrc_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        matches = pattern.findall(line)
        if not matches:
            continue
        text_part = pattern.sub("", line).strip()
        for m in matches:
            mm, ss, ms = int(m[0]), int(m[1]), int(m[2])
            if len(m[2]) == 2:
                ms *= 10
            timestamp = mm * 60000 + ss * 1000 + ms
            lines.append((timestamp, text_part))
    lines.sort(key=lambda x: x[0])
    return lines
