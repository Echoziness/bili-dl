"""Tests for WBI key extraction and request signing."""

from __future__ import annotations

import hashlib
import urllib.request
from typing import Any

import pytest

from bili_dl import transport, wbi
from bili_dl.config import NAV_API

IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


def _nav_payload() -> dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "wbi_img": {
                "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
            }
        },
    }


def test_mixin_key_matches_reference_vector() -> None:
    assert wbi.mixin_key(IMG_KEY, SUB_KEY) == "ea1db124af3c7062474693fa704f4ff8"


def test_mixin_key_rejects_invalid_keys() -> None:
    with pytest.raises(wbi.WbiError):
        wbi.mixin_key("not-a-key", SUB_KEY)


def test_sign_matches_reference_vector_without_mutating_input() -> None:
    params = {"foo": "114", "bar": "514", "zab": 1919810, "w_rid": "stale"}

    result = wbi.sign(params, IMG_KEY, SUB_KEY, timestamp=1702204169)

    assert result == {
        "foo": "114",
        "bar": "514",
        "zab": "1919810",
        "wts": "1702204169",
        "w_rid": "8f6f2b5b3d485fe1886cec6a0be8c5d4",
    }
    assert params == {"foo": "114", "bar": "514", "zab": 1919810, "w_rid": "stale"}


def test_sign_uses_encode_uri_component_semantics_and_filters_values() -> None:
    result = wbi.sign(
        {"message": "中文 空格!'()*", "wts": 1}, IMG_KEY, SUB_KEY, timestamp=1702204169
    )
    filtered = {"message": "中文 空格", "wts": "1702204169"}
    query = urllib.parse.urlencode(sorted(filtered.items()), quote_via=urllib.parse.quote)

    assert query == "message=%E4%B8%AD%E6%96%87%20%E7%A9%BA%E6%A0%BC&wts=1702204169"
    assert result["message"] == "中文 空格"
    assert (
        result["w_rid"]
        == hashlib.md5((query + wbi.mixin_key(IMG_KEY, SUB_KEY)).encode()).hexdigest()
    )


def test_extract_keys_from_nav_payload() -> None:
    assert wbi.extract_keys(_nav_payload()) == (IMG_KEY, SUB_KEY)


@pytest.mark.parametrize(
    "payload",
    [
        {"code": -1},
        {"code": 0, "data": None},
        {"code": 0, "data": {}},
        {"code": 0, "data": {"wbi_img": {}}},
        {
            "code": 0,
            "data": {
                "wbi_img": {
                    "img_url": f"http://i0.hdslb.com/{IMG_KEY}.png",
                    "sub_url": f"https://i0.hdslb.com/{SUB_KEY}.png",
                }
            },
        },
        {
            "code": 0,
            "data": {
                "wbi_img": {
                    "img_url": f"https://example.com/{IMG_KEY}.png",
                    "sub_url": f"https://i0.hdslb.com/{SUB_KEY}.png",
                }
            },
        },
        {
            "code": 0,
            "data": {
                "wbi_img": {
                    "img_url": "https://i0.hdslb.com/not-a-key.png",
                    "sub_url": f"https://i0.hdslb.com/{SUB_KEY}.png",
                }
            },
        },
        {
            "code": 0,
            "data": {
                "wbi_img": {
                    "img_url": f"https://i0.hdslb.com/{IMG_KEY}.extra.png",
                    "sub_url": f"https://i0.hdslb.com/{SUB_KEY}.png",
                }
            },
        },
    ],
)
def test_extract_keys_rejects_invalid_nav_data(payload: dict[str, Any]) -> None:
    with pytest.raises(wbi.WbiError):
        wbi.extract_keys(payload)


def test_fetch_keys_uses_shared_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = urllib.request.build_opener()
    captured: list[tuple[urllib.request.OpenerDirector, str]] = []

    def fake_fetch_json(
        actual_opener: urllib.request.OpenerDirector,
        request: urllib.request.Request,
        timeout: float = 0,
    ) -> tuple[dict[str, Any], None]:
        captured.append((actual_opener, request.full_url))
        return _nav_payload(), None

    monkeypatch.setattr(wbi.transport, "fetch_json", fake_fetch_json)

    assert wbi.fetch_keys(opener) == ((IMG_KEY, SUB_KEY), None)
    assert captured == [(opener, NAV_API)]


@pytest.mark.parametrize(
    ("payload", "failure", "expected"),
    [
        (None, transport.HttpFailure("network"), transport.HttpFailure("network")),
        (None, None, transport.HttpFailure("bad_data")),
        ({"code": 0, "data": {}}, None, transport.HttpFailure("bad_data")),
    ],
)
def test_fetch_keys_returns_safe_failures(
    payload: dict[str, Any] | None,
    failure: transport.HttpFailure | None,
    expected: transport.HttpFailure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wbi.transport, "fetch_json", lambda *args: (payload, failure))

    assert wbi.fetch_keys(urllib.request.build_opener()) == (None, expected)


@pytest.mark.parametrize(
    "url",
    [
        "https://[secret",
        "https://i0.hdslb.com:secret/key.png",
        f"https://secret@i0.hdslb.com/{IMG_KEY}.png",
    ],
)
def test_invalid_url_cannot_escape_the_safe_failure_boundary(url: str, monkeypatch: Any) -> None:
    payload = _nav_payload()
    payload["data"]["wbi_img"]["img_url"] = url
    monkeypatch.setattr(transport, "fetch_json", lambda *a: (payload, None))
    assert wbi.fetch_keys(urllib.request.build_opener()) == (
        None,
        transport.HttpFailure("bad_data"),
    )


def test_key_characters_are_preserved_and_anonymous_nav_can_supply_keys() -> None:
    payload = _nav_payload()
    payload["code"] = -101
    payload["data"]["wbi_img"]["img_url"] = f"https://i0.hdslb.com/{IMG_KEY.upper()}.png"
    assert wbi.extract_keys(payload) == (IMG_KEY.upper(), SUB_KEY)


def test_sign_rejects_ambiguous_bool_values() -> None:
    with pytest.raises(wbi.WbiError):
        wbi.sign({"foo": True}, IMG_KEY, SUB_KEY)
