"""WBI key extraction and request signing for Bilibili content APIs."""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, Optional

from . import transport
from .config import NAV_API

MIXIN_KEY_ENC_TAB = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)

_KEY_PATTERN = re.compile(r"[0-9a-fA-F]{32}")
_FILTER_PATTERN = re.compile(r"[!'()*]")


class WbiError(ValueError):
    """Raised when Bilibili returns unusable WBI key data."""


def mixin_key(img_key: str, sub_key: str) -> str:
    """Derive the 32-character signing key from the two nav keys."""
    source = img_key + sub_key
    if not _KEY_PATTERN.fullmatch(img_key) or not _KEY_PATTERN.fullmatch(sub_key):
        raise WbiError("WBI key 格式无效")
    return "".join(source[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def _key_from_url(value: object) -> str:
    if not isinstance(value, str):
        raise WbiError("WBI key URL 缺失")
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname or ""
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError
    except ValueError:
        raise WbiError("WBI key URL 格式无效") from None
    if parsed.scheme != "https" or not (hostname == "hdslb.com" or hostname.endswith(".hdslb.com")):
        raise WbiError("WBI key URL 来源无效")
    key = PurePosixPath(parsed.path).stem
    if not _KEY_PATTERN.fullmatch(key):
        raise WbiError("WBI key 格式无效")
    return key


def extract_keys(payload: dict[str, Any]) -> tuple[str, str]:
    """Extract and validate the WBI image keys from a nav response."""
    if type(payload.get("code")) is not int or payload["code"] not in {0, -101}:
        raise WbiError("nav 接口未返回有效 WBI 数据")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise WbiError("nav 接口缺少 data")
    wbi_img = data.get("wbi_img")
    if not isinstance(wbi_img, dict):
        raise WbiError("nav 接口缺少 wbi_img")
    return _key_from_url(wbi_img.get("img_url")), _key_from_url(wbi_img.get("sub_url"))


def sign(
    params: Mapping[str, str | int],
    img_key: str,
    sub_key: str,
    *,
    timestamp: Optional[int] = None,
) -> dict[str, str]:
    """Return a signed copy of query parameters without mutating the input."""
    if any(type(value) not in {str, int} for value in params.values()):
        raise WbiError("WBI 参数值必须是字符串或整数")
    if timestamp is not None and (type(timestamp) is not int or timestamp < 0):
        raise WbiError("WBI 时间戳必须是非负整数")
    signed = {
        key: _FILTER_PATTERN.sub("", str(value))
        for key, value in params.items()
        if key not in {"w_rid", "wts"}
    }
    signed["wts"] = str(int(time.time()) if timestamp is None else timestamp)
    query = urllib.parse.urlencode(sorted(signed.items()), quote_via=urllib.parse.quote)
    signed["w_rid"] = hashlib.md5(
        (query + mixin_key(img_key, sub_key)).encode(), usedforsecurity=False
    ).hexdigest()
    return signed


def fetch_keys(
    opener: urllib.request.OpenerDirector,
) -> tuple[Optional[tuple[str, str]], Optional[transport.HttpFailure]]:
    """Fetch current WBI keys through the shared HTTP transport boundary."""
    payload, failure = transport.fetch_json(opener, transport.request(NAV_API))
    if payload is None:
        return None, failure or transport.HttpFailure("bad_data")
    try:
        return extract_keys(payload), None
    except WbiError:
        return None, transport.HttpFailure("bad_data")
