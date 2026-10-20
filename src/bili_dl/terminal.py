"""Terminal presentation and input; never owns a download or session protocol."""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from . import __version__, authqr, ui
from .models import DownloadResult


class Back(Exception):
    """Return from a form without starting an operation."""


@dataclass(frozen=True)
class _Card:
    title: str
    rows: tuple[tuple[str, str], ...]
    subtitle: str


def clean(value: object) -> str:
    """External titles/messages are text, never markup or terminal commands."""
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)


def display_path(path: Path) -> str:
    """Compact personal paths in the UI; the actual Path stays untouched."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


class Terminal:
    def __init__(self) -> None:
        self.color = ui.supports_color()
        self.console = Console(
            stderr=True, no_color=not self.color, highlight=False, markup=False, emoji=False
        )
        self.refreshable = (
            sys.stdin.isatty() and sys.stderr.isatty() and os.environ.get("TERM") != "dumb"
        )
        # Keep one input control across resizes, including small viewports.
        self.enhanced = self.refreshable
        self._status: Optional[Live] = None
        self._status_label = ""
        self._qr: Optional[tuple[str, str]] = None
        self._page = "首页"
        self._body: list[Text | _Card] = []
        self._preserve_output = False
        self._native_output = False

    def page(self, title: str, *, preserve_output: bool = False) -> None:
        """Replace a page, with a visible location even in plain terminals."""
        self.stop()
        self._page = title
        self._body = []
        self._preserve_output = preserve_output
        self._native_output = False
        self._redraw(new_page=True)

    def _redraw(self, *, new_page: bool = False) -> None:
        if not new_page and (not self.refreshable or self._preserve_output):
            return
        with self.console:
            if self.refreshable and not self._preserve_output:
                self.console.clear(home=True)
            else:
                self.console.print()
            self.console.rule(
                Text.assemble(("bili-dl", "bold cyan"), (f"  /  {clean(self._page)}", "dim")),
                align="left",
            )
            self.console.print()
            for item in self._body:
                self.console.print(self._render_item(item, *self.console.size))

    def _answer(self, title: str, value: str) -> None:
        # The next input redraws a compact summary instead of old prompt widgets.
        self._body.append(Text(f"{clean(title)}  {clean(value)}", style="dim"))

    def pause(self, label: str = "返回首页") -> None:
        self.select("下一步", [("back", label)])

    def note(self, text: str, style: str = "dim") -> None:
        item = Text(clean(text).replace(str(Path.home()), "~"), style=style)
        self._body.append(item)
        if self._status is not None:
            self._status.refresh()
        else:
            self.console.print(item)

    def card(self, title: str, rows: Sequence[tuple[str, str]], *, subtitle: str = "") -> None:
        item = _Card(clean(title), tuple((clean(k), clean(v)) for k, v in rows), clean(subtitle))
        self._body.append(item)
        if self._status is not None:
            self._status.refresh()
        else:
            self.console.print(self._render_item(item, *self.console.size))

    def _render_item(self, item: Text | _Card, width: int, height: int) -> Text | Panel:
        if isinstance(item, Text):
            return item
        compact = width < 64 or height < 24
        table = Table.grid(padding=(0, 1 if compact else 2), expand=True)
        table.add_column(style="dim", no_wrap=False)
        if width >= 48:
            table.add_column(ratio=1, overflow="fold")
        for label, value in item.rows:
            if width >= 48:
                table.add_row(Text(label), Text(value))
            else:
                table.add_row(
                    Text.assemble((f"{label}\n", "dim") if label else ("", ""), (value, ""))
                )
        return Panel(
            table,
            title=Text(item.title, style="bold cyan"),
            title_align="left",
            subtitle=Text(item.subtitle, style="dim") if item.subtitle else None,
            subtitle_align="left",
            border_style="cyan" if self.color else "dim",
            box=box.ROUNDED if self.enhanced and not compact else box.SIMPLE,
            padding=(0, 1) if compact else (1, 2),
        )

    def _render_body(self, width: int, height: int) -> str:
        output = io.StringIO()
        console = Console(
            file=output,
            width=width,
            height=height,
            force_terminal=True,
            color_system="standard",
            no_color=not self.color,
            legacy_windows=False,
            markup=False,
            highlight=False,
            emoji=False,
        )
        items: Sequence[Text | _Card] = tuple(self._body)
        if self._qr is not None and height < 18:
            items = [Text(self._qr[0])]
        for item in items:
            console.print(self._render_item(item, width, height))
        return output.getvalue()

    def _live_page(self) -> Group:
        width, height = self.console.size
        budget = max(0, height - 3)
        header = Text(f"bili-dl / {self._page}", style="bold")
        header.truncate(width, overflow="ellipsis")
        lines = Text.from_ansi(self._render_body(width, height)).split("\n")
        if lines and not lines[-1].plain:
            lines.pop()
        content: list[Text | Spinner] = list(lines[:budget])
        if self._qr is not None:
            image = self._qr[1]
            qr_lines = image.splitlines()
            if (
                qr_lines
                and len(lines) + len(qr_lines) <= budget
                and max(map(len, qr_lines)) < width
            ):
                content.append(Text(image, style="black on white" if self.color else ""))
            elif len(lines) < budget:
                content.append(Text("窗口不足以完整显示二维码，请在手机打开网址。", style="dim"))
        content.append(Spinner("line", text=Text(self._status_label)))
        return Group(header, *content)

    def home(self) -> None:
        self.page("首页")
        self.card(
            f"bili-dl  {__version__}",
            [("", "B 站视频、音频、字幕与评论下载工具")],
        )

    def select(
        self, title: str, choices: Sequence[tuple[str, str]], *, default: Optional[str] = None
    ) -> str:
        self.stop()
        self._redraw()
        if not self.enhanced:
            self.console.print(Text(clean(title), style="bold"))
            for index, (_, label) in enumerate(choices, 1):
                self.console.print(Text(f"  {index}. {clean(label)}"))
            selected = next((i for i, (key, _) in enumerate(choices, 1) if key == default), 1)
            while True:
                answer = ui.prompt(f"选择 [{selected}]（q 返回）: ").strip()
                if answer.lower() == "q":
                    raise Back
                if not answer:
                    self._answer(title, choices[selected - 1][1])
                    return choices[selected - 1][0]
                if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(choices):
                    self._answer(title, choices[int(answer) - 1][1])
                    return choices[int(answer) - 1][0]
                self.console.print(Text("请输入菜单中的数字。", style="yellow"))
        import questionary

        question = questionary.select(
            title,
            choices=[questionary.Choice(clean(label), value=key) for key, label in choices],
            default=default,
            qmark="",
            pointer=">",
            use_jk_keys=False,
            instruction=" ↑↓ / Enter",
            **self._prompt_options(),
        )
        answer = self._ask(question)
        self._answer(title, dict(choices)[answer])
        return answer

    def _ask(self, question: Any) -> str:
        """Put page content and the existing input widget in one resizable UI.

        Questionary's text/path PromptSessions create a non-full-screen app.
        Configure both its application and renderer before starting, keeping
        the same buffer, completion state and selected item during resizing.
        """
        if self._preserve_output:
            # Keep uncaptured yt-dlp diagnostics and terminal scrollback visible.
            return cast(str, question.unsafe_ask())

        from prompt_toolkit.formatted_text import ANSI, to_formatted_text
        from prompt_toolkit.formatted_text.utils import split_lines
        from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
        from prompt_toolkit.key_binding.key_processor import KeyPressEvent
        from prompt_toolkit.layout import Dimension, HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl

        app = question.application
        content = app.layout.container
        focus = app.layout.current_control
        offset = 0
        cache: dict[tuple[int, int], list[Any]] = {}

        def geometry() -> tuple[int, int, int, int]:
            size = app.output.get_size()
            width, height = max(1, size.columns), max(1, size.rows)
            prompt_height = min(
                max(3, content.preferred_height(width, height).preferred), max(1, height - 4)
            )
            return width, height, prompt_height, max(0, height - prompt_height - 2)

        def body_lines() -> list[Any]:
            width, height, _, _ = geometry()
            key = width, height
            if key not in cache:
                cache[key] = list(
                    split_lines(to_formatted_text(ANSI(self._render_body(width, height))))
                )
            return cache[key]

        def header() -> str:
            width, _, _, _ = geometry()
            title = f"bili-dl / {self._page}"
            if Text(title).cell_len > width:
                title = f"bili-dl / {self._page.split(' / ')[-1]}"
            text = Text(clean(title))
            text.truncate(width, overflow="ellipsis")
            return text.plain

        def body() -> list[Any]:
            nonlocal offset
            lines = body_lines()
            budget = geometry()[3]
            offset = min(offset, max(0, len(lines) - budget))
            fragments: list[Any] = []
            for index, line in enumerate(lines[offset : offset + budget]):
                if index:
                    fragments.append(("", "\n"))
                fragments.extend(line)
            return fragments

        def footer() -> str:
            return (
                "PgUp/PgDn 查看内容 · Ctrl+C 返回"
                if len(body_lines()) > geometry()[3]
                else "Ctrl+C 返回"
            )

        bindings = KeyBindings()

        @bindings.add("pageup", eager=True)
        def scroll_up(event: KeyPressEvent) -> None:
            nonlocal offset
            offset = max(0, offset - max(1, geometry()[3]))

        @bindings.add("pagedown", eager=True)
        def scroll_down(event: KeyPressEvent) -> None:
            nonlocal offset
            offset = min(max(0, len(body_lines()) - geometry()[3]), offset + max(1, geometry()[3]))

        app.layout = Layout(
            HSplit(
                [
                    Window(FormattedTextControl(header), height=1, style="bold"),
                    Window(
                        FormattedTextControl(body),
                        height=lambda: min(len(body_lines()), geometry()[3]),
                    ),
                    HSplit([content], height=lambda: geometry()[2]),
                    Window(height=Dimension(min=0)),
                    Window(FormattedTextControl(footer), height=1, style="class:instruction"),
                ]
            ),
            focused_element=focus,
        )
        app.key_bindings = merge_key_bindings([app.key_bindings, bindings])
        app.full_screen = True
        app.renderer.full_screen = True
        app.terminal_size_polling_interval = 0.1
        return cast(str, question.unsafe_ask())

    def _prompt_options(self) -> dict[str, Any]:
        from prompt_toolkit.output import ColorDepth
        from prompt_toolkit.output.defaults import create_output
        from questionary import Style

        return {
            "output": create_output(stdout=sys.stderr),
            "color_depth": ColorDepth.DEPTH_8_BIT if self.color else ColorDepth.DEPTH_1_BIT,
            "style": Style(
                [
                    ("question", "bold"),
                    ("pointer", "fg:ansicyan bold"),
                    ("highlighted", "fg:ansicyan bold"),
                    ("answer", "fg:ansicyan"),
                    ("instruction", "fg:ansibrightblack"),
                ]
            ),
        }

    def text(
        self,
        title: str,
        *,
        default: str = "",
        validate: Optional[Callable[[str], bool | str]] = None,
        path: bool = False,
        remember: bool = True,
    ) -> str:
        self.stop()
        error = ""
        while True:
            self._redraw()
            if error:
                self.console.print(Text(error, style="yellow"))
            if self.enhanced:
                import questionary

                factory = questionary.path if path else questionary.text
                options = self._prompt_options()
                if not path:
                    options["instruction"] = ""
                if error:
                    self._body.append(Text(error, style="yellow"))
                try:
                    value = self._ask(factory(title, default=default, qmark="", **options))
                finally:
                    if error:
                        self._body.pop()
            else:
                suffix = f" [{default}]" if default else ""
                value = ui.prompt(f"{title}{suffix}（q 返回）: ") or default
            if value.strip().lower() == "q":
                raise Back
            value = value.strip()
            valid = validate(value) if validate else True
            if valid is True:
                if remember:
                    self._answer(title, value.replace(str(Path.home()), "~"))
                return value
            error = valid if isinstance(valid, str) else "输入格式不正确，请重新输入。"
            default = value

    def confirm(self, title: str, *, default: bool = False) -> bool:
        return (
            self.select(title, [("yes", "是"), ("no", "否")], default="yes" if default else "no")
            == "yes"
        )

    def stop(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        self._qr = None

    def stage(self, name: str, path: Optional[Path] = None) -> None:
        labels = {
            "prepare": "正在获取视频信息…",
            "subtitle": "正在获取第一条字幕…",
            "download": "正在下载 · Ctrl+C 可取消",
            "extract": "正在提取音频并整理容器…",
            "repair": "正在整理音频容器…",
        }
        label = labels.get(name, name)
        if name == "download":
            self.stop()
            self._redraw()
            self._native_output = True
            if path is not None:
                self.note(path.name, "bold")
            self.note(label)
            self.note("音视频可能分别显示进度，完成后还会合并或整理文件。")
            return
        if self.enhanced:
            self._status_label = label
            if self._status is None:
                self._status = Live(
                    get_renderable=self._live_page,
                    console=self.console,
                    screen=True,
                    refresh_per_second=6,
                    vertical_overflow="crop",
                    redirect_stdout=False,
                    redirect_stderr=False,
                )
                self._status.start()
            else:
                self._status.refresh()
        else:
            self.note(label)

    @contextlib.contextmanager
    def busy(self, label: str) -> Iterator[None]:
        self.stage(label)
        try:
            yield
        finally:
            self.stop()

    def messages(self, messages: Sequence[tuple[str, str]]) -> None:
        styles = {"error": "red", "warn": "yellow", "ok": "green", "info": "dim"}
        for level, text in messages:
            self.note(text, styles.get(level, ""))

    def result(self, result: DownloadResult, *, page: str = "任务结果") -> None:
        has_notes = any(level in {"warn", "error"} for level, _ in result.messages)
        # Native stderr was intentionally not captured. Keep diagnostics visible
        # until the user leaves this result page, including its action menu.
        preserve = self._native_output and (not result.success or has_notes)
        self.page(page, preserve_output=preserve)
        paths = ([result.output_path] if result.output_path else []) + result.additional_paths
        if result.success:
            rows = [("文件", display_path(path)) for path in paths]
            self.card("已保存 · 有提示" if has_notes else "已完成", rows or [("", "操作已完成。")])
            self.messages(
                [(level, text) for level, text in result.messages if level in {"warn", "error"}]
            )
        else:
            self.card("本次任务未完成", [("下一步", "可重试、调整设置，或返回首页。")])
            self.messages(result.messages)

    def qr(self, session: authqr.QrSession, *, url_only: bool = False) -> None:
        self.stop()
        self.note("用 B 站 App 扫码，在手机上确认；约 180 秒内有效。")
        image = ""
        if not url_only:
            try:
                image = authqr.render_terminal_qr(session.url)
            except Exception:
                self.note("无法绘制二维码，可在手机打开下方确认网址。", "yellow")
        self.note("手机扫码确认网址（仅供本次登录）：")
        self.note(session.url, "")
        if self.enhanced:
            self._qr = session.url, image
            self.stage("等待扫码并在手机上确认…")
            return
        lines = image.splitlines()
        if (
            lines
            and sys.stderr.isatty()
            and len(lines) + 4 < self.console.size.height
            and max(map(len, lines)) < self.console.width
        ):
            self.console.print(Text(image, style="black on white" if self.color else ""))
        elif image:
            self.note("终端空间不足以完整显示二维码，请在手机打开确认网址。")
