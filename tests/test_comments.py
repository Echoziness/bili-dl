"""Comment pagination, limits, atomic output and safe failure tests."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from bili_dl import cli, commentapi, comments, transport, video
from bili_dl.models import DownloadResult

VIDEO_URL = "https://www.bilibili.com/video/BV1cSec6tEux/"
INFO = video.VideoInfo(117280105045764, "BV1cSec6tEux", "测试:标题", 1, [{"cid": 1}])
IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


def _comment(rpid: int, message: str = "正文") -> dict[str, Any]:
    return {
        "rpid": rpid,
        "rpid_str": str(rpid),
        "ctime": 1700000000 + rpid,
        "like": rpid,
        "member": {"mid": str(rpid), "uname": f"用户{rpid}"},
        "content": {"message": message, "pictures": []},
    }


def _main_page(
    replies: list[dict[str, Any]],
    *,
    top: list[dict[str, Any]] | None = None,
    offset: str = "next",
    is_end: bool = False,
    count: int = 3,
) -> dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "top_replies": top or [],
            "replies": replies,
            "cursor": {
                "all_count": count,
                "is_end": is_end,
                "pagination_reply": {"next_offset": offset},
            },
        },
    }


def _thread_page(
    root: dict[str, Any],
    replies: list[dict[str, Any]],
    count: int,
    *,
    number: int = 1,
    size: int = 2,
) -> dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "root": root,
            "replies": [dict(item, root_str=root["rpid_str"]) for item in replies],
            "page": {"num": number, "size": size, "count": count},
        },
    }


@pytest.fixture
def cfg(tmp_path: Path) -> comments.CommentConfig:
    cookie = tmp_path / "cookies_bilibili.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tfake\n",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    output.mkdir()
    return comments.CommentConfig(output, cookie)


@pytest.fixture(autouse=True)
def content_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(comments.time, "sleep", lambda delay: None)
    monkeypatch.setattr(comments.video, "resolve", lambda *args: INFO)
    monkeypatch.setattr(commentapi.wbi, "fetch_keys", lambda opener: ((IMG_KEY, SUB_KEY), None))


def _load_result(result: Any) -> dict[str, Any]:
    assert result.success, result.messages
    assert result.output_path is not None
    return json.loads(result.output_path.read_text(encoding="utf-8"))


def test_main_download_streams_pages_deduplicates_and_marks_pinned(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [
        _main_page([_comment(2)], top=[_comment(1, "置顶")], offset="cursor-1"),
        _main_page([_comment(3)], top=[_comment(1, "置顶")], is_end=True),
    ]
    queries: list[dict[str, list[str]]] = []

    def fetch(opener: Any, request: Any, timeout: float) -> tuple[dict[str, Any], None]:
        queries.append(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query))
        return pages.pop(0), None

    monkeypatch.setattr(comments.transport, "fetch_json", fetch)
    payload = _load_result(comments.download_main(VIDEO_URL, cfg))

    assert [item["rpid_str"] for item in payload["comments"]] == ["1", "2", "3"]
    assert {
        key: payload["result"][key]
        for key in (
            "complete",
            "stopped_reason",
            "fetched_count",
            "reported_count",
            "pages",
            "pinned_ids",
        )
    } == {
        "complete": True,
        "stopped_reason": "end",
        "fetched_count": 3,
        "reported_count": 3,
        "pages": 2,
        "pinned_ids": ["1"],
    }
    assert payload["request"] == {"sort": "newest", "limit": None, "fields": "lean"}
    assert json.loads(queries[0]["pagination_str"][0]) == {"offset": ""}
    assert json.loads(queries[1]["pagination_str"][0]) == {"offset": "cursor-1"}
    assert all("w_rid" in query and "wts" in query for query in queries)


def test_main_limit_stops_mid_page_and_is_successful(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, limit=2)
    calls = 0

    def fetch(opener: Any, request: Any, timeout: float) -> tuple[dict[str, Any], None]:
        nonlocal calls
        calls += 1
        return _main_page([_comment(1), _comment(2), _comment(3)], count=100), None

    monkeypatch.setattr(comments.transport, "fetch_json", fetch)
    payload = _load_result(comments.download_main(VIDEO_URL, cfg))

    assert calls == 1
    assert len(payload["comments"]) == 2
    assert payload["result"]["complete"] is False
    assert payload["result"]["stopped_reason"] == "limit"


def test_main_exact_limit_at_api_end_is_still_complete(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, limit=2)
    monkeypatch.setattr(
        comments.transport,
        "fetch_json",
        lambda *args, **kwargs: (_main_page([_comment(1), _comment(2)], is_end=True), None),
    )

    payload = _load_result(comments.download_main(VIDEO_URL, cfg))

    assert payload["result"]["fetched_count"] == 2
    assert payload["result"]["complete"] is True
    assert payload["result"]["stopped_reason"] == "end"


def test_hot_pages_may_advance_with_an_unchanged_offset(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, sort="hot")
    pages = [
        _main_page([_comment(1)], offset="opaque-session-offset"),
        _main_page([_comment(2)], offset="opaque-session-offset", is_end=True),
    ]
    monkeypatch.setattr(
        comments.transport, "fetch_json", lambda *args, **kwargs: (pages.pop(0), None)
    )

    payload = _load_result(comments.download_main(VIDEO_URL, cfg))

    assert [item["rpid_str"] for item in payload["comments"]] == ["1", "2"]
    assert payload["request"]["sort"] == "hot"
    assert payload["result"]["pages"] == 2
    assert payload["result"]["complete"] is True
    assert payload["kind"] == "main_comments"


def test_rejected_signature_refreshes_keys_once(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_calls = 0
    responses = [{"code": -403}, _main_page([_comment(1)], is_end=True, count=1)]

    def keys(opener: Any) -> tuple[tuple[str, str], None]:
        nonlocal key_calls
        key_calls += 1
        return (IMG_KEY, SUB_KEY), None

    monkeypatch.setattr(commentapi.wbi, "fetch_keys", keys)
    monkeypatch.setattr(
        comments.transport, "fetch_json", lambda *args, **kwargs: (responses.pop(0), None)
    )

    assert comments.download_main(VIDEO_URL, cfg).success
    assert key_calls == 2


def test_thread_download_includes_root_and_all_children(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _comment(100, "主评论")
    pages = [
        _thread_page(root, [_comment(101), _comment(102)], 3),
        _thread_page(root, [_comment(103)], 3, number=2),
        _thread_page(root, [], 3, number=3),
    ]
    requested_pages: list[str] = []

    def fetch(opener: Any, request: Any, timeout: float) -> tuple[dict[str, Any], None]:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        requested_pages.append(query["pn"][0])
        return pages.pop(0), None

    monkeypatch.setattr(comments.transport, "fetch_json", fetch)
    payload = _load_result(comments.download_replies(VIDEO_URL, "100", cfg))

    assert payload["root_comment"]["rpid_str"] == "100"
    assert [item["rpid_str"] for item in payload["replies"]] == ["101", "102", "103"]
    assert requested_pages == ["1", "2", "3"]
    assert payload["result"]["fetched_count"] == 4
    assert payload["result"]["complete"] is True


def test_thread_limit_counts_the_root_comment(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, limit=2)
    root = _comment(100)
    calls = 0

    def fetch(opener: Any, request: Any, timeout: float) -> tuple[dict[str, Any], None]:
        nonlocal calls
        calls += 1
        return _thread_page(root, [_comment(101), _comment(102)], 20), None

    monkeypatch.setattr(comments.transport, "fetch_json", fetch)
    payload = _load_result(comments.download_replies(VIDEO_URL, "100", cfg))

    assert calls == 1
    assert [item["rpid_str"] for item in payload["replies"]] == ["101"]
    assert payload["result"]["fetched_count"] == 2
    assert payload["result"]["complete"] is False
    assert payload["result"]["stopped_reason"] == "limit"


def test_transient_page_failure_is_retried(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses: list[tuple[dict[str, Any] | None, transport.HttpFailure | None]] = [
        (None, transport.HttpFailure("network")),
        (_main_page([_comment(1)], is_end=True, count=1), None),
    ]
    monkeypatch.setattr(comments.transport, "fetch_json", lambda *args, **kwargs: responses.pop(0))

    assert comments.download_main(VIDEO_URL, cfg).success
    assert responses == []


def test_failure_preserves_existing_output_and_removes_temporary_file(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = cfg.output_dir / "测试_标题 [BV1cSec6tEux].comments.json"
    path.write_text("previous complete output", encoding="utf-8")
    monkeypatch.setattr(
        comments.transport,
        "fetch_json",
        lambda *args, **kwargs: ({"code": 0, "data": {"replies": [], "cursor": None}}, None),
    )

    result = comments.download_main(VIDEO_URL, cfg)

    assert not result.success
    assert path.read_text(encoding="utf-8") == "previous complete output"
    assert list(cfg.output_dir.iterdir()) == [path]


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda cfg: comments.download_main(VIDEO_URL, cfg), "正整数"),
        (lambda cfg: comments.download_replies(VIDEO_URL, "invalid", cfg), "ID 必须"),
    ],
)
def test_invalid_limits_and_ids_stop_before_requests(
    cfg: comments.CommentConfig,
    operation: Any,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if "正整数" in message:
        cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, limit=0)
    monkeypatch.setattr(
        comments.transport, "fetch_json", lambda *args: pytest.fail("Must not request")
    )

    result = operation(cfg)

    assert not result.success
    assert message in str(result.messages)


def test_api_response_body_is_never_exposed(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        comments.transport,
        "fetch_json",
        lambda *args, **kwargs: ({"code": -352, "message": "SESSDATA=secret"}, None),
    )

    result = comments.download_main(VIDEO_URL, cfg)

    assert not result.success
    assert "风控" in str(result.messages)
    assert "secret" not in repr(result)


def test_comments_cli_forwards_limit_sort_and_shared_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[tuple[str, comments.CommentConfig]] = []
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(
        cli.comments,
        "download_main",
        lambda url, cfg: captured.append((url, cfg)) or DownloadResult(True),
    )
    monkeypatch.setattr(cli.downloader, "find_ytdlp", lambda: pytest.fail("No yt-dlp needed"))
    output = tmp_path / "comments"
    cookie_dir = tmp_path / "cookies"

    result = cli.main(
        [
            "comments",
            VIDEO_URL,
            "--sort",
            "hot",
            "--limit",
            "25",
            "--proxy",
            "http://proxy:7890",
            "--cookie-dir",
            str(cookie_dir),
            "--output-dir",
            str(output),
        ]
    )

    assert result == 0
    assert captured[0][0] == VIDEO_URL
    actual = captured[0][1]
    assert actual.sort == "hot" and actual.limit == 25
    assert actual.proxy == "http://proxy:7890"
    assert actual.output_dir == output
    assert actual.cookie_path == cookie_dir / "cookies_bilibili.txt"
    assert actual.progress is not None
    actual.progress(1, 20, 100)
    assert output.is_dir() and cookie_dir.is_dir()
    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert "20" in terminal.err
    assert "20/100" not in terminal.err


def test_replies_cli_includes_root_in_limit_and_propagates_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str, comments.CommentConfig]] = []
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)

    def fail(url: str, root_id: str, cfg: comments.CommentConfig) -> DownloadResult:
        captured.append((url, root_id, cfg))
        return DownloadResult(False, [("error", "expected failure")])

    monkeypatch.setattr(cli.comments, "download_replies", fail)

    result = cli.main(
        [
            "replies",
            VIDEO_URL,
            "317745878352",
            "--limit",
            "1",
            "--cookie-dir",
            str(tmp_path / "cookies"),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert result == 1
    assert captured[0][0:2] == (VIDEO_URL, "317745878352")
    assert captured[0][2].limit == 1


def test_comments_cli_reuses_config_dirs_and_explicitly_disabled_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_dir = tmp_path / "configured-cookies"
    output = tmp_path / "configured-output"
    config = tmp_path / "config.toml"
    config.write_text(
        f'proxy = ""\ncookie_dir = "{cookie_dir.as_posix()}"\nvideo_dir = "{output.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://environment-proxy:7890")
    prepared: list[cli.Options] = []
    captured: list[comments.CommentConfig] = []
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: prepared.append(opts) or True)
    monkeypatch.setattr(
        cli.comments,
        "download_main",
        lambda url, cfg: captured.append(cfg) or DownloadResult(True),
    )

    assert cli.main(["comments", VIDEO_URL, "--config", str(config)]) == 0

    assert prepared[0].cookie_dir == cookie_dir
    assert prepared[0].proxy == ""
    assert captured[0].cookie_path == cookie_dir / "cookies_bilibili.txt"
    assert captured[0].output_dir == output
    assert captured[0].proxy == ""


@pytest.mark.parametrize(
    "args",
    [
        ["comments", VIDEO_URL, "--limit", "0"],
        ["comments", VIDEO_URL, "--limit", "invalid"],
        ["replies", VIDEO_URL, "0"],
    ],
)
def test_comment_cli_rejects_non_positive_counts_before_work(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: pytest.fail("Must not prepare"))
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize("thread", [False, True])
def test_limit_one_never_serializes_embedded_previews(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch, thread: bool
) -> None:
    original = dict(_comment(100), replies=[_comment(999, "excluded preview")])
    cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, limit=1)
    response = (
        _thread_page(original, [_comment(101)], 20)
        if thread
        else _main_page([original], is_end=True)
    )
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    result = (
        comments.download_replies(VIDEO_URL, "100", cfg)
        if thread
        else comments.download_main(VIDEO_URL, cfg)
    )
    payload = _load_result(result)
    assert "excluded preview" not in json.dumps(payload)
    assert original["replies"][0]["rpid"] == 999  # No mutation of the API object.
    assert payload["result"]["fetched_count"] == 1
    if thread:
        assert payload["replies"] == []
        assert payload["result"]["complete"] is False


def test_thread_does_not_trust_stale_count_and_deduplicates_pages(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _comment(100)
    pages = [
        _thread_page(root, [_comment(101), _comment(102)], 1),
        _thread_page(root, [_comment(102), _comment(103)], 1, number=2),
        _thread_page(root, [], 10, number=3),
    ]
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (pages.pop(0), None))
    payload = _load_result(comments.download_replies(VIDEO_URL, "100", cfg))
    assert [r["rpid_str"] for r in payload["replies"]] == ["101", "102", "103"]
    assert payload["result"]["pages"] == 3
    assert payload["result"]["reported_reply_count_first"] == 1
    assert payload["result"]["reported_reply_count"] == 10
    assert payload["result"]["complete"] is True


@pytest.mark.parametrize(
    "failure",
    [
        transport.HttpFailure("network"),
        transport.HttpFailure("bad_json"),
        transport.HttpFailure("http", 503),
    ],
)
def test_hot_transport_failure_is_not_replayed(
    cfg: comments.CommentConfig,
    monkeypatch: pytest.MonkeyPatch,
    failure: transport.HttpFailure,
) -> None:
    calls = []

    def fetch(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return None, failure

    monkeypatch.setattr(transport, "fetch_json", fetch)
    cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, sort="hot")
    result = comments.download_main(VIDEO_URL, cfg)
    assert not result.success and len(calls) == 1
    assert "避免重试漏页" in str(result.messages)
    assert not list(cfg.output_dir.iterdir())


@pytest.mark.parametrize(
    "failure,expected_calls",
    [
        (transport.HttpFailure("network"), 3),
        (transport.HttpFailure("http", 429), 3),
        (transport.HttpFailure("http", 503), 3),
        (transport.HttpFailure("http", 412), 1),
    ],
)
def test_retry_budget_is_bounded(
    cfg: comments.CommentConfig,
    monkeypatch: pytest.MonkeyPatch,
    failure: transport.HttpFailure,
    expected_calls: int,
) -> None:
    calls = []
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (calls.append(1), failure))
    assert not comments.download_main(VIDEO_URL, cfg).success
    assert len(calls) == expected_calls


@pytest.mark.parametrize("bad_value", [False, 0, "", {}, "secret"])
def test_malformed_list_is_not_treated_as_an_empty_success(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch, bad_value: Any
) -> None:
    response = _main_page([], is_end=True)
    response["data"]["replies"] = bad_value
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    result = comments.download_main(VIDEO_URL, cfg)
    assert not result.success
    assert "secret" not in str(result.messages)


def test_missing_list_and_string_end_flag_are_protocol_errors(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _main_page([], is_end=True)
    del response["data"]["replies"]
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    assert not comments.download_main(VIDEO_URL, cfg).success
    response["data"]["replies"] = []
    response["data"]["cursor"]["is_end"] = "false"
    assert not comments.download_main(VIDEO_URL, cfg).success


@pytest.mark.parametrize("stage", ["header", "record", "replace", "interrupt"])
def test_atomic_output_preserves_old_file_at_every_failure_stage(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = cfg.output_dir / "测试_标题 [BV1cSec6tEux].comments.json"
    path.write_text("old output", encoding="utf-8")
    response = _main_page([_comment(1)], is_end=True)
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    original = comments._dump

    def fail_write(value: Any, stream: Any) -> None:
        if stage == "header" or (isinstance(value, dict) and "rpid_str" in value):
            if stage == "interrupt":
                raise KeyboardInterrupt
            raise OSError("secret")
        original(value, stream)

    if stage == "replace":

        def fail_replace(*args: Any) -> None:
            raise OSError("secret")

        monkeypatch.setattr(Path, "replace", fail_replace)
    else:
        monkeypatch.setattr(comments, "_dump", fail_write)
    if stage == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            comments.download_main(VIDEO_URL, cfg)
    else:
        result = comments.download_main(VIDEO_URL, cfg)
        assert not result.success and "secret" not in str(result.messages)
    assert path.read_text(encoding="utf-8") == "old output"
    assert list(cfg.output_dir.iterdir()) == [path]


@pytest.mark.parametrize("wrong", ["root", "page", "child"])
def test_thread_rejects_mixed_up_roots_pages_and_children(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch, wrong: str
) -> None:
    response = _thread_page(_comment(100), [_comment(101)], 1)
    if wrong == "root":
        response["data"]["root"] = _comment(999)
    elif wrong == "page":
        response["data"]["page"]["num"] = 2
    else:
        response["data"]["replies"][0]["root_str"] = "999"
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    assert not comments.download_replies(VIDEO_URL, "100", cfg).success


def test_cursor_cycle_does_not_loop_forever_even_with_new_ids(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [_main_page([_comment(i)], offset=o) for i, o in [(1, "a"), (2, "b"), (3, "a")]]
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (pages.pop(0), None))
    result = comments.download_main(VIDEO_URL, cfg)
    assert not result.success and not pages
    assert not list(cfg.output_dir.iterdir())


def test_ctrl_c_returns_130_without_traceback(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    def interrupt(*args: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_main_impl", interrupt)
    assert cli.main(["comments", VIDEO_URL]) == 130
    assert "Traceback" not in capsys.readouterr().err


def test_safe_retry_regenerates_signature(cfg: comments.CommentConfig, monkeypatch: Any) -> None:
    signed = []
    requested = []

    def sign(params: Any, *keys: Any) -> Any:
        signed.append(1)
        return dict(params, wts=str(len(signed)), w_rid="test")

    def fetch(opener: Any, request: Any, **kwargs: Any) -> Any:
        requested.append(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query))
        if len(requested) == 1:
            return None, transport.HttpFailure("network")
        return _main_page([], is_end=True), None

    monkeypatch.setattr(commentapi.wbi, "sign", sign)
    monkeypatch.setattr(transport, "fetch_json", fetch)
    assert comments.download_main(VIDEO_URL, cfg).success
    assert [q["wts"] for q in requested] == [["1"], ["2"]]


def test_persistent_signature_rejection_does_not_loop(
    cfg: comments.CommentConfig, monkeypatch: Any
) -> None:
    key_calls = []
    request_calls = []

    def keys(opener: Any) -> Any:
        key_calls.append(1)
        return (IMG_KEY, SUB_KEY), None

    def fetch(*args: Any, **kwargs: Any) -> Any:
        request_calls.append(1)
        return {"code": -403}, None

    monkeypatch.setattr(commentapi.wbi, "fetch_keys", keys)
    monkeypatch.setattr(transport, "fetch_json", fetch)
    assert not comments.download_main(VIDEO_URL, cfg).success
    assert len(key_calls) == len(request_calls) == 2


@pytest.mark.parametrize("thread", [False, True])
def test_empty_null_page_produces_a_valid_document(
    cfg: comments.CommentConfig, monkeypatch: Any, thread: bool
) -> None:
    response = _thread_page(_comment(100), [], 0) if thread else _main_page([], is_end=True)
    response["data"]["replies"] = None
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    payload = _load_result(
        comments.download_replies(VIDEO_URL, "100", cfg)
        if thread
        else comments.download_main(VIDEO_URL, cfg)
    )
    assert payload["result"]["fetched_count"] == int(thread)
    assert payload["result"]["complete"]


def _rich_comment(rpid: int) -> dict[str, Any]:
    """A comment carrying the full protocol noise seen in real exports."""
    base = _comment(rpid)
    base["member"].update(
        {
            "avatar_item": {"layers": [{"visible": True, "general_spec": {"x": 1}}]},
            "vip": {"vipType": 2, "label": {"img_label_uri_hans_static": "https://x/vip.png"}},
            "user_sailing": {"pendant": {"id": 1, "image": "https://x/p.png"}},
            "nameplate": {"name": "勋章", "image": "https://x/n.png"},
            "pendant": {"pid": 56392, "image_enhance": "https://x/e.png"},
            "avatar": "https://x/face.jpg",
            "sign": "个性签名",
            "sex": "保密",
            "level_info": {"current_level": 6},
            "official_verify": {"type": 0, "desc": "bilibili 知名UP主"},
        }
    )
    base["content"].update(
        {
            "pictures": [
                {"img_src": "https://x/pic1.jpg", "img_width": 100},
                {"img_src": "", "img_width": 1},
                "junk",
            ],
            "emote": {"[doge]": {"url": "https://x/e.png"}},
            "members": [{"mid": "1", "uname": "at某人"}],
        }
    )
    base["up_action"] = {"like": True, "reply": False}
    base["reply_control"] = {"location": "IP属地：北京", "sub_reply_entry_text": "共3条回复"}
    base["track_info"] = {"x": 1}
    base["folder"] = {"has_folded": False}
    return base


def test_lean_projection_keeps_reading_fields_and_drops_protocol_noise(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _main_page([_rich_comment(1)], is_end=True)
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    payload = _load_result(comments.download_main(VIDEO_URL, cfg))

    assert payload["schema_version"] == 2
    assert payload["request"]["fields"] == "lean"
    item = payload["comments"][0]
    assert set(item) == {
        "rpid_str",
        "root_str",
        "parent_str",
        "ctime",
        "time",
        "like",
        "rcount",
        "member",
        "content",
        "up_liked",
        "location",
    }
    assert item["member"] == {
        "mid": "1",
        "uname": "用户1",
        "level": 6,
        "official": "bilibili 知名UP主",
    }
    assert item["content"] == {"message": "正文", "pictures": ["https://x/pic1.jpg"]}
    assert item["up_liked"] is True
    assert item["location"] == "IP属地：北京"
    assert item["time"] == datetime.fromtimestamp(item["ctime"]).strftime("%Y-%m-%d %H:%M")
    dumped = json.dumps(item, ensure_ascii=False)
    for noise in ("avatar_item", "vip", "sailing", "nameplate", "face.jpg", "track_info", "folder"):
        assert noise not in dumped


def test_full_mode_preserves_raw_objects(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, full=True)
    response = _main_page([_rich_comment(1)], is_end=True)
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (response, None))
    payload = _load_result(comments.download_main(VIDEO_URL, cfg))

    assert payload["schema_version"] == 2
    assert payload["request"]["fields"] == "full"
    item = payload["comments"][0]
    assert item["member"]["avatar_item"]["layers"][0]["visible"] is True
    assert item["reply_control"]["location"] == "IP属地：北京"
    assert item["ctime"] == 1700000001
    assert "time" not in item and "up_liked" not in item


def test_pinned_flag_is_inline_in_lean_but_not_in_full(
    cfg: comments.CommentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _main_page([_comment(2)], top=[_comment(1, "置顶")], is_end=True)
    monkeypatch.setattr(transport, "fetch_json", lambda *a, **kw: (page, None))
    payload = _load_result(comments.download_main(VIDEO_URL, cfg))
    assert payload["comments"][0]["pinned"] is True
    assert "pinned" not in payload["comments"][1]
    assert payload["result"]["pinned_ids"] == ["1"]

    full_cfg = comments.CommentConfig(cfg.output_dir, cfg.cookie_path, full=True)
    payload = _load_result(comments.download_main(VIDEO_URL, full_cfg))
    assert all("pinned" not in item for item in payload["comments"])
    assert payload["result"]["pinned_ids"] == ["1"]


def test_comments_cli_forwards_full_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[comments.CommentConfig] = []
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(
        cli.comments,
        "download_main",
        lambda url, cfg: captured.append(cfg) or DownloadResult(True),
    )
    assert cli.main(["comments", VIDEO_URL, "--full"]) == 0
    assert captured[0].full is True
