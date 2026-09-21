"""Shared video reference validation before authenticated requests."""

import urllib.request

import pytest

from bili_dl import transport, video


@pytest.mark.parametrize(
    "url",
    [
        "https://[secret",
        "https://secret@www.bilibili.com/video/BV1Got26ZE5K/",
        "https://www.bilibili.com:secret/video/BV1Got26ZE5K/",
        "av0",
    ],
)
def test_bad_reference_is_rejected_before_requests(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        transport, "fetch_json", lambda *a, **kw: pytest.fail("No request expected")
    )
    opener = urllib.request.build_opener()
    with pytest.raises(video.VideoError) as exc:
        video.resolve(opener, opener, url)
    assert "secret" not in str(exc.value)


def test_av_reference_keeps_part() -> None:
    assert video.parse_reference("https://www.bilibili.com/video/av123/?p=2") == ({"aid": "123"}, 2)
