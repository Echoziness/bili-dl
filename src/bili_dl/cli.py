"""Command-line interface and interactive REPL for bili-dl.

This is the **controller / presentation layer** — the only module that calls
``ui.*``. All logic modules (cookiesource, cookiestore, ffmpeg, downloader,
settings) return result objects with structured messages; this module
translates them into terminal output via :func:`_emit`.

Configuration precedence (highest to lowest, per clig.dev §Configuration):
  1. CLI flags (``--proxy``, ``-a``, etc.)
  2. ``config.toml`` in the config directory (or ``--config`` path)
  3. Environment variables (``HTTP_PROXY``/``HTTPS_PROXY`` for proxy only;
     ``NO_COLOR`` for colour suppression)
  4. Built-in defaults

Modes:
  all — video+audio (DASH, merged to MP4) + extract independent M4A
  v   — video only (single-file MP4 when such a stream exists)
  a   — audio only (M4A, standardised to faststart ISOM)
  s   — first returned subtitle track (SRT, with sentence timestamps)
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__, authqr, authrefresh, comments, cookiestore, downloader, settings, ui
from . import ffmpeg as ff
from .config import VALID_MODES
from .downloader import DownloadConfig
from .paths import config_dir, config_file_path, default_audio_dir, default_video_dir, ensure_dir

# Terminal encoding follows the host. Machine-readable yt-dlp output and
# subtitle files explicitly use UTF-8 within their respective backends.

# ─── Presentation: map logic-module message levels to ui functions ─────────
_EMITTERS = {
    "info": ui.info,
    "ok": ui.ok,
    "warn": ui.warn,
    "error": ui.error,
}


def _emit(messages: list[tuple[str, str]]) -> None:
    """Print structured messages from logic modules via the appropriate ui function."""
    for level, text in messages:
        _EMITTERS[level](text)


@dataclass
class Options:
    mode: str = "all"
    proxy: str = ""
    insecure: bool = False
    cookie_dir: Optional[Path] = None
    video_dir: Optional[Path] = None
    audio_dir: Optional[Path] = None


_HELP_EPILOG = """\
examples:
  bili-dl https://www.bilibili.com/video/BV...     # video + audio (default)
  bili-dl -a https://www.bilibili.com/video/BV...  # audio only (M4A)
  bili-dl -s https://www.bilibili.com/video/BV...  # first subtitle track (SRT)
  bili-dl --batch-file urls.txt                    # batch download
  bili-dl --status                                 # inspect login and renewal state
  bili-dl login                                    # QR login
  bili-dl comments URL [--limit N]                 # main comments (all by default)
  bili-dl replies URL ROOT_ID [--limit N]          # one thread, including its root
  bili-dl                                          # interactive REPL

report issues: https://github.com/Echoziness/bili-dl/issues\
"""


def _add_session_options(p: argparse.ArgumentParser) -> None:
    """Keep login, content and media flags on the same configuration contract."""
    p.add_argument(
        "--cookie-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=f"override cookie directory (default: {config_dir()})",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help=f"override config file path (default: {config_file_path()})",
    )
    p.add_argument(
        "--proxy",
        default=None,
        metavar="URL",
        help="proxy URL for downloads and Bilibili APIs (env: HTTP_PROXY, HTTPS_PROXY)",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="disable colored output (also disabled by NO_COLOR env var or non-TTY)",
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bili-dl",
        description="Cross-platform Bilibili video, audio, subtitle and comment downloader.",
        epilog=_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_const",
        const="all",
        dest="mode",
        help="download video + audio (default)",
    )
    mode.add_argument(
        "-v", "--video", action="store_const", const="v", dest="mode", help="download video only"
    )
    mode.add_argument(
        "-a",
        "--audio",
        action="store_const",
        const="a",
        dest="mode",
        help="download audio only (M4A)",
    )
    mode.add_argument(
        "-s",
        "--subtitle",
        action="store_const",
        const="s",
        dest="mode",
        help="download the first returned subtitle track only (SRT with timestamps)",
    )
    # NOTE: -v is taken by --video; expose --version via -V. Both yt-dlp and
    # curl use the same convention (-V / --version), so users won't be surprised.
    p.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"bili-dl {__version__}",
        help="show version and exit",
    )
    p.add_argument(
        "-k",
        "--insecure",
        action="store_true",
        default=None,
        help="skip yt-dlp TLS verification; authentication APIs stay verified",
    )
    _add_session_options(p)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="override video/subtitle output directory",
    )
    p.add_argument(
        "--audio-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="override audio output directory",
    )
    p.add_argument(
        "--batch-file",
        type=Path,
        default=None,
        metavar="FILE",
        help="download URLs listed in a text file (one URL per line, # comments)",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="show Cookie and automatic-renewal status without downloading",
    )
    p.add_argument("url", nargs="?", default=None, help="Bilibili video URL")
    return p


def _build_login_parser() -> argparse.ArgumentParser:
    """Parser for the deliberately separate, interactive ``bili-dl login`` command."""
    p = argparse.ArgumentParser(
        prog="bili-dl login",
        description="Use the Bilibili App to create a standalone QR-login session.",
    )
    _add_session_options(p)
    return p


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _add_comment_options(parser: argparse.ArgumentParser, *, thread: bool) -> None:
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "maximum objects to save, including the root; default: all"
            if thread
            else "maximum main comments to save; default: all"
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="save raw Bilibili comment objects (default: lean reading fields, ~5% of the size)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="override comment output directory (default: video directory)",
    )
    _add_session_options(parser)


def _build_comments_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bili-dl comments",
        description="Download main comments as UTF-8 JSON (newest order by default).",
    )
    parser.add_argument("url", help="Bilibili video URL, BV number or av number")
    parser.add_argument(
        "--sort",
        choices=("newest", "hot"),
        default="newest",
        help="comment order; hot pagination is session-bound (default: newest)",
    )
    _add_comment_options(parser, thread=False)
    return parser


def _build_replies_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bili-dl replies",
        description="Download one root comment and all child replies as UTF-8 JSON.",
    )
    parser.add_argument("url", help="Bilibili video URL, BV number or av number")
    parser.add_argument("root_id", type=_positive_int, help="root comment ID from main comments")
    _add_comment_options(parser, thread=True)
    return parser


def _load_settings(config_path: Optional[Path]) -> settings.Settings:
    """Load config.toml, returning empty Settings on missing file."""
    path = config_path or config_file_path()
    try:
        return settings.load(path)
    except tomllib.TOMLDecodeError as e:
        ui.warn(f"[警告] 配置文件 {path} 解析失败: {e}")
        ui.warn("[警告] 忽略配置文件，使用命令行参数和默认值")
        return settings.Settings()


def _resolve_proxy(cli_proxy: Optional[str], config_proxy: Optional[str]) -> str:
    """Resolve the single proxy value shared by downloads and Bilibili APIs."""
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


def _merge_settings(args: argparse.Namespace, cfg: settings.Settings) -> Options:
    """Merge config + env vars with CLI args.

    Precedence: CLI flags > config file > env vars > defaults.
    (clig.dev §Configuration)
    """
    mode = args.mode or cfg.mode or "all"

    return Options(
        mode=mode if mode in VALID_MODES else "all",
        proxy=_resolve_proxy(args.proxy, cfg.proxy),
        insecure=args.insecure if args.insecure is not None else (cfg.insecure or False),
        cookie_dir=args.cookie_dir or cfg.cookie_dir,
        video_dir=args.output_dir or cfg.video_dir,
        audio_dir=args.audio_dir or cfg.audio_dir,
    )


def _read_batch_urls(path: Path) -> list[str]:
    """Read URLs from a batch file, skipping blank lines and # comments."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    urls = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls


def _prepare_cookie(opts: Options) -> bool:
    """Ensure a valid Bilibili cookie is available.

    Delegates the validate → import → re-validate flow to
    :func:`cookiestore.ensure_cookie` (one call, no internal coordination
    leaking into the controller). On failure, prints a help block.
    """
    result = cookiestore.ensure_cookie(opts.cookie_dir, proxy=opts.proxy)
    _emit(result.messages)
    if result.ready:
        return True

    base_dir = opts.cookie_dir or config_dir()
    print(file=sys.stderr)
    ui.error("[失败] 没有可用的 B 站 Cookie。")
    ui.warn("  可运行 bili-dl login，用 B 站 App 扫码建立独立登录会话；或")
    ui.warn("  请先在浏览器登录 bilibili.com，然后导出 Cookie（Netscape 格式），")
    ui.warn(f"  将导出的 .txt 文件放入以下目录：{base_dir}")
    ui.warn("  支持任意文件名，只要文件包含 bilibili 条目即可自动识别。")
    print(file=sys.stderr)
    return False


def _login_command(argv: list[str]) -> int:
    """Run the minimal opt-in QR-login experiment without checking yt-dlp."""
    args = _build_login_parser().parse_args(argv)
    if args.no_color:
        ui.disable_color()
    if not sys.stdin.isatty():
        ui.error("[错误] 扫码登录需要交互终端")
        return 1
    if not authqr.qrcode_available():
        ui.error("[错误] 未安装扫码登录组件")
        ui.info("请重新安装: pip install -U bili-dl")
        return 1

    cfg = _load_settings(args.config)
    cookie_dir = args.cookie_dir or cfg.cookie_dir or config_dir()
    proxy = _resolve_proxy(args.proxy, cfg.proxy)
    try:
        ensure_dir(cookie_dir)
    except OSError as exc:
        ui.error(f"[错误] 无法创建 Cookie 目录: {exc}")
        return 1

    start = authqr.start(proxy=proxy)
    _emit(start.messages)
    if start.session is None:
        return 1
    try:
        image = authqr.render_terminal_qr(start.session.url)
    except Exception as exc:
        ui.error(f"[错误] 无法渲染二维码: {exc}")
        return 1

    ui.info("请使用 B 站 App 扫描二维码并在手机上确认（最长 180 秒）")
    print(file=sys.stderr)
    print(image, file=sys.stderr)
    print(file=sys.stderr)
    result = authqr.poll(start.session)
    _emit(result.messages)
    if not result.success:
        return 1
    stored = cookiestore.store_qr_session(
        result.cookie_lines, result.refresh_token, cookie_dir, proxy=proxy
    )
    _emit(stored.messages)
    return 0 if stored.success else 1


def _comment_command(argv: list[str], *, thread: bool) -> int:
    """Run one non-interactive main-comment or thread download."""
    parser = _build_replies_parser() if thread else _build_comments_parser()
    args = parser.parse_args(argv)
    if args.no_color:
        ui.disable_color()
    cfg = _load_settings(args.config)
    cookie_dir = args.cookie_dir or cfg.cookie_dir
    output_dir = args.output_dir or cfg.video_dir or default_video_dir()
    proxy = _resolve_proxy(args.proxy, cfg.proxy)
    try:
        ensure_dir(cookie_dir or config_dir())
        ensure_dir(output_dir)
    except OSError as exc:
        ui.error(f"[错误] 无法创建评论目录: {exc}")
        return 1
    opts = Options(proxy=proxy, cookie_dir=cookie_dir, video_dir=output_dir)
    if not _prepare_cookie(opts):
        return 1

    def progress(pages: int, fetched: int, reported: Optional[int]) -> None:
        if pages == 1 or pages % 25 == 0:
            ui.info(f"[评论] 已抓取 {fetched} 条 | {pages} 页")

    comment_cfg = comments.CommentConfig(
        output_dir=output_dir,
        cookie_path=cookiestore.bili_cookie_path(cookie_dir),
        proxy=proxy,
        limit=args.limit,
        sort=getattr(args, "sort", "newest"),
        full=args.full,
        progress=progress,
    )
    if thread:
        ui.info(f"楼中楼 | 主评论 ID: {args.root_id} | URL: {args.url}")
        result = comments.download_replies(args.url, str(args.root_id), comment_cfg)
    else:
        limit = str(args.limit) if args.limit is not None else "全部"
        ui.info(f"主评论 | 排序: {args.sort} | 上限: {limit} | URL: {args.url}")
        result = comments.download_main(args.url, comment_cfg)
    _emit(result.messages)
    return 0 if result.success else 1


def _status_command(opts: Options) -> int:
    """Report meaningful session facts without downloading, refreshing, or prompting."""
    result = cookiestore.validate(opts.cookie_dir, proxy=opts.proxy)
    state, state_error = cookiestore.renewal_state(opts.cookie_dir)
    if result.valid and result.uname is not None:
        ui.ok(f"[状态] 已登录: {result.uname}")
    elif result.valid:
        ui.warn("[状态] 登录状态: 本地格式可用，当前无法在线确认")
    else:
        _emit(result.messages)
        ui.error("[状态] 下载 Cookie: 不可用")

    if result.valid:
        required, error = authrefresh.check_requirement(
            cookiestore.bili_cookie_path(opts.cookie_dir), proxy=opts.proxy
        )
        if required is True:
            if state is not None:
                ui.warn("[状态] B 站当前要求续期: 是（下次下载会自动处理）")
            else:
                ui.warn("[状态] B 站当前要求续期: 是（当前会话无法自动处理）")
        elif required is False:
            ui.ok("[状态] B 站当前要求续期: 否")
        else:
            ui.warn(f"[状态] B 站续期状态: 暂时无法确认（{error or '未知错误'}）")

    if state_error:
        ui.warn(f"[状态] 自动续期: 不可用（{state_error}）")
    elif state is None:
        ui.warn("[状态] 自动续期: 未启用（运行 bili-dl login 可启用）")
    elif state.pending_confirm_token is not None:
        ui.warn("[状态] 自动续期: 已启用（下次下载将完成上次续期确认）")
    else:
        ui.ok("[状态] 自动续期: 已启用")
    return 0 if result.valid else 1


def _run_once(opts: Options, url: str, ytdlp: Optional[str], ffmpeg_bin: Optional[str]) -> bool:
    """Execute one download and emit results. Returns success."""
    # A REPL started in subtitle mode can later switch to a media mode.
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
    )
    result = downloader.download(url, cfg)
    _emit(result.messages)
    return result.success


def _batch_download(
    opts: Options, urls: list[str], ytdlp: Optional[str], ffmpeg_bin: Optional[str]
) -> int:
    """Download multiple URLs sequentially. Returns 0 if all succeed, 1 if any fail."""
    total = len(urls)
    ui.info(f"[批量] 共 {total} 个链接")
    failures = 0
    for i, url in enumerate(urls, 1):
        print(file=sys.stderr)
        ui.info(f"[批量] ({i}/{total}) {url}")
        if not _run_once(opts, url, ytdlp, ffmpeg_bin):
            failures += 1
    print(file=sys.stderr)
    if failures:
        ui.warn(f"[批量] 完成: {total - failures} 成功, {failures} 失败")
    else:
        ui.ok(f"[批量] 全部完成: {total} 个链接")
    return 1 if failures else 0


def _repl(opts: Options, ytdlp: Optional[str], ffmpeg_bin: Optional[str]) -> int:
    if ytdlp:
        print(f"使用: {ytdlp}", file=sys.stderr)
    print(file=sys.stderr)
    while True:
        label = ui.mode_label(opts.mode)
        try:
            raw = ui.prompt(
                f"输入 B 站链接 (模式:{label} | {'/'.join(VALID_MODES)} 切换 | q 退出): "
            )
        except EOFError:
            break
        if not raw.strip():
            continue
        if raw.strip().lower() == "q":
            break
        if raw.strip().lower() in VALID_MODES:
            opts.mode = raw.strip().lower()
            print(file=sys.stderr)
            ui.info(f"[模式] {ui.mode_label(opts.mode)}")
            print(file=sys.stderr)
            continue
        print(file=sys.stderr)
        _run_once(opts, raw.strip(), ytdlp, ffmpeg_bin)
        print(file=sys.stderr)
    print(file=sys.stderr)
    ui.info("再见!")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return _main_impl(argv)
    except KeyboardInterrupt:
        ui.warn("[取消] 下载已中断")
        return 130
    except Exception:
        ui.error("[错误] 发生未预期错误，堆栈如下:")
        traceback.print_exc(file=sys.stderr)
        ui.info(f"环境: bili-dl {__version__} | Python {sys.version.split()[0]} | {sys.platform}")
        ui.info("请将以上完整堆栈提交到 https://github.com/Echoziness/bili-dl/issues")
        return 1


def _main_impl(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "login":
        return _login_command(raw_argv[1:])
    if raw_argv and raw_argv[0] == "comments":
        return _comment_command(raw_argv[1:], thread=False)
    if raw_argv and raw_argv[0] == "replies":
        return _comment_command(raw_argv[1:], thread=True)

    parser = _build_parser()
    args = parser.parse_args(raw_argv)

    # Handle --no-color before anything else (clig.dev §Output) --------------
    if args.no_color:
        ui.disable_color()

    # Load config file, merge with CLI args + env vars (CLI > config > env) --
    cfg = _load_settings(args.config)
    opts = _merge_settings(args, cfg)

    if args.status:
        if args.url or args.batch_file:
            parser.error("--status cannot be combined with a URL or --batch-file")
        return _status_command(opts)

    # Dependency checks (same severity ladder as bd.ps1) ---------------------
    ytdlp = downloader.find_ytdlp() if opts.mode != "s" else None
    if opts.mode != "s" and not ytdlp:
        ui.error("[错误] 未找到 yt-dlp，请先安装 (pip install -U yt-dlp 或 winget install yt-dlp)")
        return 1
    ffmpeg_bin = ff.find_ffmpeg() if opts.mode != "s" else None
    if opts.mode != "s" and not ffmpeg_bin:
        ui.warn("[警告] 未找到 ffmpeg，将跳过音频提取与容器修复")

    try:
        ensure_dir(opts.cookie_dir or config_dir())
    except OSError as e:
        ui.error(f"[错误] 无法创建 Cookie 目录: {e}")
        ui.info("请检查路径权限或使用 --cookie-dir 指定其他目录")
        return 1

    ui.info("B 站视频 / 音频 / 字幕下载工具")
    print(file=sys.stderr)

    if not _prepare_cookie(opts):
        return 1

    # Batch mode: read URLs from file, download all --------------------------
    if args.batch_file:
        if not args.batch_file.exists():
            ui.error(f"[错误] 批量文件不存在: {args.batch_file}")
            return 1
        try:
            urls = _read_batch_urls(args.batch_file)
        except OSError as e:
            ui.error(f"[错误] 无法读取批量文件: {e}")
            return 1
        if not urls:
            ui.warn("[警告] 批量文件中没有有效 URL")
            return 0
        if ytdlp:
            ui.info(f"使用: {ytdlp}")
        ui.info(f"模式: {ui.mode_label(opts.mode)} | 批量文件: {args.batch_file}")
        print(file=sys.stderr)
        return _batch_download(opts, urls, ytdlp, ffmpeg_bin)

    # Non-interactive mode: one URL then exit --------------------------------
    if args.url:
        if ytdlp:
            ui.info(f"使用: {ytdlp}")
        ui.info(f"模式: {ui.mode_label(opts.mode)} | URL: {args.url}")
        print(file=sys.stderr)
        ok = _run_once(opts, args.url, ytdlp, ffmpeg_bin)
        print(file=sys.stderr)
        ui.info("再见!")
        return 0 if ok else 1

    # No URL and no batch file: REPL only if stdin is a TTY ------------------
    # (clig.dev §Interactivity: "Only use prompts if stdin is a TTY")
    if not sys.stdin.isatty():
        ui.error("[错误] 非交互模式需要提供 URL 或 --batch-file 参数")
        ui.info("用法: bili-dl <URL>  或  bili-dl --batch-file <FILE>")
        return 1

    return _repl(opts, ytdlp, ffmpeg_bin)


if __name__ == "__main__":
    raise SystemExit(main())
