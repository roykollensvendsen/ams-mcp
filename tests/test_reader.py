# SPDX-License-Identifier: GPL-3.0-or-later
"""The reader is only ever asked to show a page, never to change anything.

AmsToMqttBridge answers GET on its JSON pages without a login, and also takes
POST for saving settings, rebooting and uploading firmware. This plugin may
send nothing but GET, to the seven pages listed, and follows no redirect.
"""

import asyncio
import json

import httpx
import pytest

from ams_mcp import reader
from ams_mcp.reader import PAGES, HttpReader, RefusedPageError


def _reader(handler, **options):
    return HttpReader("192.0.2.10", transport=httpx.MockTransport(handler), **options)


def _ok(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"path": request.url.path})

    return handler


def test_only_an_allow_listed_page_is_ever_asked_for():
    seen = []
    r = _reader(_ok(seen))
    for page in ("reboot", "configuration.json", "save", "upgrade", "wifiscan.json", "../data.json"):
        with pytest.raises(RefusedPageError):
            asyncio.run(r.page(page))
    assert seen == []


def test_the_allow_list_is_the_seven_read_only_pages():
    assert set(PAGES) == {
        "data.json",
        "dayplot.json",
        "monthplot.json",
        "energyprice.json",
        "tariff.json",
        "temperature.json",
        "sysinfo.json",
    }


def test_a_page_is_only_ever_read_with_get():
    seen = []
    r = _reader(_ok(seen))
    for page in PAGES:
        asyncio.run(r.page(page))
    assert [q.method for q in seen] == ["GET"] * len(PAGES)
    assert [q.url.path for q in seen] == [f"/{p}" for p in PAGES]
    assert all(q.content == b"" for q in seen)
    assert all("authorization" not in q.headers for q in seen)


def test_a_redirect_is_never_followed():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "/reboot"})

    with pytest.raises(reader.ReaderError):
        asyncio.run(_reader(handler).page("data.json"))
    assert [q.url.path for q in seen] == ["/data.json"]


def test_a_page_read_moments_ago_is_not_asked_for_again():
    seen, now = [], [100.0]
    r = _reader(_ok(seen), clock=lambda: now[0])
    asyncio.run(r.page("data.json"))
    now[0] += reader.CACHE_FOR - 0.5
    asyncio.run(r.page("data.json"))
    assert len(seen) == 1
    now[0] += 1.0
    asyncio.run(r.page("data.json"))
    assert len(seen) == 2


def test_a_page_that_is_not_json_is_an_error():
    def handler(request):
        return httpx.Response(200, text="<html>login</html>")

    with pytest.raises(reader.ReaderError):
        asyncio.run(_reader(handler).page("data.json"))


def test_a_refused_login_is_an_error_and_never_answered():
    def handler(request):
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="ams"'})

    with pytest.raises(reader.ReaderError):
        asyncio.run(_reader(handler).page("data.json"))


def test_a_reader_that_does_not_answer_is_an_error():
    def handler(request):
        raise httpx.ConnectTimeout("no answer", request=request)

    with pytest.raises(reader.ReaderError):
        asyncio.run(_reader(handler).page("data.json"))


def test_the_answer_says_when_it_was_read():
    r = _reader(lambda q: httpx.Response(200, content=json.dumps({"a": 1})), wall=lambda: 1794402646.0)
    page = asyncio.run(r.page("data.json"))
    assert page.body == {"a": 1}
    assert page.read_at == 1794402646.0
