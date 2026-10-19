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
  v   — video with sound, without extracting a separate audio file
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


def _help_parser(
    prog: str, *, usage: str, description: str, epilog: str
) -> argparse.ArgumentParser:
    """Keep all four help entry points consistent without changing their syntax."""
    return argparse.ArgumentParser(
        prog=prog,
        usage=usage,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )


def _output_help(*, audio: bool = False) -> str:
    """Describe platform defaults without resolving or disclosing personal paths."""
    directories = (
        ("Windows", r"%USERPROFILE%\Videos\bilibili_videos", r"%USERPROFILE%\Music\bilibili_audio"),
        ("macOS", "~/Movies/bilibili_videos", "~/Music/bilibili_audio"),
        ("Linux", "$XDG_DOWNLOAD_DIR/bilibili_videos", "$XDG_DOWNLOAD_DIR/bilibili_audio"),
    )
    lines = ["默认输出目录（按运行系统选择；符号路径不展开用户名）："]
    for system, video_path, audio_path in directories:
        lines.append(f"  {system} 视频 / 字幕 / 评论：{video_path}")
        if audio:
            lines.append(f"          音频：{audio_path}")
    lines.append("  Linux 的 XDG_DOWNLOAD_DIR 未设置时使用 ~/Downloads。")
    lines.append("  config.toml 或命令行可覆盖上述目录。")
    return "\n".join(lines)


def _session_help() -> str:
    return r"""配置与会话目录（符号路径不展开用户名）：
  Windows：%APPDATA%\bili-dl
  macOS：~/Library/Application Support/bili-dl
  Linux：$XDG_CONFIG_HOME/bili-dl（未设置时为 ~/.config/bili-dl）
  默认配置文件为上述目录中的 config.toml，需自行创建。
  会话目录按需生成 cookies_bilibili.txt、auth_state.json 和锁文件。
  --config 指定配置文件；--cookie-dir 指定会话目录，不改变配置文件位置。
  命令行参数 > config.toml > 内置默认值；代理还会读取环境变量。
  代理环境变量依次为 HTTPS_PROXY、https_proxy、HTTP_PROXY、http_proxy。
  子命令的选项放在子命令之后，如 bili-dl login --config my.toml。
  首次使用先运行 bili-dl login；也可将 Netscape 格式 Cookie .txt
  放入 Cookie 目录自动导入。下载时不会弹出扫码登录。

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
        help="读取指定 TOML 配置文件（内置路径见下方）",
    )
    network = p.add_argument_group("网络")
    network.add_argument(
        "--proxy",
        default=None,
        metavar="URL",
        help='下载及 B 站 API 的代理；传空字符串 "" 可禁用环境代理',
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
        usage="%(prog)s [选项] URL\n"
        "       %(prog)s [选项] --batch-file FILE\n"
        "       %(prog)s {login,comments,replies} ...\n"
        "       %(prog)s --status\n"
        "       %(prog)s [选项]",
        description="""\
B 站视频、音频、字幕和评论下载工具。

常用示例（将 URL 替换为视频链接）：
  bili-dl login                     用 B 站 App 扫码登录
  bili-dl URL                       保存视频，并额外提取音频（内置默认模式）
  bili-dl -a URL                    只保存音频
  bili-dl -s URL                    只保存字幕（SRT）
  bili-dl --batch-file urls.txt      按当前模式批量下载
  bili-dl --status                  查看登录与续期状态
  bili-dl                           交互输入链接，all/v/a/s 切换，q 退出

评论子命令（JSON 输出）：
  bili-dl comments URL --limit 100   保存最多 100 条主评论
  bili-dl replies URL ROOT_ID        保存一条主评论及其楼中楼
  各子命令支持 --help，例如 bili-dl comments --help。""",
        epilog=f"""\
{_output_help(audio=True)}

使用说明：
  音视频模式需要 yt-dlp；ffmpeg 用于合并视频、提取音频和容器修复。
  字幕、评论、登录和 --status 无需 yt-dlp / ffmpeg。
  字幕只取第一条已有字幕，不做语音识别；链接 ?p=N 指定分 P，默认 P1。
  config.toml 支持 mode（all/v/a/s）、video_dir、audio_dir、cookie_dir、
  proxy、insecure；例如 mode = "a" 可将默认模式改为仅音频。

{_session_help()}""",
    )
    target = p.add_argument_group("下载目标")
    target.add_argument(
        "url",
        nargs="?",
        default=None,
        metavar="URL",
        help="视频链接（字幕也支持 BV/AV 号）；省略时交互输入（需终端）",
    )
    mode = p.add_argument_group("下载模式（四选一；未配置时默认 --all）")
    modes = mode.add_mutually_exclusive_group()
    modes.add_argument(
        "--all",
        action="store_const",
        const="all",
        dest="mode",
        help="保存带声音的视频，并额外提取独立音频（需 ffmpeg）",
    )
    modes.add_argument(
        "-v",
        "--video",
        action="store_const",
        const="v",
        dest="mode",
        help="只保存带声音的视频，不额外提取音频",
    )
    modes.add_argument(
        "-a",
        "--audio",
        action="store_const",
        const="a",
        dest="mode",
        help="只保存音频（优先 M4A），有 ffmpeg 时做无损容器修复",
    )
    modes.add_argument(
        "-s",
        "--subtitle",
        action="store_const",
        const="s",
        dest="mode",
        help="只保存 API 返回的第一条字幕（带时间戳的 SRT）",
    )
    output = p.add_argument_group("输出与批量")
    output.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="视频和字幕目录；音频目录由 --audio-dir 单独指定",
    )
    output.add_argument(
        "--audio-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="下载或提取的音频目录（也适用于 --all）",
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
用 B 站 App 确认，建立独立的 Web 登录会话，供后续下载复用。

示例：
  bili-dl login                         显示二维码和临时确认网址
  bili-dl login --url-only              只显示网址，在手机打开后由 App 确认
  bili-dl login --config my.toml        使用指定配置文件

需要交互终端；无需 yt-dlp / ffmpeg，也不读取浏览器 Profile。
--url-only 仍会等待手机确认并保存登录状态，不会只打印网址就退出。
网址约 180 秒内有效；切换账号时请 Ctrl+C 后重新运行 login。
登录成功后可用 bili-dl --status 检查状态；下载会按需自动续期。""",
        epilog=_session_help(),
    )
    login = p.add_argument_group("登录方式")
    login.add_argument(
        "--url-only",
        action="store_true",
        help="不绘制终端二维码，仅显示手机 App 确认网址（仍需交互终端）",
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
    content = parser.add_argument_group("导出内容")
    content.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "最多保存 N 条，包含主评论本身；默认不限，N 为正整数"
            if thread
            else "最多保存 N 条主评论（含置顶）；默认不限，N 为正整数"
        ),
    )
    content.add_argument(
        "--full",
        action="store_true",
        help="保留原始字段（不含回复预览）；默认精简阅读字段",
    )
    output = parser.add_argument_group("输出")
    output.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="JSON 输出目录；默认用配置 video_dir 或内置视频目录",
    )
    _add_session_options(parser)
    _add_help_options(parser)


def _build_comments_parser() -> argparse.ArgumentParser:
    parser = _help_parser(
        "bili-dl comments",
        usage="%(prog)s [选项] URL",
        description="""\
导出视频主评论为 UTF-8 JSON；楼中楼需另用 replies 子命令。

示例：
  bili-dl comments BV1Got26ZE5K                       导出全部可获取的主评论
  bili-dl comments BV1Got26ZE5K --sort hot --limit 100 热门主评论，最多 100 条
  bili-dl comments BV1Got26ZE5K --output-dir ./comments

默认按最新排序，置顶评论优先且计入上限；“全部”指本次 API 可枚举的内容。
需要有效登录；无需 yt-dlp / ffmpeg。失败或中断时保留旧文件，暂不支持续传。""",
        epilog=f"{_output_help()}\n\n{_session_help()}",
    )
    target = parser.add_argument_group("评论目标")
    target.add_argument("url", metavar="URL", help="普通视频链接、b23.tv 短链或 BV/AV 号")
    order = parser.add_argument_group("排序")
    order.add_argument(
        "--sort",
        choices=("newest", "hot"),
        default="newest",
        help="newest = 最新（默认）；hot = 热门",
    )
    _add_comment_options(parser, thread=False)
    return parser


def _build_replies_parser() -> argparse.ArgumentParser:
    parser = _help_parser(
        "bili-dl replies",
        usage="%(prog)s [选项] URL ROOT_ID",
        description="""\
导出一条主评论及其全部楼中楼为 UTF-8 JSON。

示例：
  bili-dl replies BV1cSec6tEux 317745878352
  bili-dl replies BV1cSec6tEux 317745878352 --limit 50

ROOT_ID 从 comments 导出的 JSON 中 comments 数组的 rpid_str 字段获取，
它是主评论的 ID。--limit 50 表示最多保存主评论及 49 条子回复。
需要有效登录；无需 yt-dlp / ffmpeg。失败或中断时保留旧文件，暂不支持续传。""",
        epilog=f"{_output_help()}\n\n{_session_help()}",
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


def _login_command(argv: list[str]) -> int:
    """Run the minimal opt-in QR-login experiment without checking yt-dlp."""
    args = _build_login_parser().parse_args(argv)
    if args.no_color:
        ui.disable_color()
    if not sys.stdin.isatty():
        ui.error("[错误] 登录需要交互终端")
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
    ui.info("B 站扫码确认网址（约 180 秒内有效；手机打开后在 B 站 App 确认）：")
    print(start.session.url, file=sys.stderr)
    if not args.url_only:
        if not authqr.qrcode_available():
            ui.warn("[登录] 未安装二维码组件，仅显示扫码确认网址；可重新安装 bili-dl")
        else:
            try:
                image = authqr.render_terminal_qr(start.session.url)
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
    result = authqr.poll(
        start.session,
        on_scanned=lambda: ui.info(
            "[登录] B 站已识别本次请求，等待 App 确认；若切换账号，请 Ctrl+C 后重跑 login"
        ),
    )
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
