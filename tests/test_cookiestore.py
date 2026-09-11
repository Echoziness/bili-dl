"""Tests for cookie validation and ensure_cookie orchestration.

Asserts that:
* ``validate`` returns correct verdicts for missing/empty/no-SESSDATA files;
* ``_nav_probe`` distinguishes network errors from HTTP errors (AGENTS.md §2.6);
* ``validate`` reports the *real* error cause (风控 vs 网络) on degradation;
* ``ensure_cookie`` orchestrates validate → import → re-validate correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bili_dl import cookiestore as store

DATA = Path(__file__).parent / "data" / "sample_cookies_all.txt"


def _make_cookie_dir(tmp_path: Path) -> Path:
    """Create a cookie dir with a valid SESSDATA file for validate tests."""
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tabc%2C123\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\tcsrf\n",
        encoding="utf-8",
    )
    return tmp_path


def test_validate_missing_file(tmp_path: Path) -> None:
    result = store.validate(tmp_path)
    assert result.valid is False
    assert len(result.messages) == 0


def test_validate_empty_file(tmp_path: Path) -> None:
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("", encoding="utf-8")
    result = store.validate(tmp_path)
    assert result.valid is False


def test_validate_no_sessdata(tmp_path: Path) -> None:
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tother_cookie\tval\n", encoding="utf-8")
    result = store.validate(tmp_path)
    assert result.valid is False


def test_ensure_cookie_imports_from_source(tmp_path: Path) -> None:
    """ensure_cookie: no output file → import from source → re-validate."""
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    result = store.ensure_cookie(tmp_path)
    assert isinstance(result.ready, bool)
    assert len(result.messages) > 0


def test_ensure_cookie_no_source(tmp_path: Path) -> None:
    """ensure_cookie: no source file → not ready, no crash."""
    result = store.ensure_cookie(tmp_path)
    assert result.ready is False


# ─── _nav_probe: shared transport integration ──────────────────────────────


def test_nav_probe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        store.transport,
        "fetch_json",
        lambda opener, request: (
            {"code": 0, "data": {"isLogin": True, "uname": "alice"}},
            None,
        ),
    )
    data, error = store._nav_probe("sess")
    assert data is not None
    assert error is None
    assert data["data"]["uname"] == "alice"


def test_nav_probe_passes_proxy_to_shared_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str | None] = []
    real_cookie_opener = store.transport.cookie_opener
    monkeypatch.setattr(
        store.transport,
        "cookie_opener",
        lambda jar=None, proxy=None: captured.append(proxy) or real_cookie_opener(jar, proxy),
    )
    monkeypatch.setattr(
        store.transport,
        "fetch_json",
        lambda opener, request: (None, store.transport.HttpFailure("http", 412)),
    )
    data, error = store._nav_probe("sess", proxy="http://proxy:7890")
    assert data is None
    assert error == store.transport.HttpFailure("http", 412)
    assert captured == ["http://proxy:7890"]


# ─── validate: mock _nav_probe to test message precision ────────────────────


def test_validate_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s, proxy=None: ({"code": 0, "data": {"isLogin": True, "uname": "bob"}}, None),
    )
    result = store.validate(d)
    assert result.valid is True
    assert result.uname == "bob"


def test_validate_not_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store, "_nav_probe", lambda s, proxy=None: ({"code": -101, "data": {}}, None)
    )
    result = store.validate(d)
    assert result.valid is False


def test_validate_degrades_on_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s, proxy=None: (None, store.transport.HttpFailure("network")),
    )
    result = store.validate(d)
    assert result.valid is True
    assert any("网络" in t for _, t in result.messages)


def test_validate_degrades_on_http_error_with_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s, proxy=None: (None, store.transport.HttpFailure("http", 412)),
    )
    result = store.validate(d)
    assert result.valid is True
    assert any("HTTP 412" in t for _, t in result.messages)


def test_validate_degrades_on_badjson(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B站 returns non-JSON → report 'non-JSON', not '网络/SSL' (§2.20)."""
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s, proxy=None: (None, store.transport.HttpFailure("bad_json")),
    )
    result = store.validate(d)
    assert result.valid is True
    assert any("非 JSON" in t for _, t in result.messages)
    assert not any("网络" in t for _, t in result.messages)


# ─── _extract_sessdata: precise field matching (§2.6 hardening) ──────────────


def test_extract_sessdata_www_domain(tmp_path: Path) -> None:
    """www.bilibili.com domain contains 'bilibili.com' → SESSDATA extracted."""
    lines = ["www.bilibili.com\tFALSE\t/\tFALSE\t0\tSESSDATA\twww_sess"]
    assert store._extract_sessdata(lines) == "www_sess"


def test_extract_sessdata_other_domain_dropped() -> None:
    """A SESSDATA-named cookie on a non-bilibili domain must not be returned."""
    lines = [".example.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tevil"]
    assert store._extract_sessdata(lines) is None


def test_extract_sessdata_wrong_name() -> None:
    """A bilibili line whose name column is not 'SESSDATA' is skipped."""
    lines = [".bilibili.com\tTRUE\t/\tFALSE\t0\tother_cookie\tval"]
    assert store._extract_sessdata(lines) is None


def test_extract_sessdata_short_line() -> None:
    """Lines with fewer than 7 fields can't carry a value → None."""
    lines = [".bilibili.com\tTRUE\t/"]
    assert store._extract_sessdata(lines) is None


def test_validate_logged_in_no_uname(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """isLogin True but uname missing → falls back to '?' rather than KeyError."""
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s, proxy=None: ({"code": 0, "data": {"isLogin": True}}, None),
    )
    result = store.validate(d)
    assert result.valid is True
    assert result.uname == "?"


def test_store_qr_cookie_validates_before_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unverified QR session must never overwrite a pre-existing cookie file."""
    old = store.bili_cookie_path(tmp_path)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("old-cookie\n", encoding="utf-8")
    monkeypatch.setattr(
        store, "_nav_probe", lambda s, proxy=None: ({"code": -101, "data": {}}, None)
    )

    result = store.store_qr_cookie([".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tnew"], tmp_path)

    assert result.success is False
    assert old.read_text(encoding="utf-8") == "old-cookie\n"


def test_store_qr_cookie_replaces_after_online_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s, proxy=None: (
            {"code": 0, "data": {"isLogin": True, "uname": "qr-user"}},
            None,
        ),
    )

    result = store.store_qr_cookie(
        ["# Netscape HTTP Cookie File", ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tnew"],
        tmp_path,
    )

    assert result.success is True
    assert "SESSDATA\tnew" in store.bili_cookie_path(tmp_path).read_text(encoding="utf-8")
    assert any("qr-user" in text for _, text in result.messages)


def test_store_qr_session_saves_refresh_token_after_cookie_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authstate

    monkeypatch.setattr(
        store,
        "store_qr_cookie",
        lambda lines, cookie_dir, proxy: store.QrStoreResult(True, [("ok", "stored")]),
    )
    saved: list[authstate.AuthState] = []
    monkeypatch.setattr(authstate, "save", lambda state, cookie_dir: saved.append(state) or None)

    lines = [
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsession",
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tbili_jct\tcsrf",
    ]
    result = store.store_qr_session(lines, "refresh-token", tmp_path)

    assert result.success is True
    fingerprint = store._session_fingerprint(lines)
    assert fingerprint is not None
    assert saved == [authstate.AuthState("refresh-token", fingerprint)]
    assert any("每日会话续期" in text for _, text in result.messages)


def test_store_qr_session_without_token_removes_stale_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authstate

    authstate.path(tmp_path).write_text("stale", encoding="utf-8")
    monkeypatch.setattr(
        store,
        "store_qr_cookie",
        lambda lines, cookie_dir, proxy: store.QrStoreResult(True, [("ok", "stored")]),
    )

    result = store.store_qr_session(
        [".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tnew-session"], None, tmp_path
    )

    assert result.success is True
    assert not authstate.path(tmp_path).exists()
    assert any("未返回续期凭证" in text for _, text in result.messages)


def test_store_qr_session_without_csrf_does_not_enable_renewal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authstate

    authstate.path(tmp_path).write_text("stale", encoding="utf-8")
    monkeypatch.setattr(
        store,
        "store_qr_cookie",
        lambda lines, cookie_dir, proxy: store.QrStoreResult(True, [("ok", "stored")]),
    )

    result = store.store_qr_session(
        [".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tnew-session"],
        "refresh-token",
        tmp_path,
    )

    assert result.success is True
    assert not authstate.path(tmp_path).exists()
    assert any("缺少 bili_jct" in text for _, text in result.messages)


def test_renewal_state_requires_csrf_for_the_bound_session(tmp_path: Path) -> None:
    from bili_dl import authstate

    lines = [".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsession"]
    store.bili_cookie_path(tmp_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    fingerprint = store._session_fingerprint(lines)
    assert fingerprint is not None
    assert authstate.save(authstate.AuthState("token", fingerprint), tmp_path) is None

    state, error = store.renewal_state(tmp_path)

    assert state is None
    assert error == "当前 Cookie 缺少 bili_jct，无法自动续期"


def test_store_qr_session_state_write_failure_removes_stale_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authstate

    authstate.path(tmp_path).write_text("stale", encoding="utf-8")
    monkeypatch.setattr(
        store,
        "store_qr_cookie",
        lambda lines, cookie_dir, proxy: store.QrStoreResult(True, [("ok", "stored")]),
    )
    monkeypatch.setattr(authstate, "save", lambda state, cookie_dir: "无法保存刷新凭证状态")

    result = store.store_qr_session(
        [
            ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tnew-session",
            ".bilibili.com\tTRUE\t/\tTRUE\t0\tbili_jct\tcsrf",
        ],
        "new-token",
        tmp_path,
    )

    assert result.success is True
    assert not authstate.path(tmp_path).exists()
    assert any("自动续期不可用" in text for _, text in result.messages)


def test_store_qr_session_refuses_a_concurrent_state_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "AUTH_LOCK_TIMEOUT", 0)

    with store._auth_lock(tmp_path):
        result = store.store_qr_session([], "token", tmp_path)

    assert result.success is False
    assert any("正在进行" in text for _, text in result.messages)


def test_renew_if_due_skips_when_another_process_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "AUTH_LOCK_TIMEOUT", 0)

    with store._auth_lock(tmp_path):
        messages = store._renew_if_due(tmp_path)

    assert any("跳过自动续期" in text for _, text in messages)


def test_renewal_state_rejects_token_from_another_cookie_session(tmp_path: Path) -> None:
    from bili_dl import authstate

    _make_cookie_dir(tmp_path)
    assert authstate.save(authstate.AuthState("token", "wrong-fingerprint"), tmp_path) is None

    state, error = store.renewal_state(tmp_path)

    assert state is None
    assert error is not None and "不属于当前 Cookie 会话" in error


def test_renew_if_due_never_sends_a_mismatched_refresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authrefresh, authstate

    _make_cookie_dir(tmp_path)
    assert (
        authstate.save(authstate.AuthState("must-not-send", "wrong-fingerprint"), tmp_path) is None
    )

    def fail_if_called(path: Path, token: str, proxy: str | None) -> authrefresh.RenewalResult:
        raise AssertionError("mismatched refresh token was sent")

    monkeypatch.setattr(authrefresh, "check_and_refresh", fail_if_called)

    messages = store._renew_if_due(tmp_path)

    assert any("不属于当前 Cookie 会话" in text for _, text in messages)


def test_renew_if_due_records_successful_daily_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authrefresh, authstate

    monkeypatch.setattr(store, "_utc_today", lambda: "2026-09-11")
    monkeypatch.setattr(
        store,
        "renewal_state",
        lambda cookie_dir: (authstate.AuthState("token", "fingerprint"), None),
    )
    proxies: list[str | None] = []
    monkeypatch.setattr(
        authrefresh,
        "check_and_refresh",
        lambda path, token, proxy: proxies.append(proxy) or authrefresh.RenewalResult(checked=True),
    )
    saved: list[authstate.AuthState] = []
    monkeypatch.setattr(authstate, "save", lambda state, cookie_dir: saved.append(state) or None)

    assert store._renew_if_due(tmp_path, proxy="http://renew:7890") == []
    assert saved == [authstate.AuthState("token", "fingerprint", "2026-09-11")]
    assert proxies == ["http://renew:7890"]


def test_renew_if_due_persists_new_session_before_confirming_old_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authrefresh, authstate

    monkeypatch.setattr(store, "_utc_today", lambda: "2026-09-11")
    monkeypatch.setattr(
        store,
        "renewal_state",
        lambda cookie_dir: (authstate.AuthState("old", "old-fingerprint"), None),
    )
    renewal = authrefresh.RenewalResult(
        checked=True,
        refreshed=True,
        cookie_lines=[".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tnew"],
        refresh_token="new",
        old_refresh_token="old",
    )
    monkeypatch.setattr(authrefresh, "check_and_refresh", lambda path, token, proxy: renewal)
    monkeypatch.setattr(
        store,
        "_store_verified_cookie",
        lambda lines, cookie_dir, message, proxy: store.QrStoreResult(True, [("ok", "new-cookie")]),
    )
    saved: list[authstate.AuthState] = []
    monkeypatch.setattr(authstate, "save", lambda state, cookie_dir: saved.append(state) or None)
    confirmed = [False]
    monkeypatch.setattr(
        authrefresh, "confirm", lambda result: confirmed.__setitem__(0, True) or None
    )

    result = store._renew_if_due(tmp_path)

    expected_pending = authstate.AuthState(
        "new",
        store._session_fingerprint(renewal.cookie_lines) or "",
        "2026-09-11",
        pending_confirm_token="old",
    )
    expected_complete = authstate.AuthState(
        "new",
        store._session_fingerprint(renewal.cookie_lines) or "",
        "2026-09-11",
    )
    assert saved == [expected_pending, expected_complete]
    assert confirmed == [True]
    assert ("ok", "new-cookie") in result


def test_renew_if_due_keeps_pending_confirmation_after_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authrefresh, authstate

    monkeypatch.setattr(store, "_utc_today", lambda: "2026-09-11")
    monkeypatch.setattr(
        store,
        "renewal_state",
        lambda cookie_dir: (authstate.AuthState("old", "old-fingerprint"), None),
    )
    renewal = authrefresh.RenewalResult(
        checked=True,
        refreshed=True,
        cookie_lines=[".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tnew"],
        refresh_token="new",
        old_refresh_token="old",
    )
    monkeypatch.setattr(authrefresh, "check_and_refresh", lambda path, token, proxy: renewal)
    monkeypatch.setattr(
        store,
        "_store_verified_cookie",
        lambda lines, cookie_dir, message, proxy: store.QrStoreResult(True),
    )
    saved: list[authstate.AuthState] = []
    monkeypatch.setattr(authstate, "save", lambda state, cookie_dir: saved.append(state) or None)
    monkeypatch.setattr(authrefresh, "confirm", lambda result: "网络连接失败")

    messages = store._renew_if_due(tmp_path)

    assert len(saved) == 1
    assert saved[0].pending_confirm_token == "old"
    assert any("无法确认旧续期凭证" in text for _, text in messages)


def test_renew_if_due_retries_persisted_confirmation_before_daily_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bili_dl import authrefresh, authstate

    state = authstate.AuthState("new", "fingerprint", "2026-09-11", pending_confirm_token="old")
    monkeypatch.setattr(store, "_utc_today", lambda: "2026-09-11")
    monkeypatch.setattr(store, "renewal_state", lambda cookie_dir: (state, None))
    confirmed: list[str] = []
    monkeypatch.setattr(
        authrefresh,
        "confirm_pending",
        lambda path, token, proxy: confirmed.append(token) or None,
    )
    saved: list[authstate.AuthState] = []
    monkeypatch.setattr(authstate, "save", lambda value, cookie_dir: saved.append(value) or None)
    monkeypatch.setattr(
        authrefresh,
        "check_and_refresh",
        lambda path, token, proxy: pytest.fail("daily check must stay throttled"),
    )

    messages = store._renew_if_due(tmp_path)

    assert confirmed == ["old"]
    assert saved == [authstate.AuthState("new", "fingerprint", "2026-09-11")]
    assert any("已完成上次会话续期确认" in text for _, text in messages)


# ─── ensure_cookie: orchestration branches ───────────────────────────────────


def test_ensure_cookie_already_valid_skips_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the existing cookie validates online, import is never called."""
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s, proxy=None: (
            {"code": 0, "data": {"isLogin": True, "uname": "u"}},
            None,
        ),
    )
    from bili_dl.cookiesource import ImportResult

    import_calls: list[Path] = []

    def fake_import(cd: Path | None, dest: Path | None = None) -> ImportResult:
        import_calls.append(cd or Path())
        return ImportResult(success=True)

    monkeypatch.setattr(store, "import_cookie", fake_import)
    result = store.ensure_cookie(d)
    assert result.ready is True
    assert import_calls == []  # validate succeeded → no import


def test_ensure_cookie_import_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """validate fails → source found → import fails → ready False."""
    src = tmp_path / "s.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    from bili_dl.cookiesource import ImportResult

    monkeypatch.setattr(
        store,
        "import_cookie",
        lambda cd, dest=None: ImportResult(success=False, messages=[("error", "bad")]),
    )
    result = store.ensure_cookie(tmp_path)
    assert result.ready is False
