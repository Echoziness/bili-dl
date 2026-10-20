"""Command-line interface and interactive task menu for bili-dl.

This is the command-line controller / presentation layer. Interactive input
and presentation have a separate controller; both use :mod:`actions`.
Logic modules return result objects with structured messages; this module
translates them into terminal output via :func:`_emit`.

Configuration precedence (highest to lowest, per clig.dev §Configuration):
  1. CLI flags (``--proxy``, ``-a``, etc.)
  2. ``config.toml`` in the config directory (or ``--config`` path)
  3. Environment variables (``HTTP_PROXY``/``HTTPS_PROXY`` for proxy only;
     ``NO_COLOR`` for colour suppression)
  4. Built-in defaults

Modes:
  all — video+audio (DASH, merged to MP4) + extract independent M4A
  v   — video with sound, without extracting a separate audio file
  a   — audio only (M4A, standardised to faststart ISOM)
  s   — first returned subtitle track (SRT, with sentence timestamps)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tomllib
import traceback
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from . import __version__, actions, authqr, cookiestore, downloader, settings, ui
from . import ffmpeg as ff
from .actions import Options as Options
from .actions import read_batch_urls as _read_batch_urls
from .actions import resolve_proxy as _resolve_proxy
from .config import VALID_MODES
from .paths import config_dir, config_file_path, default_video_dir, ensure_dir

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


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Wrap Chinese by terminal cells, preserving example and path indentation."""

    def __init__(self, prog: str) -> None:
        width = max(24, min(96, shutil.get_terminal_size(fallback=(88, 24)).columns))
        super().__init__(prog, width=width, max_help_position=26)

    def _split_lines(self, text: str, width: int) -> list[str]:
        from rich.console import Console
        from rich.text import Text

        return [
            line.plain for line in Text(text).wrap(Console(width=width), width, overflow="fold")
        ]

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        lines: list[str] = []
        for line in text.splitlines():
            if not line:
                lines.append("")
                continue
            prefix = indent + line[: len(line) - len(line.lstrip())]
            lines.extend(
                prefix + part
                for part in self._split_lines(line.lstrip(), max(1, width - len(prefix)))
            )
        return "\n".join(lines)

    def _format_usage(
        self,
        usage: Optional[str],
        actions: Iterable[argparse.Action],
        groups: Iterable[argparse._MutuallyExclusiveGroup],
        prefix: Optional[str],
    ) -> str:
        raw = super()._format_usage(usage, actions, groups, prefix)
        lines: list[str] = []
        for line in raw.rstrip().splitlines():
            if line.startswith("usage: "):
                parts = self._split_lines(line[7:], self._width - 7)
                lines.extend(
                    ("usage: " if i == 0 else "       ") + part for i, part in enumerate(parts)
                )
            else:
                lines.append(self._fill_text(line, self._width, ""))
        return "\n".join(lines) + "\n\n"


def _help_parser(
    prog: str, *, usage: str, description: str, epilog: str
) -> argparse.ArgumentParser:
    """Keep all four help entry points consistent without changing their syntax."""
    return argparse.ArgumentParser(
        prog=prog,
        usage=usage,
        description=description,
        epilog=epilog,
        formatter_class=_HelpFormatter,
        add_help=False,
    )


def _output_help(*, audio: bool = False) -> str:
    """Describe platform defaults without resolving or disclosing personal paths."""
    directories = (
        ("Windows", r"%USERPROFILE%\Videos\bilibili_videos", r"%USERPROFILE%\Music\bilibili_audio"),
        ("macOS", "~/Movies/bilibili_videos", "~/Music/bilibili_audio"),
        ("Linux", "$XDG_DOWNLOAD_DIR/bilibili_videos", "$XDG_DOWNLOAD_DIR/bilibili_audio"),
    )
    lines = ["默认输出目录："]
    for system, video_path, audio_path in directories:
        lines.append(f"  {system:7} {video_path}")
        if audio:
            lines.append(f"          {audio_path}（音频）")
    lines.append("  Linux 未设置 XDG_DOWNLOAD_DIR 时使用 ~/Downloads。")
    return "\n".join(lines)


def _session_help() -> str:
    return r"""配置与会话目录：
  Windows  %APPDATA%\bili-dl
  macOS    ~/Library/Application Support/bili-dl
  Linux    $XDG_CONFIG_HOME/bili-dl（未设置时为 ~/.config/bili-dl）
  配置文件为 config.toml，需自行创建；会话文件按需生成：
  cookies_bilibili.txt、auth_state.json 和 .auth_state.lock。
  --cookie-dir 仅改变会话目录；配置文件另由 --config 指定。

配置优先级：
  命令行 > config.toml > 内置默认值。
  代理未配置时依次读取 HTTPS_PROXY、https_proxy、HTTP_PROXY、http_proxy；
  --proxy "" 明确直连。子命令选项放在子命令之后。

文档：https://github.com/Echoziness/bili-dl#readme
反馈：https://github.com/Echoziness/bili-dl/issues"""


def _add_help_options(p: argparse.ArgumentParser, *, version: bool = False) -> None:
    display = p.add_argument_group("帮助与显示")
    display.add_argument("-h", "--help", action="help", help="显示此帮助并退出")
    if version:
        # -v is already used for video downloads.
        display.add_argument(
            "-V",
            "--version",
            action="version",
            version=f"bili-dl {__version__}",
            help="显示版本并退出",
        )
    display.add_argument(
        "--no-color", action="store_true", help="禁用彩色输出（也支持 NO_COLOR 环境变量）"
    )


def _add_session_options(
    p: argparse.ArgumentParser, *, status: bool = False, media: bool = False
) -> None:
    """Keep login, content and media flags on the same configuration contract."""
    session = p.add_argument_group("登录与配置")
    if status:
        session.add_argument(
            "--status",
            action="store_true",
            help="联网查看登录与续期状态；不执行下载或续期",
        )
    session.add_argument(
        "--cookie-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="指定 Cookie 和登录状态的存储目录",
    )
    session.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="读取指定 TOML 配置文件",
    )
    network = p.add_argument_group("网络")
    network.add_argument(
        "--proxy",
        default=None,
        metavar="URL",
        help='指定代理；--proxy "" 表示直连',
    )
    if media:
        network.add_argument(
            "-k",
            "--insecure",
            action="store_true",
            default=None,
            help="仅跳过 yt-dlp 的 TLS 证书校验；登录、字幕和评论仍校验",
        )


def _build_parser() -> argparse.ArgumentParser:
    p = _help_parser(
        "bili-dl",
        usage="%(prog)s [选项] [URL]\n"
        "       %(prog)s {login,comments,replies} ...\n"
        "       %(prog)s [选项] --batch-file FILE",
        description="""\
B 站视频、音频、字幕和评论下载工具。

快速开始（URL 为视频链接）：
  bili-dl                         打开交互菜单
  bili-dl login                   扫码登录
  bili-dl URL                     视频 + 独立音频
  bili-dl -a URL                  仅音频；-v 仅视频，-s 仅字幕
  bili-dl --batch-file urls.txt    批量下载

其他命令：
  bili-dl comments URL            导出主评论（JSON）
  bili-dl replies URL ROOT_ID     导出一条评论及其楼中楼（JSON）
  bili-dl --status                查看登录与续期状态
  子命令用 bili-dl <命令> --help 查看详情。""",
        epilog=f"""\
使用说明：
  首次下载前先 bili-dl login；也可在会话目录放入 Netscape Cookie .txt。
  音视频需要 yt-dlp；合并、提取音频和容器修复使用 ffmpeg。
  字幕、评论、登录和 --status 无需这两个工具。
  字幕仅取第一条已有轨道，不做语音识别；?p=N 选分 P，默认 P1。

{_output_help(audio=True)}

{_session_help()}""",
    )
    target = p.add_argument_group("下载目标")
    target.add_argument(
        "url",
        nargs="?",
        default=None,
        metavar="URL",
        help="视频链接；省略时打开交互菜单（需终端）",
    )
    mode = p.add_argument_group("保存内容（四选一）")
    modes = mode.add_mutually_exclusive_group()
    modes.add_argument(
        "--all",
        action="store_const",
        const="all",
        dest="mode",
        help="视频 + 独立音频（内置默认；提取需 ffmpeg）",
    )
    modes.add_argument(
        "-v",
        "--video",
        action="store_const",
        const="v",
        dest="mode",
        help="仅视频，保留声音",
    )
    modes.add_argument(
        "-a",
        "--audio",
        action="store_const",
        const="a",
        dest="mode",
        help="仅音频（优先 M4A；有 ffmpeg 时无损修复容器）",
    )
    modes.add_argument(
        "-s",
        "--subtitle",
        action="store_const",
        const="s",
        dest="mode",
        help="仅字幕（带时间戳的 SRT）",
    )
    output = p.add_argument_group("输出与批量")
    output.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="视频 / 字幕目录",
    )
    output.add_argument(
        "--audio-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="音频目录（含 --all 提取的音频）",
    )
    output.add_argument(
        "--batch-file",
        type=Path,
        default=None,
        metavar="FILE",
        help="UTF-8 链接列表，每行一个；忽略空行和 # 开头的行",
    )
    _add_session_options(p, status=True, media=True)
    _add_help_options(p, version=True)
    return p


def _build_login_parser() -> argparse.ArgumentParser:
    """Parser for the deliberately separate, interactive ``bili-dl login`` command."""
    p = _help_parser(
        "bili-dl login",
        usage="%(prog)s [选项]",
        description="""\
用 B 站 App 扫码确认，保存独立的 Web 登录会话。

示例：
  bili-dl login                   显示二维码和确认网址
  bili-dl login --url-only        仅显示确认网址，等待手机确认
  bili-dl login --config my.toml  使用指定配置文件""",
        epilog=f"""登录说明：
  需要交互终端；无需浏览器、yt-dlp 或 ffmpeg。
  手机打开确认网址后由 B 站 App 完成确认；网址约 180 秒内有效。
  --url-only 同样等待确认并保存会话。切换账号请 Ctrl+C 后重新登录。
  成功后用 bili-dl --status 检查状态；下载时按需续期。

{_session_help()}""",
    )
    login = p.add_argument_group("登录方式")
    login.add_argument(
        "--url-only",
        action="store_true",
        help="仅显示确认网址，不绘制二维码",
    )
    _add_session_options(p)
    _add_help_options(p)
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
    content = parser.add_argument_group("导出选项")
    if not thread:
        content.add_argument(
            "--sort",
            choices=("newest", "hot"),
            default="newest",
            help="newest = 最新（默认）；hot = 热门",
        )
    content.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "最多 N 条（含主评论本身）；默认不限，N 为正整数"
            if thread
            else "最多 N 条主评论（含置顶）；默认不限，N 为正整数"
        ),
    )
    content.add_argument(
        "--full",
        action="store_true",
        help="保留原始字段；默认仅保留阅读所需字段",
    )
    output = parser.add_argument_group("输出")
    output.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="JSON 目录（配置项 video_dir）",
    )
    _add_session_options(parser)
    _add_help_options(parser)


def _build_comments_parser() -> argparse.ArgumentParser:
    parser = _help_parser(
        "bili-dl comments",
        usage="%(prog)s [选项] URL",
        description="""\
导出视频主评论为 UTF-8 JSON。

示例：
  bili-dl comments BV1Got26ZE5K
  bili-dl comments BV1Got26ZE5K --sort hot --limit 100
  bili-dl comments BV1Got26ZE5K --output-dir ./comments""",
        epilog=f"""导出说明：
  默认按最新排序，置顶优先；不限量指本次 API 可枚举的全部主评论。
  楼中楼另用 bili-dl replies URL ROOT_ID。--full 也不保留回复预览。
  需要有效登录；无需 yt-dlp / ffmpeg。失败保留旧文件，暂不支持续传。

{_output_help()}

{_session_help()}""",
    )
    target = parser.add_argument_group("评论目标")
    target.add_argument("url", metavar="URL", help="普通视频链接、b23.tv 短链或 BV/AV 号")
    _add_comment_options(parser, thread=False)
    return parser


def _build_replies_parser() -> argparse.ArgumentParser:
    parser = _help_parser(
        "bili-dl replies",
        usage="%(prog)s [选项] URL ROOT_ID",
        description="""\
导出一条主评论及其楼中楼为 UTF-8 JSON。

示例：
  bili-dl replies BV1cSec6tEux 317745878352
  bili-dl replies BV1cSec6tEux 317745878352 --limit 50""",
        epilog=f"""导出说明：
  ROOT_ID 取自 comments 导出 JSON 的 comments 数组中主评论的 rpid_str。
  --limit 50 包含主评论及最多 49 条回复。--full 也不保留回复预览。
  需要有效登录；无需 yt-dlp / ffmpeg。失败保留旧文件，暂不支持续传。

{_output_help()}

{_session_help()}""",
    )
    target = parser.add_argument_group("评论目标")
    target.add_argument("url", metavar="URL", help="普通视频链接、b23.tv 短链或 BV/AV 号")
    target.add_argument(
        "root_id", type=_positive_int, metavar="ROOT_ID", help="主评论 ID（rpid_str），为正整数"
    )
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


def _qr_fits_terminal(image: str) -> bool:
    """Keep every QR row visible, with one column and row to spare."""
    if not sys.stderr.isatty():
        return False
    try:
        terminal = os.get_terminal_size(sys.stderr.fileno())
    except (OSError, ValueError):
        return False
    rows = image.splitlines()
    return bool(rows) and len(rows) < terminal.lines and max(map(len, rows)) < terminal.columns


def _present_login_qr(session: authqr.QrSession, *, url_only: bool = False) -> None:
    ui.info("B 站扫码确认网址（约 180 秒内有效；手机打开后在 B 站 App 确认）：")
    print(session.url, file=sys.stderr)
    if not url_only:
        if not authqr.qrcode_available():
            ui.warn("[登录] 未安装二维码组件，仅显示扫码确认网址；可重新安装 bili-dl")
        else:
            try:
                image = authqr.render_terminal_qr(session.url)
            except Exception:
                ui.warn("[登录] 无法绘制二维码，仅显示扫码确认网址")
            else:
                if _qr_fits_terminal(image):
                    ui.info("也可使用 B 站 App 扫描下方二维码并在手机上确认：")
                    if ui.supports_color():
                        image = "\n".join(f"\x1b[47;30m{row}\x1b[0m" for row in image.splitlines())
                    print(image, file=sys.stderr)
                else:
                    ui.info("[登录] 终端空间不足以完整显示二维码，已显示扫码确认网址")


def _login_command(argv: list[str]) -> int:
    args = _build_login_parser().parse_args(argv)
    if args.no_color:
        ui.disable_color()
    if not sys.stdin.isatty():
        ui.error("[错误] 登录需要交互终端")
        return 1
    cfg = _load_settings(args.config)
    opts = Options(
        cookie_dir=args.cookie_dir or cfg.cookie_dir or config_dir(),
        proxy=_resolve_proxy(args.proxy, cfg.proxy),
    )
    result = actions.login(
        opts,
        present=lambda session: _present_login_qr(session, url_only=args.url_only),
        on_scanned=lambda: ui.info(
            "[登录] B 站已识别本次请求，等待 App 确认；若切换账号，请 Ctrl+C 后重跑 login"
        ),
    )
    _emit(result.messages)
    return 0 if result.success else 1


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

    if thread:
        ui.info(f"楼中楼 | 主评论 ID: {args.root_id} | URL: {args.url}")
    else:
        limit = str(args.limit) if args.limit is not None else "全部"
        ui.info(f"主评论 | 排序: {args.sort} | 上限: {limit} | URL: {args.url}")
    result = actions.download_comments(
        opts,
        args.url,
        root_id=str(args.root_id) if thread else None,
        sort=getattr(args, "sort", "newest"),
        limit=args.limit,
        full=args.full,
        progress=progress,
    )
    _emit(result.messages)
    return 0 if result.success else 1


def _status_command(opts: Options) -> int:
    result = actions.session_status(opts)
    _emit(result.messages)
    return 0 if result.success else 1


def _run_once(opts: Options, url: str, ytdlp: Optional[str], ffmpeg_bin: Optional[str]) -> bool:
    result = actions.download_media(opts, url, ytdlp, ffmpeg_bin)
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


def _interactive(opts: Options, config_path: Optional[Path]) -> int:
    # Keep interactive dependencies out of --help, --status and script startup.
    from .interactive import run

    return run(opts, config_path)


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main_impl(raw_argv)
    except KeyboardInterrupt:
        ui.warn("[取消] 登录已中断" if raw_argv and raw_argv[0] == "login" else "[取消] 下载已中断")
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

    if args.url is None and args.batch_file is None:
        if not sys.stdin.isatty():
            ui.error("[错误] 非交互模式需要提供 URL 或 --batch-file 参数")
            ui.info("用法: bili-dl <URL>  或  bili-dl --batch-file <FILE>")
            return 1
        return _interactive(opts, args.config)

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
