"""Operations shared by command-line and interactive controllers.

No terminal imports: callers own input, presentation and progress callbacks.
Options contain already-resolved configuration, including the proxy value.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import authqr, authrefresh, comments, cookiestore, downloader
from . import ffmpeg as ff
from .models import DownloadConfig, DownloadResult
from .paths import default_audio_dir, default_video_dir, ensure_dir


@dataclass
class Options:
    mode: str = "all"
    proxy: str = ""
    insecure: bool = False
    cookie_dir: Optional[Path] = None
    video_dir: Optional[Path] = None
    audio_dir: Optional[Path] = None


def resolve_proxy(cli_proxy: Optional[str], config_proxy: Optional[str]) -> str:
    """Resolve once; an explicit empty string means direct connection."""
    proxy = cli_proxy if cli_proxy is not None else config_proxy
    if proxy is not None:
        return proxy
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    )


def read_batch_urls(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def download_media(
    opts: Options,
    url: str,
    ytdlp: Optional[tuple[str, ...]] = None,
    ffmpeg_bin: Optional[str] = None,
    *,
    stage: Optional[Callable[[str, Optional[Path]], None]] = None,
    no_color: bool = False,
) -> DownloadResult:
    if opts.mode != "s" and ytdlp is None:
        ytdlp = downloader.find_ytdlp()
        ffmpeg_bin = ff.find_ffmpeg()
    cfg = DownloadConfig(
        mode=opts.mode,
        video_dir=opts.video_dir or default_video_dir(),
        audio_dir=opts.audio_dir or default_audio_dir(),
        cookie_path=cookiestore.bili_cookie_path(opts.cookie_dir),
        proxy=opts.proxy,
        insecure=opts.insecure,
        ytdlp=ytdlp,
        ffmpeg_bin=ffmpeg_bin,
        stage=stage,
        no_color=no_color,
    )
    return downloader.download(url, cfg)


def download_comments(
    opts: Options,
    url: str,
    *,
    root_id: Optional[str] = None,
    sort: str = "newest",
    limit: Optional[int] = None,
    full: bool = False,
    progress: Optional[Callable[[int, int, Optional[int]], None]] = None,
) -> DownloadResult:
    cfg = comments.CommentConfig(
        output_dir=opts.video_dir or default_video_dir(),
        cookie_path=cookiestore.bili_cookie_path(opts.cookie_dir),
        proxy=opts.proxy,
        limit=limit,
        sort=sort,
        full=full,
        progress=progress,
    )
    if root_id is not None:
        return comments.download_replies(url, root_id, cfg)
    return comments.download_main(url, cfg)


def login(
    opts: Options,
    *,
    present: Callable[[authqr.QrSession], None],
    on_scanned: Callable[[], None],
) -> DownloadResult:
    """Explicit login only; both controllers share the storage transaction."""
    from .paths import config_dir

    try:
        ensure_dir(opts.cookie_dir or config_dir())
    except OSError as exc:
        return DownloadResult(False, [("error", f"[错误] 无法创建 Cookie 目录: {exc}")])
    start = authqr.start(proxy=opts.proxy)
    if start.session is None:
        return DownloadResult(False, start.messages)
    present(start.session)
    polled = authqr.poll(start.session, on_scanned=on_scanned)
    messages = [*start.messages, *polled.messages]
    if not polled.success:
        return DownloadResult(False, messages)
    stored = cookiestore.store_qr_session(
        polled.cookie_lines, polled.refresh_token, opts.cookie_dir, proxy=opts.proxy
    )
    return DownloadResult(stored.success, [*messages, *stored.messages])


def session_status(opts: Options) -> DownloadResult:
    """Read-only status: never renew a session or request a QR code."""
    result = cookiestore.validate(opts.cookie_dir, proxy=opts.proxy)
    state, state_error = cookiestore.renewal_state(opts.cookie_dir)
    messages: list[tuple[str, str]] = []
    if result.valid and result.uname is not None:
        messages.append(("ok", f"[状态] 已登录: {result.uname}"))
    elif result.valid:
        messages.append(("warn", "[状态] 登录状态: 本地格式可用，当前无法在线确认"))
    else:
        messages.extend(result.messages)
        messages.append(("error", "[状态] 下载 Cookie: 不可用"))
    if result.valid:
        required, error = authrefresh.check_requirement(
            cookiestore.bili_cookie_path(opts.cookie_dir), proxy=opts.proxy
        )
        if required is True:
            detail = "下次下载会自动处理" if state is not None else "当前会话无法自动处理"
            messages.append(("warn", f"[状态] B 站当前要求续期: 是（{detail}）"))
        elif required is False:
            messages.append(("ok", "[状态] B 站当前要求续期: 否"))
        else:
            messages.append(("warn", f"[状态] B 站续期状态: 暂时无法确认（{error or '未知错误'}）"))
    if state_error:
        messages.append(("warn", f"[状态] 自动续期: 不可用（{state_error}）"))
    elif state is None:
        messages.append(("warn", "[状态] 自动续期: 未启用（运行 bili-dl login 可启用）"))
    elif state.pending_confirm_token is not None:
        messages.append(("warn", "[状态] 自动续期: 已启用（下次下载将完成上次续期确认）"))
    else:
        messages.append(("ok", "[状态] 自动续期: 已启用"))
    return DownloadResult(result.valid, messages)
