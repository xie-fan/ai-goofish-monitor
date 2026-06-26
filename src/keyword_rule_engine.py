"""
关键词判断引擎：每行一组，组内 AND，组间 OR。
纯英数字关键词按完整词匹配，避免 Q1 误命中 Q1R5。
"""
import re
from typing import Any, Dict, Iterable, List


_ASCII_TOKEN_KEYWORD_PATTERN = re.compile(r"^[a-z0-9 ]+$")
_ASCII_TOKEN_BOUNDARY = r"[a-z0-9]"
_KEYWORD_GROUP_SEPARATOR_PATTERN = re.compile(r"[,，]+")
_URL_PATTERN = re.compile(
    r"\b[a-z][a-z0-9+.-]*://\S+|//\S+|www\.\S+",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def _strip_urls(value: str) -> str:
    return _URL_PATTERN.sub(" ", value or "")


def _collect_text_fragments(value: Any, bucket: List[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = _strip_urls(value).strip()
        if text:
            bucket.append(text)
        return
    if isinstance(value, (int, float, bool)):
        bucket.append(str(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_text_fragments(item, bucket)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fragments(item, bucket)


def build_search_text(record: Dict[str, Any]) -> str:
    fragments: List[str] = []
    product_info = record.get("商品信息", {})
    seller_info = record.get("卖家信息", {})

    _collect_text_fragments(product_info.get("商品标题"), fragments)
    _collect_text_fragments(product_info, fragments)
    _collect_text_fragments(seller_info, fragments)

    return normalize_text(" ".join(fragments))


def _normalize_keywords(values: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for raw in values or []:
        text = normalize_text(str(raw).strip())
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_keyword_groups(values: Iterable[str]) -> List[List[str]]:
    groups: List[List[str]] = []
    seen_groups = set()
    for raw_group in values or []:
        group = _normalize_keywords(
            _KEYWORD_GROUP_SEPARATOR_PATTERN.split(str(raw_group or ""))
        )
        if not group:
            continue
        group_key = tuple(group)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        groups.append(group)
    return groups


def _uses_ascii_token_match(keyword: str) -> bool:
    return bool(keyword) and _ASCII_TOKEN_KEYWORD_PATTERN.fullmatch(keyword) is not None


def _keyword_matches(keyword: str, normalized_text: str) -> bool:
    if not _uses_ascii_token_match(keyword):
        return keyword in normalized_text
    pattern = rf"(?<!{_ASCII_TOKEN_BOUNDARY}){re.escape(keyword)}(?!{_ASCII_TOKEN_BOUNDARY})"
    return re.search(pattern, normalized_text) is not None


def _format_keyword_group(group: List[str]) -> str:
    return " + ".join(group)


def _flatten_unique(groups: List[List[str]]) -> List[str]:
    flattened: List[str] = []
    seen = set()
    for group in groups:
        for keyword in group:
            if keyword in seen:
                continue
            seen.add(keyword)
            flattened.append(keyword)
    return flattened


def evaluate_keyword_rules(keywords: List[str], search_text: str) -> Dict[str, Any]:
    normalized_text = normalize_text(search_text)
    normalized_groups = _normalize_keyword_groups(keywords)

    if not normalized_text:
        return {
            "analysis_source": "keyword",
            "is_recommended": False,
            "reason": "可匹配文本为空，关键词规则无法执行。",
            "matched_keywords": [],
            "matched_keyword_groups": [],
            "keyword_hit_count": 0,
        }

    if not normalized_groups:
        return {
            "analysis_source": "keyword",
            "is_recommended": False,
            "reason": "未配置关键词规则。",
            "matched_keywords": [],
            "matched_keyword_groups": [],
            "keyword_hit_count": 0,
        }

    matched_groups = [
        group
        for group in normalized_groups
        if all(_keyword_matches(keyword, normalized_text) for keyword in group)
    ]
    matched_group_labels = [_format_keyword_group(group) for group in matched_groups]
    matched_keywords = _flatten_unique(matched_groups)
    hit_count = len(matched_groups)
    is_recommended = hit_count > 0

    if is_recommended:
        reason = f"命中 {hit_count} 组关键词：{'；'.join(matched_group_labels)}"
    else:
        reason = "未命中任何关键词组。"

    return {
        "analysis_source": "keyword",
        "is_recommended": is_recommended,
        "reason": reason,
        "matched_keywords": matched_keywords,
        "matched_keyword_groups": matched_group_labels,
        "keyword_hit_count": hit_count,
    }
