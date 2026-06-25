import importlib


def _load_scraper(monkeypatch, *, login_is_edge: bool, running_in_docker: bool):
    monkeypatch.setenv("LOGIN_IS_EDGE", "true" if login_is_edge else "false")
    monkeypatch.setenv("RUNNING_IN_DOCKER", "true" if running_in_docker else "false")

    import src.config as config_module
    import src.scraper as scraper_module

    importlib.reload(config_module)
    reloaded_scraper = importlib.reload(scraper_module)
    reloaded_scraper.EDGE_DOCKER_WARNING_PRINTED = False
    return reloaded_scraper


def test_resolve_browser_channel_uses_chromium_in_docker_even_when_edge_requested(monkeypatch, capsys):
    scraper = _load_scraper(monkeypatch, login_is_edge=True, running_in_docker=True)

    assert scraper._resolve_browser_channel() == "chromium"
    assert "Docker 镜像未内置 Edge" in capsys.readouterr().out


def test_resolve_browser_channel_uses_msedge_locally_when_requested(monkeypatch):
    scraper = _load_scraper(monkeypatch, login_is_edge=True, running_in_docker=False)

    assert scraper._resolve_browser_channel() == "msedge"


def test_build_extra_headers_filters_request_specific_snapshot_headers(monkeypatch):
    scraper = _load_scraper(monkeypatch, login_is_edge=False, running_in_docker=False)

    headers = scraper._build_extra_headers(
        {
            "Accept": "text/html",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.goofish.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "snapshot UA",
            "sec-ch-ua": '"Chromium";v="131"',
            "X-Debug-Header": "keep-me",
        }
    )

    assert headers == {
        "Accept-Language": "zh-CN,zh;q=0.9",
        "X-Debug-Header": "keep-me",
    }
