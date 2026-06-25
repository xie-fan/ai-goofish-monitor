import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.utils import log_time, random_sleep

NEXT_PAGE_SELECTOR = (
    "button[class*='search-pagination-arrow-container']"
    ":has([class*='search-pagination-arrow-right'])"
    ":not([disabled])"
)
SEARCH_RESULTS_API_FRAGMENT = "/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/"
INITIAL_SEARCH_REQUEST_TIMEOUT_MS = 30_000
INITIAL_SEARCH_GOTO_TIMEOUT_MS = 60_000
INITIAL_SEARCH_RETRY_DELAY_SECONDS = 5
INITIAL_SEARCH_RETRY_COUNT = 2
PAGE_REQUEST_TIMEOUT_MS = 20_000
PAGE_CLICK_TIMEOUT_MS = 10_000
PAGE_RETRY_DELAY_SECONDS = 5
PAGE_RETRY_COUNT = 2
PAGE_CLICK_SLEEP_MIN_SECONDS = 2
PAGE_CLICK_SLEEP_MAX_SECONDS = 5
INITIAL_SEARCH_DIAGNOSTIC_KEYWORDS = ("goofish", "mtop", "taobao")
MAX_INITIAL_SEARCH_DIAGNOSTIC_EVENTS = 12


@dataclass(frozen=True)
class PageAdvanceResult:
    advanced: bool
    response: Optional[Any] = None
    stop_reason: Optional[str] = None


class InitialSearchDiagnostics:
    """Collects recent page network events for initial search timeout logs."""

    def __init__(self, max_events: int = MAX_INITIAL_SEARCH_DIAGNOSTIC_EVENTS):
        self.max_events = max(1, max_events)
        self.events: list[str] = []

    def record_response(self, response: Any) -> None:
        url = _event_url(response)
        if not _is_relevant_initial_search_event(url):
            return
        request = getattr(response, "request", None)
        method = getattr(request, "method", "-")
        status = getattr(response, "status", "-")
        self._append(f"response {method} {status} {_truncate(url)}")

    def record_request_failed(self, request: Any) -> None:
        url = _event_url(request)
        if not _is_relevant_initial_search_event(url):
            return
        method = getattr(request, "method", "-")
        failure = getattr(request, "failure", None)
        error_text = ""
        if callable(failure):
            try:
                failure_value = failure()
                if isinstance(failure_value, dict):
                    error_text = str(failure_value.get("errorText") or "")
            except Exception:
                error_text = ""
        elif isinstance(failure, str):
            error_text = failure
        suffix = f" error={_truncate(error_text, 120)}" if error_text else ""
        self._append(f"requestfailed {method} {_truncate(url)}{suffix}")

    def _append(self, event: str) -> None:
        self.events.append(event)
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events :]


def _event_url(event: Any) -> str:
    return str(getattr(event, "url", "") or "")


def _truncate(value: str, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _is_relevant_initial_search_event(url: str) -> bool:
    lowered = str(url or "").lower()
    return any(keyword in lowered for keyword in INITIAL_SEARCH_DIAGNOSTIC_KEYWORDS)


def is_search_results_response(
    response: Any,
    api_url_fragment: str = SEARCH_RESULTS_API_FRAGMENT,
) -> bool:
    request = getattr(response, "request", None)
    request_method = getattr(request, "method", None)
    response_url = getattr(response, "url", "")
    return api_url_fragment in response_url and request_method == "POST"


def _attach_initial_search_diagnostics(
    page: Any,
    diagnostics: InitialSearchDiagnostics,
) -> None:
    on = getattr(page, "on", None)
    if not callable(on):
        return
    on("response", diagnostics.record_response)
    on("requestfailed", diagnostics.record_request_failed)


def _detach_initial_search_diagnostics(
    page: Any,
    diagnostics: InitialSearchDiagnostics,
) -> None:
    remove_listener = getattr(page, "remove_listener", None)
    if not callable(remove_listener):
        return
    remove_listener("response", diagnostics.record_response)
    remove_listener("requestfailed", diagnostics.record_request_failed)


async def _log_initial_search_diagnostics(
    *,
    page: Any,
    diagnostics: InitialSearchDiagnostics,
    logger: Callable[[str], None],
) -> None:
    current_url = getattr(page, "url", "") or "未知"
    title = "未知"
    title_getter = getattr(page, "title", None)
    if callable(title_getter):
        try:
            title = await title_getter()
        except Exception:
            title = "读取失败"
    logger(f"初始搜索诊断: title={_truncate(title, 120)} url={_truncate(current_url)}")
    if not diagnostics.events:
        logger("初始搜索阶段未捕获到 goofish/mtop/taobao 相关响应或失败请求。")
        return
    logger("初始搜索阶段最近网络事件:")
    for event in diagnostics.events:
        logger(f"  {event}")


async def wait_for_initial_search_response(
    *,
    page: Any,
    search_url: str,
    logger: Callable[[str], None] = log_time,
    retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_retries: int = INITIAL_SEARCH_RETRY_COUNT,
) -> Any:
    """Navigate to the search page and wait for the first search API response."""
    last_error: Optional[PlaywrightTimeoutError] = None
    diagnostics = InitialSearchDiagnostics()
    _attach_initial_search_diagnostics(page, diagnostics)
    try:
        for retry_index in range(max(1, max_retries)):
            try:
                async with page.expect_response(
                    is_search_results_response,
                    timeout=INITIAL_SEARCH_REQUEST_TIMEOUT_MS,
                ) as response_info:
                    await page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=INITIAL_SEARCH_GOTO_TIMEOUT_MS,
                    )
                return await response_info.value
            except PlaywrightTimeoutError as exc:
                last_error = exc
                current_url = getattr(page, "url", "") or "未知"
                if retry_index < max_retries - 1:
                    logger(
                        "等待初始搜索响应超时，"
                        f"当前页面: {current_url}，"
                        f"{INITIAL_SEARCH_RETRY_DELAY_SECONDS}秒后重试..."
                    )
                    await retry_sleep(INITIAL_SEARCH_RETRY_DELAY_SECONDS)
                    continue

                logger(
                    f"等待初始搜索响应超时 {max_retries} 次，"
                    f"当前页面: {current_url}。"
                )
                await _log_initial_search_diagnostics(
                    page=page,
                    diagnostics=diagnostics,
                    logger=logger,
                )
                raise last_error
    finally:
        _detach_initial_search_diagnostics(page, diagnostics)

    raise RuntimeError("初始搜索响应等待流程异常退出。")


async def advance_search_page(
    *,
    page: Any,
    page_num: int,
    logger: Callable[[str], None] = log_time,
    wait_after_click: Callable[[float, float], Awaitable[None]] = random_sleep,
    retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_retries: int = PAGE_RETRY_COUNT,
) -> PageAdvanceResult:
    next_button = page.locator(NEXT_PAGE_SELECTOR).first
    if not await next_button.count():
        logger("已到达最后一页，未找到可用的'下一页'按钮，停止翻页。")
        return PageAdvanceResult(advanced=False, stop_reason="no_next_button")

    for retry_index in range(max_retries):
        try:
            await next_button.scroll_into_view_if_needed()
            async with page.expect_response(
                is_search_results_response,
                timeout=PAGE_REQUEST_TIMEOUT_MS,
            ) as response_info:
                try:
                    await next_button.click(timeout=PAGE_CLICK_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    logger(f"第 {page_num} 页下一页按钮点击超时，停止翻页。")
                    return PageAdvanceResult(
                        advanced=False,
                        stop_reason="click_timeout",
                    )
            await wait_after_click(
                PAGE_CLICK_SLEEP_MIN_SECONDS,
                PAGE_CLICK_SLEEP_MAX_SECONDS,
            )
            return PageAdvanceResult(
                advanced=True,
                response=await response_info.value,
            )
        except PlaywrightTimeoutError:
            if retry_index < max_retries - 1:
                logger(
                    f"等待第 {page_num} 页搜索响应超时，"
                    f"{PAGE_RETRY_DELAY_SECONDS}秒后重试..."
                )
                await retry_sleep(PAGE_RETRY_DELAY_SECONDS)
                continue

            logger(f"等待第 {page_num} 页搜索响应超时 {max_retries} 次，停止翻页。")
            return PageAdvanceResult(advanced=False, stop_reason="response_timeout")

    return PageAdvanceResult(advanced=False, stop_reason="unknown")
