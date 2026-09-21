"""Comment download orchestration: one pagination loop and atomic JSON output."""

from __future__ import annotations

import contextlib
import json
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Optional

from . import transport, video
from .commentapi import SORT_MODES, CommentClient, CommentError, comment_id
from .config import COMMENT_PAGE_DELAY
from .models import DownloadResult


@dataclass(frozen=True)
class CommentConfig:
    """Resolved settings; thread limits include the root comment."""

    output_dir: Path
    cookie_path: Path
    proxy: str = ""
    limit: Optional[int] = None
    sort: str = "newest"
    progress: Optional[Callable[[int, int, Optional[int]], None]] = None


@contextlib.contextmanager
def _atomic_output(path: Path) -> Iterator[IO[str]]:
    """Clean up even if opening/writing the header fails or the user interrupts."""
    temporary: Optional[Path] = None
    try:
        # A short prefix also fits filesystems with a 255-byte filename limit.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".comments-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            yield stream.file
        temporary.replace(path)
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)


def _dump(value: object, stream: IO[str]) -> None:
    json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _download(url: str, cfg: CommentConfig, root_id: Optional[str]) -> DownloadResult:
    if cfg.limit is not None and (type(cfg.limit) is not int or cfg.limit < 1):
        raise CommentError("评论数量上限必须是正整数")
    if cfg.sort not in SORT_MODES:
        raise CommentError("主评论排序必须是 newest 或 hot")
    if root_id is not None:
        root_id = comment_id(root_id)
    started_at = datetime.now(UTC).isoformat()
    jar, error = transport.load_cookie_jar(cfg.cookie_path)
    if jar is None:
        raise CommentError(error or "无法读取现有 Cookie")
    opener = transport.cookie_opener(jar, proxy=cfg.proxy)
    info = video.resolve(opener, transport.cookie_opener(proxy=cfg.proxy), url)
    client = CommentClient(opener)
    page = (
        client.main(info.aid, cfg.sort, "")
        if root_id is None
        else client.replies(info.aid, root_id, 1)
    )
    suffix = (
        ("comments" if cfg.sort == "newest" else "comments.hot")
        if root_id is None
        else f"comment-{root_id}"
    )
    title = video.safe_filename_part(info.title) or "comments"
    path = cfg.output_dir / f"{title} [{video.safe_filename_part(info.bvid, 20)}].{suffix}.json"
    header: dict[str, Any] = {
        "schema_version": 1,
        "kind": "main_comments" if root_id is None else "comment_thread",
        "video": {"aid": info.aid, "bvid": info.bvid, "title": info.title},
        "request": {"sort": cfg.sort, "limit": cfg.limit}
        if root_id is None
        else {"root_id": root_id, "limit": cfg.limit},
        "started_at": started_at,
    }
    if root_id is not None:
        header["root_comment"] = page.root
    # Only IDs are retained across pages; comment bodies go straight to disk.
    seen: set[str] = set()
    pinned_ids: list[str] = []
    offset_history: set[str] = set()
    root_count = int(root_id is not None)
    pages = 0
    reported_first = page.reported_count
    reason = "end"
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    with _atomic_output(path) as stream:
        # Header serialization belongs inside the file transaction, too.
        stream.write("{\n")
        for key, value in header.items():
            _dump(key, stream)
            stream.write(":")
            _dump(value, stream)
            stream.write(",\n")
        stream.write('"comments":[\n' if root_id is None else '"replies":[\n')
        while True:
            pages += 1
            added = 0
            truncated = False
            for item in page.comments:
                rpid = item["rpid_str"]
                if rpid in seen:
                    continue
                if cfg.limit is not None and len(seen) + root_count >= cfg.limit:
                    truncated = True
                    break
                if seen:
                    stream.write(",\n")
                _dump(item, stream)
                seen.add(rpid)
                if rpid in page.pinned_ids:
                    pinned_ids.append(rpid)
                added += 1
            if cfg.progress is not None:
                cfg.progress(pages, len(seen) + root_count, page.reported_count)
            if truncated:
                reason = "limit"
                break
            if page.end:
                break
            if cfg.limit is not None and len(seen) + root_count >= cfg.limit:
                reason = "limit"
                break
            if added == 0:
                raise CommentError("评论分页没有继续推进，已停止以避免无限请求")
            if root_id is None and cfg.sort == "newest":
                if page.offset in offset_history:
                    raise CommentError("最新评论游标循环，已停止下载")
                offset_history.add(page.offset)
            time.sleep(COMMENT_PAGE_DELAY)
            page = (
                client.main(info.aid, cfg.sort, page.offset)
                if root_id is None
                else client.replies(info.aid, root_id, pages + 1)
            )
        result: dict[str, Any] = {
            "complete": reason == "end",
            "stopped_reason": reason,
            "fetched_count": len(seen) + root_count,
            "pages": pages,
            "finished_at": datetime.now(UTC).isoformat(),
        }
        if root_id is None:
            result.update(
                reported_count=reported_first,
                reported_count_last=page.reported_count,
                pinned_ids=pinned_ids,
            )
        else:
            result.update(
                fetched_reply_count=len(seen),
                reported_reply_count=page.reported_count,
                reported_reply_count_first=reported_first,
            )
        stream.write('\n],\n"result":')
        _dump(result, stream)
        stream.write("\n}\n")
    label = "主评论" if root_id is None else "楼中楼（包含主评论）"
    messages = [("ok", f"[完成!] {label}: {path}（{len(seen) + root_count} 条）")]
    if reason == "limit":
        messages.insert(0, ("info", f"[评论] 已达到数量上限 {cfg.limit}"))
    return DownloadResult(True, messages, path)


def _run(url: str, cfg: CommentConfig, root_id: Optional[str]) -> DownloadResult:
    try:
        return _download(url, cfg, root_id)
    except (CommentError, video.VideoError) as exc:
        return DownloadResult(False, [("error", f"[失败] {exc}")])
    except OSError:
        return DownloadResult(
            False, [("error", "[失败] 无法写入评论文件，请检查空间、权限或文件占用")]
        )
    except ValueError:
        # URL/JSON encoders can include untrusted values in their exceptions.
        return DownloadResult(False, [("error", "[失败] 视频或评论数据格式异常")])


def download_main(url: str, cfg: CommentConfig) -> DownloadResult:
    """Download main comments, without embedded child previews."""
    return _run(url, cfg, None)


def download_replies(url: str, root_id: str, cfg: CommentConfig) -> DownloadResult:
    """Download one root and its children, with a total-record limit."""
    return _run(url, cfg, root_id)
