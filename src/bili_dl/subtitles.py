"""Fetch the first Bilibili subtitle track and save its timed cues as UTF-8 SRT."""

from __future__ import annotations

import contextlib
import math
import re
import tempfile
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from . import transport
from .config import CONTENT_API_TIMEOUT, PLAYER_INFO_API, VIDEO_INFO_API
from .models import DownloadConfig, DownloadResult


class SubtitleError(ValueError):
    """A display-safe content failure; never includes cookies or signed URLs."""


def _video_ref(value: str) -> tuple[dict[str, str], int]:
    """Parse a BV/AV identifier or a regular video URL, retaining its part."""
    value = value.strip()
    part = 1
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "bilibili.com",
            "www.bilibili.com",
            "m.bilibili.com",
        }:
            raise SubtitleError("请提供 B 站普通视频链接或 BV/AV 号")
        match = re.fullmatch(r"/video/([^/]+)/?", parsed.path)
        if not match:
            raise SubtitleError("字幕模式目前支持普通投稿视频链接")
        value = match[1]
        try:
            part = int(urllib.parse.parse_qs(parsed.query).get("p", ["1"])[0])
        except ValueError:
            raise SubtitleError("分 P 参数 p 必须是正整数") from None
    if part < 1:
        raise SubtitleError("分 P 参数 p 必须是正整数")
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        return {"bvid": value}, part
    if re.fullmatch(r"av[0-9]+", value, flags=re.IGNORECASE):
        return {"aid": value[2:]}, part
    raise SubtitleError("无法识别视频的 BV/AV 号")


def _api_data(
    opener: urllib.request.OpenerDirector, endpoint: str, params: dict[str, str], stage: str
) -> dict[str, Any]:
    payload, failure = transport.fetch_json(
        opener,
        transport.request(f"{endpoint}?{urllib.parse.urlencode(params)}"),
        timeout=CONTENT_API_TIMEOUT,
    )
    if payload is None:
        raise SubtitleError(f"{stage}失败：{failure.describe() if failure else '未知错误'}")
    code = payload.get("code")
    if code != 0:
        if code == -101:
            raise SubtitleError("登录已失效，请运行 bili-dl login 后重试")
        detail = f"错误码 {code}" if isinstance(code, int) else "异常响应"
        raise SubtitleError(f"{stage}失败：{detail}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SubtitleError(f"{stage}失败：返回数据格式异常")
    return data


def _subtitle_url(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise SubtitleError("第一条字幕轨没有可用地址，请检查登录状态后重试")
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    if parsed.scheme not in {"https", "http"} or not any(
        host == domain or host.endswith("." + domain) for domain in ("hdslb.com", "bilibili.com")
    ):
        raise SubtitleError("第一条字幕轨的地址格式异常")
    if parsed.username or parsed.password:
        raise SubtitleError("第一条字幕轨的地址格式异常")
    return str(urllib.parse.urlunsplit(parsed._replace(scheme="https")))


def _timestamp(seconds: int | float) -> str:
    milliseconds = int((Decimal(str(seconds)) * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_int, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_int:02},{ms:03}"


def _to_srt(body: Any) -> str:
    """Validate every cue before writing; never silently omit malformed sentences."""
    if not isinstance(body, list) or not body:
        raise SubtitleError("第一条字幕轨内容为空或格式异常")
    cues = []
    for index, cue in enumerate(body, 1):
        if not isinstance(cue, dict):
            raise SubtitleError(f"第 {index} 条字幕格式异常")
        start, end, content = cue.get("from"), cue.get("to"), cue.get("content")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end < start
            or not isinstance(content, str)
            or not content.strip()
        ):
            raise SubtitleError(f"第 {index} 条字幕的时间戳或正文异常")
        # Empty lines delimit SRT cues; keep multiline text within a single cue.
        text = "\n".join(line for line in content.splitlines() if line.strip())
        cues.append(f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}\n\n")
    return "".join(cues)


def _filename_part(value: str, max_bytes: int = 150) -> str:
    # Limit UTF-8 bytes as well as removing Windows-invalid characters: Unix
    # filesystems generally cap a filename at 255 bytes, not 255 characters.
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip(" .")
    return clean.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").rstrip(" .")


def _write_srt(path: Path, text: str) -> None:
    """Replace only after a complete write, preserving existing output on failure."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".subtitle-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)


def download(url: str, cfg: DownloadConfig) -> DownloadResult:
    """Select exactly subtitles[0]; do not prefer a language or fall back to later tracks."""
    try:
        public_opener = transport.cookie_opener(proxy=cfg.proxy)
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.hostname in {"b23.tv", "www.b23.tv"} and parsed.scheme in {"http", "https"}:
            resolved, failure = transport.resolve_url(
                public_opener,
                urllib.parse.urlunsplit(parsed._replace(scheme="https")),
                CONTENT_API_TIMEOUT,
            )
            if resolved is None:
                raise SubtitleError(
                    f"短链接解析失败：{failure.describe() if failure else '未知错误'}"
                )
            url = resolved
        params, part = _video_ref(url)
        jar, error = transport.load_cookie_jar(cfg.cookie_path)
        if jar is None:
            raise SubtitleError(error or "无法读取现有 Cookie")
        opener = transport.cookie_opener(jar, proxy=cfg.proxy)
        info = _api_data(opener, VIDEO_INFO_API, params, "获取视频信息")
        pages = info.get("pages")
        if not isinstance(pages, list) or part > len(pages):
            raise SubtitleError("请求的分 P 不存在，或视频分 P 信息异常")
        page = pages[part - 1]
        cid = page.get("cid") if isinstance(page, dict) else None
        aid = info.get("aid")
        if not isinstance(cid, int) or cid <= 0 or not isinstance(aid, int) or aid <= 0:
            raise SubtitleError("视频缺少有效的 aid/cid")
        player = _api_data(
            opener, PLAYER_INFO_API, {"aid": str(aid), "cid": str(cid)}, "获取字幕列表"
        )
        if player.get("need_login_subtitle"):
            raise SubtitleError("此视频字幕需要有效登录，请运行 bili-dl login 后重试")
        subtitle = player.get("subtitle")
        tracks = subtitle.get("subtitles") if isinstance(subtitle, dict) else None
        if not isinstance(tracks, list):
            raise SubtitleError("字幕列表格式异常")
        if not tracks:
            raise SubtitleError("当前分 P 没有可提取的字幕")
        first = tracks[0]
        if not isinstance(first, dict):
            raise SubtitleError("第一条字幕轨格式异常")
        subtitle_url = _subtitle_url(first.get("subtitle_url"))
        # CDN and share-link requests never carry the authenticated CookieJar.
        payload, failure = transport.fetch_json(
            public_opener, transport.request(subtitle_url), timeout=CONTENT_API_TIMEOUT
        )
        if payload is None:
            raise SubtitleError(f"下载字幕失败：{failure.describe() if failure else '未知错误'}")
        text = _to_srt(payload.get("body"))
        bvid = str(info.get("bvid") or f"av{aid}")
        title = _filename_part(str(info.get("title") or bvid)) or "subtitle"
        language = _filename_part(str(first.get("lan") or "unknown"), 32) or "unknown"
        filename = f"{title} [{_filename_part(bvid, 20)}] p{part}.{language}.srt"
        path = cfg.video_dir / filename
        _write_srt(path, text)
    except (ValueError, OverflowError) as exc:
        # Generic parsing errors can contain signed URLs; only expose our safe failures.
        detail = str(exc) if isinstance(exc, SubtitleError) else "视频或字幕数据格式异常"
        return DownloadResult(False, [("error", f"[失败] {detail}")])
    except OSError:
        return DownloadResult(False, [("error", "[失败] 无法写入字幕文件，请检查输出目录权限")])
    return DownloadResult(
        True,
        [("info", f"[字幕] 已选择返回的第一条字幕轨：{language}"), ("ok", f"[完成!] 字幕: {path}")],
        path,
    )
