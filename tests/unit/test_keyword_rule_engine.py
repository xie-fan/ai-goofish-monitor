from src.keyword_rule_engine import build_search_text, evaluate_keyword_rules


def _sample_record():
    return {
        "商品信息": {
            "商品标题": "Sony A7M4 全画幅相机",
            "当前售价": "10000",
            "商品标签": ["验货宝", "包邮"],
        },
        "卖家信息": {
            "卖家昵称": "摄影器材店",
            "卖家个性签名": "可验机，支持同城面交",
        },
    }


def test_build_search_text_contains_product_and_seller_fields():
    text = build_search_text(_sample_record())
    assert "sony a7m4" in text
    assert "摄影器材店" in text
    assert "支持同城面交" in text


def test_build_search_text_removes_urls_before_keyword_matching():
    record = {
        "商品信息": {
            "商品标题": "富士相机套机",
            "商品描述": "机身成色好 https://example.com/a7m4/details",
            "商品链接": "https://www.goofish.com/item?id=m1",
            "商品图片列表": [
                "https://img.alicdn.com/imgextra/m1.jpg",
                "//gw.alicdn.com/bao/uploaded/i1/sony.png",
            ],
        },
        "卖家信息": {
            "卖家昵称": "个人卖家",
            "卖家头像链接": "https://img.alicdn.com/avatar/sony.jpg",
        },
    }

    text = build_search_text(record)
    assert "富士相机套机" in text
    assert "机身成色好" in text
    assert "个人卖家" in text
    assert "https" not in text
    assert "alicdn" not in text
    assert "a7m4" not in text
    assert "m1" not in text
    assert "sony" not in text

    result = evaluate_keyword_rules(["a7m4", "m1", "sony"], text)
    assert result["is_recommended"] is False
    assert result["matched_keywords"] == []


def test_keyword_rules_or_match_any_keyword():
    text = build_search_text(_sample_record())
    result = evaluate_keyword_rules(["a7m4", "佳能"], text)
    assert result["is_recommended"] is True
    assert result["analysis_source"] == "keyword"
    assert result["keyword_hit_count"] == 1
    assert result["matched_keywords"] == ["a7m4"]


def test_keyword_rules_count_multiple_hits():
    text = build_search_text(_sample_record())
    result = evaluate_keyword_rules(["a7m4", "验货宝", "摄影器材店"], text)
    assert result["is_recommended"] is True
    assert result["keyword_hit_count"] == 3


def test_keyword_rules_and_match_keywords_inside_one_group():
    text = build_search_text(_sample_record())
    result = evaluate_keyword_rules(["a7m4, 验货宝", "佳能"], text)
    assert result["is_recommended"] is True
    assert result["keyword_hit_count"] == 1
    assert result["matched_keywords"] == ["a7m4", "验货宝"]
    assert result["matched_keyword_groups"] == ["a7m4 + 验货宝"]


def test_keyword_rules_support_chinese_comma_and_require_full_group():
    text = build_search_text(_sample_record())
    result = evaluate_keyword_rules(["a7m4，佳能", "单反，验货宝"], text)
    assert result["is_recommended"] is False
    assert result["keyword_hit_count"] == 0
    assert result["matched_keywords"] == []
    assert result["matched_keyword_groups"] == []


def test_keyword_rules_or_match_between_line_groups():
    text = build_search_text(_sample_record())
    result = evaluate_keyword_rules(["佳能, 单反", "sony, 全画幅"], text)
    assert result["is_recommended"] is True
    assert result["keyword_hit_count"] == 1
    assert result["matched_keywords"] == ["sony", "全画幅"]
    assert result["matched_keyword_groups"] == ["sony + 全画幅"]


def test_keyword_rules_case_insensitive_contains():
    text = build_search_text(_sample_record())
    result = evaluate_keyword_rules(["SONY", "A7M4"], text)
    assert result["is_recommended"] is True
    assert result["keyword_hit_count"] == 2


def test_keyword_rules_no_match():
    text = build_search_text(_sample_record())
    result = evaluate_keyword_rules(["佳能", "单反"], text)
    assert result["is_recommended"] is False
    assert result["keyword_hit_count"] == 0


def test_keyword_rules_do_not_partially_match_alphanumeric_prefixes():
    result = evaluate_keyword_rules(["q1"], "富士 q1r5 旗舰相机")
    assert result["is_recommended"] is False
    assert result["keyword_hit_count"] == 0


def test_keyword_rules_still_match_full_alphanumeric_token():
    result = evaluate_keyword_rules(["q1r5"], "富士 q1r5 旗舰相机")
    assert result["is_recommended"] is True
    assert result["keyword_hit_count"] == 1
