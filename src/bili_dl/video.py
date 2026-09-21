"""Shared Bilibili video-reference parsing and metadata resolution."""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import transport
from .config import CONTENT_API_TIMEOUT, VIDEO_INFO_API


class VideoError(ValueError):
    """A display-safe video metadata failure."""


def _split_url(value: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise ValueError
        return parsed
    except ValueError:
        raise VideoError("视频链接格式异常") from None


@dataclass(frozen=True)
class VideoInfo:
    """Resolved metadata shared by subtitle and comment downloads."""

    aid: int
    bvid: str
    title: str
    part: int
    pages: list[Any]


def parse_reference(value: str) -> tuple[dict[str, str], int]:
    """Parse a BV/AV identifier or regular video URL, retaining its part."""
    value = value.strip()
    part = 1
    if "://" in value:
        parsed = _split_url(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "bilibili.com",
            "www.bilibili.com",
            "m.bilibili.com",
        }:
            raise VideoError("请提供 B 站普通视频链接或 BV/AV 号")
        match = re.fullmatch(r"/video/([^/]+)/?", parsed.path)
        if not match:
            raise VideoError("目前支持普通投稿视频链接")
        value = match[1]
        try:
            part = int(urllib.parse.parse_qs(parsed.query).get("p", ["1"])[0])
        except ValueError:
            raise VideoError("分 P 参数 p 必须是正整数") from None
    if part < 1:
        raise VideoError("分 P 参数 p 必须是正整数")
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        return {"bvid": value}, part
    if re.fullmatch(r"av[0-9]{1,20}", value, flags=re.IGNORECASE) and int(value[2:]) > 0:
        return {"aid": value[2:]}, part
    raise VideoError("无法识别视频的 BV/AV 号")


def api_data(
    opener: urllib.request.OpenerDirector, endpoint: str, params: dict[str, str], stage: str
) -> dict[str, Any]:
    payload, failure = transport.fetch_json(
        opener,
        transport.request(f"{endpoint}?{urllib.parse.urlencode(params)}"),
        timeout=CONTENT_API_TIMEOUT,
    )
    if payload is None:
        raise VideoError(f"{stage}失败：{failure.describe() if failure else '未知错误'}")
    code = payload.get("code")
    if type(code) is not int or code != 0:
        if code == -101:
            raise VideoError("登录已失效，请运行 bili-dl login 后重试")
        detail = f"错误码 {code}" if isinstance(code, int) else "异常响应"
        raise VideoError(f"{stage}失败：{detail}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise VideoError(f"{stage}失败：返回数据格式异常")
    return data


def resolve(
    opener: urllib.request.OpenerDirector,
    public_opener: urllib.request.OpenerDirector,
    value: str,
) -> VideoInfo:
    """Resolve a video reference, using no session Cookie for share redirects."""
    parsed = _split_url(value.strip())
    if parsed.hostname in {"b23.tv", "www.b23.tv"} and parsed.scheme in {"http", "https"}:
        resolved, failure = transport.resolve_url(
            public_opener,
            urllib.parse.urlunsplit(parsed._replace(scheme="https")),
            CONTENT_API_TIMEOUT,
        )
        if resolved is None:
            raise VideoError(f"短链接解析失败：{failure.describe() if failure else '未知错误'}")
        value = resolved
    params, part = parse_reference(value)
    data = api_data(opener, VIDEO_INFO_API, params, "获取视频信息")
    aid = data.get("aid")
    pages = data.get("pages")
    if type(aid) is not int or aid <= 0 or not isinstance(pages, list):
        raise VideoError("视频缺少有效的 aid 或分 P 信息")
    bvid = str(data.get("bvid") or f"av{aid}")
    title = str(data.get("title") or bvid)
    return VideoInfo(aid, bvid, title, part, pages)


def safe_filename_part(value: str, max_bytes: int = 150) -> str:
    """Return a cross-platform filename component within a UTF-8 byte budget."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip(" .")
    return clean.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").rstrip(" .")
