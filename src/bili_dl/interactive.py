"""Task-oriented interactive controller. All input/rendering lives in terminal."""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from . import actions, cookiestore, downloader, settings, video
from . import ffmpeg as ff
from .actions import Options
from .paths import config_dir, config_file_path, default_audio_dir, default_video_dir, ensure_dir
from .terminal import Back, Terminal, display_path

MODES = [
    ("v", "视频（保留声音）"),
    ("a", "仅音频"),
    ("all", "视频 + 独立音频"),
    ("s", "字幕（SRT）"),
]
HOME = [
    ("download", "下载视频 / 音频 / 字幕"),
    ("comments", "导出视频主评论"),
    ("replies", "导出一条评论的楼中楼"),
    ("batch", "批量下载"),
    ("account", "登录与账号状态"),
    ("settings", "目录与高级设置"),
    ("help", "使用帮助"),
    ("exit", "退出"),
]
TASK_LABELS = {
    "download": "下载内容",
    "comments": "导出主评论",
    "replies": "导出楼中楼",
    "batch": "批量下载",
}


def input_path(value: str) -> Path:
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return Path(os.path.expandvars(value)).expanduser().absolute()


def reference(value: str) -> str:
    """Accept a reference or a pasted share message; retain the selected part."""
    match = re.search(r"https?://[^\s<>]+", value)
    if match:
        value = match[0].rstrip("，。；！）】),.;!]")
    if "://" not in value:
        video.parse_reference(value)
        return f"https://www.bilibili.com/video/{value}"
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("请粘贴不含账号密码的视频链接。")
    if (
        parsed.hostname in {"b23.tv", "www.b23.tv"}
        and parsed.scheme in {"http", "https"}
        and parsed.path.strip("/")
    ):
        return value
    video.parse_reference(value)
    return value


def valid_reference(value: str) -> bool | str:
    try:
        reference(value)
    except ValueError:
        return "请粘贴 B 站普通视频链接、b23.tv 短链或完整 BV / AV 号。"
    return True


def positive(value: str) -> bool | str:
    return (
        True if value.isascii() and value.isdigit() and int(value) > 0 else "请输入大于 0 的整数。"
    )


def proxy_label(value: str) -> str:
    if not value:
        return "直连"
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        if not host:
            return "已设置"
        return f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "")
    except ValueError:
        return "已设置"


@dataclass
class Task:
    kind: str
    opts: Options
    urls: list[str]
    root_id: Optional[str] = None
    sort: str = "newest"
    limit: Optional[int] = None
    full: bool = False


class Interactive:
    def __init__(self, opts: Options, config_path: Optional[Path] = None) -> None:
        self.opts = replace(opts)
        self.config_path = config_path or config_file_path()
        self.terminal = Terminal()

    def run(self) -> int:
        terminal = self.terminal
        notice = ""
        while True:
            try:
                terminal.home()
                if notice:
                    terminal.note(notice)
                    notice = ""
                choice = terminal.select("今天想保存什么？", HOME)
            except (Back, KeyboardInterrupt, EOFError):
                break
            if choice == "exit":
                break
            try:
                if choice in {"download", "comments", "replies", "batch"}:
                    self.task_form(choice)
                elif choice == "account":
                    self.account()
                elif choice == "settings":
                    self.configure(self.opts)
                else:
                    self.help()
            except (Back, KeyboardInterrupt):
                terminal.stop()
                notice = "已取消本次操作，返回首页。"
            except EOFError:
                break
            except (OSError, ValueError) as exc:
                terminal.page("操作未完成")
                terminal.note(f"操作未完成：{exc}", "red")
                try:
                    terminal.pause()
                except (Back, KeyboardInterrupt):
                    pass
                except EOFError:
                    break
        terminal.page("已退出")
        terminal.note("已退出。")
        return 0

    def task_form(self, kind: str) -> None:
        terminal = self.terminal
        while True:
            terminal.page(f"首页 / {TASK_LABELS[kind]}")
            task = Task(kind, replace(self.opts), [])
            if kind == "batch":
                path = input_path(terminal.text("批量文件路径（UTF-8，每行一个链接）", path=True))
                urls = actions.read_batch_urls(path)
                if not urls:
                    terminal.note("文件中没有链接；空行和 # 注释会被跳过。", "yellow")
                    terminal.pause()
                    return
                for index, url in enumerate(urls, 1):
                    try:
                        task.urls.append(reference(url))
                    except ValueError:
                        raise ValueError(
                            f"批量文件中的第 {index} 个链接无法识别，请修改后重试。"
                        ) from None
            else:
                task.urls = [
                    reference(terminal.text("粘贴视频链接或 BV / AV 号", validate=valid_reference))
                ]
            if kind in {"download", "batch"}:
                task.opts.mode = terminal.select("保存什么？", MODES, default=self.opts.mode)
            else:
                if kind == "replies":
                    terminal.note("主评论 ID 来自主评论 JSON 的 rpid_str 字段。")
                    task.root_id = terminal.text("主评论 ID", validate=positive)
                else:
                    task.sort = terminal.select(
                        "按什么顺序获取？", [("newest", "最新"), ("hot", "热门")]
                    )
                amount = terminal.select(
                    "保存多少条？", [("all", "全部可获取的评论"), ("limit", "指定数量上限")]
                )
                if amount == "limit":
                    terminal.note(
                        "楼中楼的数量包含主评论本身。"
                        if kind == "replies"
                        else "数量包含置顶主评论。"
                    )
                    task.limit = int(terminal.text("数量上限", default="100", validate=positive))
                task.full = (
                    terminal.select(
                        "保存哪些字段？",
                        [
                            ("lean", "阅读所需字段（推荐，体积更小）"),
                            ("full", "完整字段（用于研究或处理数据）"),
                        ],
                    )
                    == "full"
                )
            if not self.review(task):
                return
            if not self.execute(task):
                return

    def review(self, task: Task) -> bool:
        terminal = self.terminal
        while True:
            terminal.page(f"首页 / {TASK_LABELS[task.kind]} / 确认任务")
            rows = [("目标", task.urls[0] if len(task.urls) == 1 else f"{len(task.urls)} 个视频")]
            if task.kind in {"download", "batch"}:
                rows.append(("内容", dict(MODES)[task.opts.mode]))
            else:
                rows.extend(
                    [
                        (
                            "范围",
                            f"主评论 {task.root_id} 及其回复"
                            if task.root_id
                            else f"主评论 · {'最新' if task.sort == 'newest' else '热门'}",
                        ),
                        ("上限", str(task.limit) if task.limit is not None else "全部可获取"),
                        ("字段", "完整" if task.full else "阅读所需"),
                    ]
                )
            if task.kind in {"comments", "replies"} or task.opts.mode != "a":
                rows.append(("保存目录", display_path(task.opts.video_dir or default_video_dir())))
            if task.kind in {"download", "batch"} and task.opts.mode in {"a", "all"}:
                rows.append(("音频目录", display_path(task.opts.audio_dir or default_audio_dir())))
            rows.append(("网络", proxy_label(task.opts.proxy)))
            terminal.card(
                "准备就绪", rows, subtitle="同名产物按现有规则替换；本次设置不会自动写入配置"
            )
            choice = terminal.select(
                "下一步", [("start", "开始"), ("settings", "调整本次设置"), ("back", "返回首页")]
            )
            if choice != "settings":
                return choice == "start"
            self.configure(task.opts, export=False)

    def ready(self, opts: Options) -> bool:
        terminal = self.terminal
        while True:
            ensure_dir(opts.cookie_dir or config_dir())
            with terminal.busy("正在检查登录会话…"):
                result = cookiestore.ensure_cookie(opts.cookie_dir, proxy=opts.proxy)
            terminal.messages(
                [(level, text) for level, text in result.messages if level in {"warn", "error"}]
            )
            if result.ready:
                return True
            terminal.page("登录状态 / 需要登录")
            terminal.messages(result.messages)
            terminal.card(
                "需要登录", [("说明", "当前没有可用的登录会话。扫码登录后可继续本次任务。")]
            )
            choice = terminal.select(
                "如何继续？",
                [
                    ("login", "扫码登录"),
                    ("cookie", "查看 Cookie 导入方法"),
                    ("settings", "调整会话目录或代理"),
                    ("back", "返回首页"),
                ],
            )
            if choice == "back":
                return False
            if choice == "login":
                if not self.login(opts):
                    return False
            elif choice == "cookie":
                self.cookie_help(opts)
                return False
            else:
                self.configure(opts, export=False)

    def execute(self, task: Task) -> bool:
        terminal = self.terminal
        while True:
            terminal.page(f"首页 / {TASK_LABELS[task.kind]} / 执行任务")
            media = task.kind in {"download", "batch"} and task.opts.mode != "s"
            if media and downloader.find_ytdlp() is None:
                terminal.card(
                    "缺少下载工具",
                    [
                        ("需要", "yt-dlp"),
                        ("安装", "uv tool install yt-dlp 或 pipx install yt-dlp"),
                        ("可用功能", "字幕、评论和登录不需要此工具。"),
                    ],
                )
                terminal.pause()
                return False
            if media and ff.find_ffmpeg() is None:
                terminal.note("未找到 ffmpeg：合并、独立音频提取与容器修复可能不可用。", "yellow")
                if not terminal.confirm("仍要尝试下载？"):
                    return False
            if not self.ready(task.opts):
                return False
            failures = 0
            failed_urls = []
            for index, url in enumerate(task.urls, 1):
                suffix = f" {index} / {len(task.urls)}" if len(task.urls) > 1 else ""
                terminal.page(f"首页 / {TASK_LABELS[task.kind]} / 执行任务{suffix}")
                if len(task.urls) > 1:
                    terminal.card(f"批量任务  {index} / {len(task.urls)}", [("目标", url)])
                try:
                    if task.kind in {"download", "batch"}:
                        if task.opts.mode == "s":
                            terminal.stage("subtitle")
                        result = actions.download_media(
                            task.opts, url, stage=terminal.stage, no_color=not terminal.color
                        )
                    else:
                        with terminal.busy("正在获取评论…"):
                            result = actions.download_comments(
                                task.opts,
                                url,
                                root_id=task.root_id,
                                sort=task.sort,
                                limit=task.limit,
                                full=task.full,
                                progress=self.comment_progress,
                            )
                finally:
                    terminal.stop()
                terminal.result(result, page=f"首页 / {TASK_LABELS[task.kind]} / 任务结果")
                failures += int(not result.success)
                if not result.success:
                    failed_urls.append(url)
            if len(task.urls) > 1:
                terminal.card(
                    "批量任务结束",
                    [("成功", str(len(task.urls) - failures)), ("失败", str(failures))],
                )
            choices = [("new", "新建同类任务"), ("home", "返回首页")]
            if failures:
                choices = [
                    ("retry", "重试失败任务"),
                    ("settings", "调整设置后重试失败任务"),
                    *choices,
                ]
            choice = terminal.select("接下来", choices, default="home")
            if choice in {"retry", "settings"}:
                task.urls = failed_urls
            if choice == "settings":
                self.configure(task.opts, export=False)
                if not self.review(task):
                    return False
            elif choice != "retry":
                return choice == "new"

    def comment_progress(self, pages: int, fetched: int, reported: Optional[int]) -> None:
        # API totals can include replies: they are not a completion denominator.
        self.terminal.stage(f"已获取 {fetched} 条评论 · {pages} 页")

    def login(self, opts: Options) -> bool:
        terminal = self.terminal
        terminal.page("首页 / 登录与账号状态 / 扫码登录")
        result = actions.login(
            opts,
            present=terminal.qr,
            on_scanned=lambda: terminal.note(
                "已扫码，请在 App 中确认。切换账号需取消后重新扫码。", "cyan"
            ),
        )
        terminal.page("首页 / 登录与账号状态 / 登录结果")
        terminal.messages(result.messages)
        if result.success:
            terminal.card("登录完成", [("", "登录会话已保存，可继续使用。")])
        terminal.pause("继续" if result.success else "返回首页")
        return result.success

    def account(self) -> None:
        self.terminal.page("首页 / 登录与账号状态")
        choice = self.terminal.select(
            "登录与账号状态",
            [
                ("login", "扫码登录 / 切换账号"),
                ("status", "在线检查登录与续期状态"),
                ("cookie", "导入浏览器 Cookie 的方法"),
                ("back", "返回首页"),
            ],
        )
        if choice == "login":
            self.login(self.opts)
        elif choice == "status":
            self.terminal.page("首页 / 登录与账号状态 / 在线状态")
            with self.terminal.busy("正在查询账号状态…"):
                result = actions.session_status(self.opts)
            self.terminal.messages(result.messages)
            self.terminal.pause()
        elif choice == "cookie":
            self.cookie_help(self.opts)

    def cookie_help(self, opts: Options) -> None:
        self.terminal.page("首页 / 登录与账号状态 / Cookie 导入")
        self.terminal.card(
            "导入浏览器 Cookie",
            [
                ("1", "在浏览器登录 B 站，导出 Netscape 格式的 Cookie 文本。"),
                ("2", "将 .txt 文件放到下方会话目录，支持任意文件名。"),
                ("目录", display_path(opts.cookie_dir or config_dir())),
                ("3", "重新开始任务；程序只提取 B 站条目。"),
            ],
        )
        self.terminal.pause()

    def configure(self, opts: Options, *, export: bool = True) -> None:
        terminal = self.terminal
        while True:
            terminal.page("首页 / 目录与高级设置" if export else "确认任务 / 调整本次设置")
            terminal.card(
                "本次会话设置" if export else "本次任务设置",
                [
                    ("默认内容", dict(MODES)[opts.mode]),
                    ("视频 / 字幕 / 评论", display_path(opts.video_dir or default_video_dir())),
                    ("音频", display_path(opts.audio_dir or default_audio_dir())),
                    ("会话目录", display_path(opts.cookie_dir or config_dir())),
                    ("代理", proxy_label(opts.proxy)),
                    ("下载证书校验", "已关闭（仅 yt-dlp）" if opts.insecure else "已开启"),
                ],
                subtitle=(
                    "立即作用于本次会话；配置文件不会自动改写"
                    if export
                    else "仅作用于当前任务；会话默认值与配置文件保持不变"
                ),
            )
            choices = [
                ("mode", "默认保存内容"),
                ("video_dir", "视频 / 字幕 / 评论目录"),
                ("audio_dir", "音频目录"),
                ("cookie_dir", "会话目录"),
                ("proxy", "代理设置"),
                ("insecure", "下载证书校验"),
            ]
            if export:
                choices.append(("export", "导出当前设置为配置文件"))
            choices.append(("back", "完成设置"))
            choice = terminal.select("调整哪一项？", choices, default="back")
            if choice == "back":
                return
            if choice == "mode":
                opts.mode = terminal.select("默认保存内容", MODES, default=opts.mode)
            elif choice in {"video_dir", "audio_dir", "cookie_dir"}:
                current = (
                    getattr(opts, choice)
                    or {
                        "video_dir": default_video_dir,
                        "audio_dir": default_audio_dir,
                        "cookie_dir": config_dir,
                    }[choice]()
                )
                setattr(
                    opts,
                    choice,
                    input_path(terminal.text("目录路径", default=display_path(current), path=True)),
                )
            elif choice == "proxy":
                mode = terminal.select(
                    "网络连接方式",
                    [
                        ("direct", "直连（明确禁用环境代理）"),
                        ("custom", "指定代理"),
                        ("env", "重新读取环境代理"),
                    ],
                )
                if mode == "custom":
                    value = terminal.text("代理地址，例如 http://127.0.0.1:7890", remember=False)
                    parsed = urllib.parse.urlsplit(value)
                    if (
                        parsed.scheme not in {"http", "https", "socks4", "socks5", "socks5h"}
                        or not parsed.hostname
                    ):
                        terminal.note("代理地址需要包含有效的协议与主机名。", "yellow")
                        terminal.pause("返回设置")
                        continue
                    opts.proxy = value
                else:
                    opts.proxy = "" if mode == "direct" else actions.resolve_proxy(None, None)
            elif choice == "insecure":
                terminal.note("登录与会话 API 始终校验证书；此设置仅作用于 yt-dlp。")
                opts.insecure = terminal.confirm("关闭下载证书校验？", default=opts.insecure)
            elif choice == "export":
                self.export_settings(opts)

    def export_settings(self, opts: Options) -> None:
        terminal = self.terminal
        terminal.page("目录与高级设置 / 导出配置")
        destination = self.config_path
        if destination.exists():
            destination = destination.with_name(f"{destination.stem}.interactive.toml")
        terminal.note("导出为新文件，保留已有配置。保存到默认 config.toml 时，下次启动会自动读取。")
        path = input_path(
            terminal.text("配置文件路径", default=display_path(destination), path=True)
        )
        if path.exists():
            terminal.note("文件已存在，请选择新的文件名；已有配置未修改。", "yellow")
            terminal.pause("返回设置")
            return
        # Proxy userinfo may contain a secret: don't persist it as an ordinary export.
        proxy: Optional[str] = opts.proxy
        if urllib.parse.urlsplit(opts.proxy).username is not None:
            terminal.note("代理含认证信息，导出文件将省略代理；本次会话仍沿用它。", "yellow")
            proxy = None
        settings.export(
            path,
            settings.Settings(
                mode=opts.mode,
                proxy=proxy,
                insecure=opts.insecure,
                video_dir=opts.video_dir or default_video_dir(),
                audio_dir=opts.audio_dir or default_audio_dir(),
                cookie_dir=opts.cookie_dir,
            ),
        )
        terminal.card(
            "配置已导出",
            [
                ("文件", display_path(path)),
                ("使用方法", "下次启动时，用 bili-dl --config FILE 指定这个文件。"),
            ],
        )
        terminal.pause("返回设置")

    def help(self) -> None:
        self.terminal.page("首页 / 使用帮助")
        self.terminal.card(
            "使用提示",
            [
                ("操作", "方向键选择，Enter 确认；Ctrl+C 返回首页，在首页按 Ctrl+C 退出。"),
                (
                    "下载",
                    "粘贴普通链接、分享文本或 BV / AV 号。视频保留声音，字幕只取已有首轨，不做语音识别。",
                ),
                ("评论", "主评论与楼中楼分别导出为 JSON；楼中楼需提供主评论的 rpid_str。"),
                ("批量", "UTF-8 文本每行一个链接，空行和 # 注释跳过，顺序执行。"),
                ("登录", "只有主动选择扫码登录才生成二维码；会话不读取浏览器资料。"),
                ("配置", "任务调整仅影响本次任务；首页设置影响本次会话。可显式导出配置。"),
                ("命令行", "仍可使用 bili-dl URL；各命令的 --help 提供完整参数。"),
                ("支持", "https://github.com/Echoziness/bili-dl/issues"),
            ],
        )
        self.terminal.pause()


def run(opts: Options, config_path: Optional[Path] = None) -> int:
    return Interactive(opts, config_path).run()
