"""Comment protocol boundary: validated pages and per-task WBI/session state."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar

from . import transport, wbi
from .config import COMMENT_MAIN_API, COMMENT_REPLY_API, COMMENT_RETRY_DELAYS, CONTENT_API_TIMEOUT

SORT_MODES = {"newest": 2, "hot": 3}
T = TypeVar("T")


class CommentError(ValueError):
    """Display-safe failure; never includes response bodies or signed URLs."""


def comment_id(value: object) -> str:
    """Canonical positive decimal ID, including IDs larger than JS integers."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CommentError("评论 ID 必须是正整数")
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", text) or int(text) <= 0:
        raise CommentError("评论 ID 必须是正整数")
    return str(int(text))


def _comment(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CommentError("评论对象格式异常")
    result = dict(value)
    result["rpid_str"] = comment_id(value.get("rpid_str", value.get("rpid")))
    # Embedded previews are not the requested records and bypass --limit.
    result.pop("replies", None)
    return result


def _list(data: dict[str, Any], name: str, *, optional: bool = False) -> list[dict[str, Any]]:
    if name not in data and not optional:
        raise CommentError("评论响应缺少列表")
    value = data.get(name)
    if value is None:
        return []  # Bilibili represents an empty page as null on some endpoints.
    if not isinstance(value, list):
        raise CommentError("评论列表格式异常")
    return [_comment(item) for item in value]


def _count(value: object) -> Optional[int]:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise CommentError("评论统计格式异常")
    return value


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload.get("code")
    if type(code) is not int or code != 0:
        if code == -101:
            raise CommentError("登录已失效，请运行 bili-dl login 后重试")
        if code == -352:
            raise CommentError("请求被 B 站风控拒绝，请稍后重试")
        detail = f"错误码 {code}" if type(code) is int else "异常响应"
        raise CommentError(f"获取评论失败：{detail}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CommentError("评论响应数据格式异常")
    return data


def _retry(
    operation: Callable[[], tuple[Optional[T], Optional[transport.HttpFailure]]],
    *,
    replay_safe: bool = True,
) -> T:
    for attempt in range(len(COMMENT_RETRY_DELAYS) + 1):
        result, failure = operation()
        if result is not None:
            return result
        if not replay_safe:
            raise CommentError("热门分页请求失败，进度可能已推进；请重新运行，避免重试漏页")
        transient = failure is not None and (
            failure.kind == "network"
            or (failure.kind == "http" and failure.status in {429, 500, 502, 503, 504})
        )
        if not transient or attempt == len(COMMENT_RETRY_DELAYS):
            detail = failure.describe() if failure else "未知错误"
            raise CommentError(f"获取评论失败：{detail}")
        time.sleep(COMMENT_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


@dataclass
class CommentPage:
    """One validated page. Counts are advisory, never an end-of-stream signal."""

    comments: list[dict[str, Any]]
    end: bool
    reported_count: Optional[int]
    offset: str = ""
    pinned_ids: set[str] = field(default_factory=set)
    root: Optional[dict[str, Any]] = None


class CommentClient:
    """Own one opener and key pair for the lifetime of a download."""

    def __init__(self, opener: urllib.request.OpenerDirector) -> None:
        self.opener = opener
        self.keys: Optional[tuple[str, str]] = None

    def _fetch(self, url: str) -> tuple[Optional[dict[str, Any]], Optional[transport.HttpFailure]]:
        return transport.fetch_json(
            self.opener, transport.request(url), timeout=CONTENT_API_TIMEOUT
        )

    def main(self, aid: int, sort: str, offset: str) -> CommentPage:
        if self.keys is None:
            self.keys = _retry(lambda: wbi.fetch_keys(self.opener))
        params: dict[str, str | int] = {
            "oid": aid,
            "type": 1,
            "mode": SORT_MODES[sort],
            "plat": 1,
            "web_location": 1315875,
            "pagination_str": json.dumps({"offset": offset}, separators=(",", ":")),
        }

        def request() -> tuple[Optional[dict[str, Any]], Optional[transport.HttpFailure]]:
            assert self.keys is not None
            # Regenerate wts/w_rid on each attempt, rather than replaying a stale URL.
            signed = wbi.sign(params, *self.keys)
            query = urllib.parse.urlencode(signed, quote_via=urllib.parse.quote)
            return self._fetch(f"{COMMENT_MAIN_API}?{query}")

        payload = _retry(request, replay_safe=sort != "hot")
        if payload.get("code") == -403:
            self.keys = _retry(lambda: wbi.fetch_keys(self.opener))
            payload = _retry(request, replay_safe=sort != "hot")
        data = _data(payload)
        cursor = data.get("cursor")
        if not isinstance(cursor, dict) or type(cursor.get("is_end")) is not bool:
            raise CommentError("主评论游标格式异常")
        end = cursor["is_end"]
        pagination = cursor.get("pagination_reply")
        next_offset = pagination.get("next_offset") if isinstance(pagination, dict) else None
        if not end and (not isinstance(next_offset, str) or not next_offset):
            raise CommentError("主评论缺少下一页游标")
        if not end and sort == "newest" and next_offset == offset:
            raise CommentError("最新评论游标没有推进")
        tops = _list(data, "top_replies", optional=True)
        return CommentPage(
            tops + _list(data, "replies"),
            end,
            _count(cursor.get("all_count")),
            offset=next_offset if isinstance(next_offset, str) else "",
            pinned_ids={item["rpid_str"] for item in tops},
        )

    def replies(self, aid: int, root_id: str, number: int) -> CommentPage:
        params = {"oid": aid, "type": 1, "root": root_id, "ps": 20, "pn": number}
        url = f"{COMMENT_REPLY_API}?{urllib.parse.urlencode(params)}"
        data = _data(_retry(lambda: self._fetch(url)))
        page = data.get("page")
        if (
            not isinstance(page, dict)
            or type(page.get("num")) is not int
            or page["num"] != number
            or type(page.get("size")) is not int
            or page["size"] <= 0
        ):
            raise CommentError("楼中楼页码或分页大小异常")
        root = _comment(data.get("root"))
        if root["rpid_str"] != root_id:
            raise CommentError("楼中楼接口未返回对应的主评论")
        replies = _list(data, "replies")
        for reply in replies:
            if reply["rpid_str"] == root_id:
                raise CommentError("楼中楼列表错误地包含主评论")
            owner = reply.get("root_str", reply.get("root"))
            if comment_id(owner) != root_id:
                raise CommentError("楼中楼回复不属于指定主评论")
        # A count or a short page may reflect filtering/live changes. Confirm the
        # end with an empty page instead of claiming completeness from statistics.
        return CommentPage(replies, not replies, _count(page.get("count")), root=root)
