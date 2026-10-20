"""Rendering, keyboard input, plain fallback and terminal ownership."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from bili_dl import ui
from bili_dl.models import DownloadResult
from bili_dl.terminal import Back, Terminal, clean, display_path


def test_plain_menu_validates_and_uses_default(monkeypatch: Any) -> None:
    terminal = Terminal()
    terminal.enhanced = False
    inputs = iter(["nonsense", "9", ""])
    monkeypatch.setattr(ui, "prompt", lambda p: next(inputs))
    assert terminal.select("任务", [("a", "音频"), ("v", "视频")], default="v") == "v"


def test_plain_text_validation_and_back(monkeypatch: Any) -> None:
    terminal = Terminal()
    terminal.enhanced = False
    inputs = iter(["bad", "123", "q"])
    monkeypatch.setattr(ui, "prompt", lambda p: next(inputs))
    assert terminal.text("数量", validate=lambda s: s.isdigit()) == "123"
    with pytest.raises(Back):
        terminal.text("下一项")


def test_arrow_menu_and_path_prompt_use_questionary(monkeypatch: Any) -> None:
    terminal = Terminal()
    terminal.enhanced = True
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "_prompt_options", lambda: {"input": pipe, "output": DummyOutput()}
        )
        pipe.send_text("\x1b[B\r")
        assert terminal.select("任务", [("a", "音频"), ("v", "视频")]) == "v"
        pipe.send_text("folder with spaces\r")
        assert terminal.text("路径", path=True) == "folder with spaces"


@pytest.fixture
def live_events(monkeypatch: Any) -> list[str]:
    calls = []

    class Live:
        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr("bili_dl.terminal.Live", lambda *a, **kw: Live())
    return calls


def test_native_download_stops_live_display_before_output(
    monkeypatch: Any, live_events: list[str]
) -> None:
    terminal = Terminal()
    terminal.enhanced = True
    monkeypatch.setattr(terminal, "note", lambda *a, **kw: live_events.append("output"))
    terminal.stage("prepare")
    terminal.stage("download", Path("视频.mp4"))
    assert live_events.index("start") < live_events.index("stop") < live_events.index("output")


def test_rendering_is_literal_private_and_no_color(monkeypatch: Any) -> None:
    terminal = Terminal()
    stream = io.StringIO()
    terminal.console = Console(file=stream, width=48, no_color=True, force_terminal=False)
    path = Path.home() / "Videos" / "[red]示例[/red].mp4"
    terminal.result(
        DownloadResult(True, output_path=path, additional_paths=[path.with_suffix(".m4a")])
    )
    terminal.messages([("error", f"无法写入 {path}")])
    result = stream.getvalue()
    assert "[red]" in result and ".m4a" in result and ".mp4" in result
    assert str(Path.home()) not in result and "\x1b" not in result
    assert display_path(path).startswith("~")


def test_external_control_sequences_are_not_executed() -> None:
    assert clean("title\x1b[2J\x1b]0;evil\x07\x00") == "title"


def test_busy_restores_terminal_when_operation_raises(live_events: list[str]) -> None:
    terminal = Terminal()
    terminal.enhanced = True
    with pytest.raises(KeyboardInterrupt), terminal.busy("working"):
        raise KeyboardInterrupt
    assert live_events == ["start", "stop"]


def test_page_navigation_clears_old_content_and_shows_location(monkeypatch: Any) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal = Terminal()
    stream = io.StringIO()
    terminal.console = Console(
        file=stream, width=80, force_terminal=True, no_color=True, legacy_windows=False
    )
    terminal.refreshable = True
    terminal.enhanced = False
    terminal.page("首页 / 下载内容")
    answers = iter(["BV1Got26ZE5K", "2"])
    monkeypatch.setattr(ui, "prompt", lambda p: next(answers))
    assert terminal.text("视频链接") == "BV1Got26ZE5K"
    assert terminal.select("保存内容", [("v", "视频"), ("a", "音频")]) == "a"
    frame = stream.getvalue().split("\x1b[2J")[-1]
    assert "BV1Got26ZE5K" in frame
    terminal.page("首页 / 目录与高级设置")
    frame = stream.getvalue().split("\x1b[2J")[-1]
    assert "目录与高级设置" in frame
    assert "BV1Got26ZE5K" not in frame and "下载内容" not in frame


def test_previous_numbered_menu_is_not_replayed(monkeypatch: Any) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal = Terminal()
    stream = io.StringIO()
    terminal.console = Console(
        file=stream, width=48, force_terminal=True, no_color=True, legacy_windows=False
    )
    terminal.refreshable = True
    terminal.enhanced = False
    monkeypatch.setattr(ui, "prompt", lambda p: "1")
    terminal.page("评论导出")
    terminal.select("排序", [("hot", "热门"), ("newest", "最新")])
    terminal.select("数量", [("all", "全部")])
    frame = stream.getvalue().split("\x1b[2J")[-1]
    assert "排序  热门" in frame and "1. 全部" in frame
    assert "2. 最新" not in frame


def test_native_failure_keeps_diagnostics_until_leaving_result(monkeypatch: Any) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal = Terminal()
    stream = io.StringIO()
    terminal.console = Console(
        file=stream, width=80, force_terminal=True, no_color=True, legacy_windows=False
    )
    terminal.refreshable = True
    terminal.enhanced = False
    terminal.page("下载 / 执行")
    terminal.stage("download", Path("example.mp4"))
    terminal.console.print("NATIVE ERROR DETAILS")
    terminal.result(DownloadResult(False, [("error", "下载未完成")]))
    monkeypatch.setattr(ui, "prompt", lambda p: "1")
    terminal.pause()
    text = stream.getvalue().split("NATIVE ERROR DETAILS", 1)[1]
    assert "\x1b[2J" not in text and "任务结果" in text
    terminal.home()
    frame = stream.getvalue().split("\x1b[2J")[-1]
    assert "首页" in frame and "NATIVE ERROR DETAILS" not in frame


def test_plain_pages_have_boundaries_without_escape_sequences() -> None:
    terminal = Terminal()
    stream = io.StringIO()
    terminal.console = Console(file=stream, width=44, force_terminal=False)
    terminal.refreshable = False
    terminal.page("首页")
    terminal.page("首页 / 使用帮助")
    assert "使用帮助" in stream.getvalue() and "\x1b" not in stream.getvalue()


def test_validation_error_is_visible_inside_full_page(monkeypatch: Any) -> None:
    from prompt_toolkit.input import DummyInput

    terminal = Terminal()
    terminal.enhanced = True
    monkeypatch.setattr(
        terminal, "_prompt_options", lambda: {"input": DummyInput(), "output": DummyOutput()}
    )
    attempts = iter(["bad", "12"])
    frames = []

    def answer(question: Any) -> str:
        frames.append(terminal._render_body(60, 20))
        return next(attempts)

    monkeypatch.setattr(terminal, "_ask", answer)
    assert terminal.text("数量", validate=lambda s: True if s.isdigit() else "请填写数字") == "12"
    assert "请填写数字" not in frames[0] and "请填写数字" in frames[1]
    assert "请填写数字" not in terminal._render_body(60, 20)


def test_full_page_keeps_text_during_live_resize() -> None:
    import questionary
    from prompt_toolkit.data_structures import Size

    class ResizableOutput(DummyOutput):
        size = Size(rows=32, columns=96)

        def get_size(self) -> Size:
            return self.size

    terminal = Terminal()
    output = ResizableOutput()
    frames = []
    with create_pipe_input() as pipe:
        question = questionary.text("视频链接", input=pipe, output=output)
        app = question.application
        app.before_render += lambda a: frames.append(output.get_size())

        def shrink() -> None:
            output.size = Size(rows=10, columns=30)
            app.invalidate()
            app.loop.call_later(0.08, finish)

        def finish() -> None:
            output.size = Size(rows=36, columns=120)
            app.invalidate()
            app.loop.call_later(0.08, lambda: pipe.send_text("26ZE5K\r"))

        def begin() -> None:
            pipe.send_text("BV1Got")
            app.loop.call_later(0.08, shrink)

        app.pre_run_callables.append(begin)
        assert terminal._ask(question) == "BV1Got26ZE5K"
    assert Size(rows=10, columns=30) in frames
    assert Size(rows=36, columns=120) in frames
