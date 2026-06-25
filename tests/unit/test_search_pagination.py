import asyncio

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.services.search_pagination import advance_search_page
from src.services.search_pagination import is_search_results_response
from src.services.search_pagination import wait_for_initial_search_response


class FakeRequest:
    def __init__(self, method: str = "POST", failure=None):
        self.method = method
        self.failure = failure


class FakeResponse:
    def __init__(self, url: str, ok: bool = True, method: str = "POST", status: int = 200):
        self.url = url
        self.ok = ok
        self.status = status
        self.request = FakeRequest(method)


class FakeLocator:
    def __init__(self, count: int, click_error: Exception | None = None):
        self._count = count
        self.clicks = 0
        self.scrolls = 0
        self.click_timeout = None
        self._click_error = click_error

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return self._count

    async def scroll_into_view_if_needed(self) -> None:
        self.scrolls += 1

    async def click(self, timeout: int | None = None) -> None:
        self.clicks += 1
        self.click_timeout = timeout
        if self._click_error is not None:
            raise self._click_error


class FakeResponseContext:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    @property
    def value(self):
        return self._resolve()

    async def _resolve(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class FakePage:
    def __init__(
        self,
        next_button_count: int,
        outcomes: list[object],
        click_error: Exception | None = None,
        expected_timeout: int = 20000,
        goto_events: list[tuple[str, object]] | None = None,
    ):
        self.locator_stub = FakeLocator(next_button_count, click_error=click_error)
        self._outcomes = list(outcomes)
        self._expected_timeout = expected_timeout
        self.goto_calls: list[dict] = []
        self._goto_events = list(goto_events or [])
        self._handlers: dict[str, list] = {}
        self.url = "about:blank"

    def locator(self, _selector: str) -> FakeLocator:
        return self.locator_stub

    def expect_response(self, _predicate, timeout: int):
        assert timeout == self._expected_timeout
        if not self._outcomes:
            raise AssertionError("missing fake response outcome")
        return FakeResponseContext(self._outcomes.pop(0))

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        if event not in self._handlers:
            return
        self._handlers[event] = [item for item in self._handlers[event] if item is not handler]

    async def goto(
        self,
        url: str,
        wait_until: str,
        timeout: int,
    ) -> None:
        self.url = url
        self.goto_calls.append(
            {"url": url, "wait_until": wait_until, "timeout": timeout}
        )
        if self._goto_events:
            event, payload = self._goto_events.pop(0)
            for handler in self._handlers.get(event, []):
                handler(payload)

    async def title(self) -> str:
        return "Fake Search Page"


async def _noop_random_sleep(_min_seconds: float, _max_seconds: float) -> None:
    return None


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_advance_search_page_stops_when_no_next_button() -> None:
    page = FakePage(next_button_count=0, outcomes=[])
    logs: list[str] = []

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=logs.append,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is False
    assert result.response is None
    assert result.stop_reason == "no_next_button"
    assert page.locator_stub.clicks == 0
    assert logs == ["已到达最后一页，未找到可用的'下一页'按钮，停止翻页。"]


def test_advance_search_page_stops_after_timeout_retries() -> None:
    page = FakePage(
        next_button_count=1,
        outcomes=[
            PlaywrightTimeoutError("page 2 timeout"),
            PlaywrightTimeoutError("page 2 timeout"),
        ],
    )
    logs: list[str] = []

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=logs.append,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is False
    assert result.response is None
    assert result.stop_reason == "response_timeout"
    assert page.locator_stub.clicks == 2
    assert page.locator_stub.scrolls == 2
    assert logs == [
        "等待第 2 页搜索响应超时，5秒后重试...",
        "等待第 2 页搜索响应超时 2 次，停止翻页。",
    ]


def test_advance_search_page_returns_new_response_on_success() -> None:
    response = FakeResponse(
        url="https://example.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/?page=2"
    )
    page = FakePage(next_button_count=1, outcomes=[response])

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=lambda _message: None,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is True
    assert result.response is response
    assert result.stop_reason is None
    assert page.locator_stub.clicks == 1
    assert page.locator_stub.scrolls == 1
    assert page.locator_stub.click_timeout == 10000


def test_advance_search_page_stops_when_click_times_out() -> None:
    page = FakePage(
        next_button_count=1,
        outcomes=[FakeResponse(url="https://example.com/unused")],
        click_error=PlaywrightTimeoutError("click timeout"),
    )
    logs: list[str] = []

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=logs.append,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is False
    assert result.response is None
    assert result.stop_reason == "click_timeout"
    assert page.locator_stub.clicks == 1
    assert logs == ["第 2 页下一页按钮点击超时，停止翻页。"]


def test_wait_for_initial_search_response_retries_after_timeout() -> None:
    response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/?foo=bar"
    )
    page = FakePage(
        next_button_count=0,
        outcomes=[PlaywrightTimeoutError("initial search timeout"), response],
        expected_timeout=30000,
    )
    logs: list[str] = []

    result = asyncio.run(
        wait_for_initial_search_response(
            page=page,
            search_url="https://www.goofish.com/search?q=mac+m1pro",
            logger=logs.append,
            retry_sleep=_noop_sleep,
        )
    )

    assert result is response
    assert [call["url"] for call in page.goto_calls] == [
        "https://www.goofish.com/search?q=mac+m1pro",
        "https://www.goofish.com/search?q=mac+m1pro",
    ]
    assert logs == [
        "等待初始搜索响应超时，当前页面: https://www.goofish.com/search?q=mac+m1pro，5秒后重试..."
    ]


def test_wait_for_initial_search_response_logs_network_diagnostics_on_final_timeout() -> None:
    observed_response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.searchv2/1.0/?foo=bar",
        method="POST",
        status=200,
    )
    page = FakePage(
        next_button_count=0,
        outcomes=[
            PlaywrightTimeoutError("initial search timeout"),
            PlaywrightTimeoutError("initial search timeout"),
        ],
        expected_timeout=30000,
        goto_events=[("response", observed_response)],
    )
    logs: list[str] = []

    try:
        asyncio.run(
            wait_for_initial_search_response(
                page=page,
                search_url="https://www.goofish.com/search?q=mac+m1pro",
                logger=logs.append,
                retry_sleep=_noop_sleep,
            )
        )
    except PlaywrightTimeoutError:
        pass

    assert "初始搜索诊断: title=Fake Search Page url=https://www.goofish.com/search?q=mac+m1pro" in logs
    assert "初始搜索阶段最近网络事件:" in logs
    assert (
        "  response POST 200 "
        "https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.searchv2/1.0/?foo=bar"
    ) in logs


def test_wait_for_initial_search_response_logs_request_failure_text() -> None:
    failed_request = FakeRequest(
        method="GET",
        failure="net::ERR_FAILED",
    )
    failed_request.url = "https://g.alicdn.com/mtb/lib-mtop/2.7.3/mtop.js"
    page = FakePage(
        next_button_count=0,
        outcomes=[
            PlaywrightTimeoutError("initial search timeout"),
            PlaywrightTimeoutError("initial search timeout"),
        ],
        expected_timeout=30000,
        goto_events=[("requestfailed", failed_request)],
    )
    logs: list[str] = []

    try:
        asyncio.run(
            wait_for_initial_search_response(
                page=page,
                search_url="https://www.goofish.com/search?q=mac+m1pro",
                logger=logs.append,
                retry_sleep=_noop_sleep,
            )
        )
    except PlaywrightTimeoutError:
        pass

    assert (
        "  requestfailed GET https://g.alicdn.com/mtb/lib-mtop/2.7.3/mtop.js "
        "error=net::ERR_FAILED"
    ) in logs


def test_is_search_results_response_matches_exact_search_api() -> None:
    response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/?foo=bar",
        method="POST",
    )

    assert is_search_results_response(response) is True


def test_is_search_results_response_rejects_search_shade_api() -> None:
    response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search.shade/1.0/?foo=bar",
        method="POST",
    )

    assert is_search_results_response(response) is False


def test_is_search_results_response_rejects_non_post_request() -> None:
    response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/?foo=bar",
        method="GET",
    )

    assert is_search_results_response(response) is False
