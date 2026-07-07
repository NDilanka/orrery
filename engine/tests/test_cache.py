"""Cache-usage parser parity — mirrors selftest-final.ps1 (section a)."""

from __future__ import annotations

from orrery_loop.cache import get_cache_usage, total_tokens


def test_warm_full_usage_correct_ratio_and_flag():
    u = {
        "input_tokens": 1000,
        "cache_read_input_tokens": 9000,
        "cache_creation_input_tokens": 0,
        "output_tokens": 200,
    }
    c = get_cache_usage(u)
    assert c.cache_read == 9000
    assert c.input == 1000
    assert c.hit_ratio == 0.9  # 9000 / (9000 + 1000)
    assert c.warm is True


def test_cold_first_call_no_read():
    u = {
        "input_tokens": 5000,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 5000,
        "output_tokens": 120,
    }
    c = get_cache_usage(u)
    assert c.cache_read == 0
    assert c.cache_creation == 5000
    assert c.hit_ratio == 0.0
    assert c.warm is False


def test_absent_counters_tolerated():
    c = get_cache_usage({"output_tokens": 50})
    assert c.cache_read == 0
    assert c.hit_ratio == 0.0
    assert c.warm is False


def test_nested_result_wrapper_unwrapped():
    result = {
        "type": "result",
        "is_error": False,
        "total_cost_usd": 0.02,
        "usage": {"input_tokens": 2000, "cache_read_input_tokens": 6000},
    }
    c = get_cache_usage(result)
    assert c.cache_read == 6000
    assert c.input == 2000
    assert c.hit_ratio == 0.75  # 6000 / (6000 + 2000)
    assert c.warm is True


def test_raw_json_string_parsed():
    c = get_cache_usage('{ "input_tokens": 100, "cache_read_input_tokens": 300 }')
    assert c.hit_ratio == 0.75
    assert c.warm is True


def test_camel_case_accepted():
    c = get_cache_usage('{ "inputTokens": 1000, "cacheReadInputTokens": 1000 }')
    assert c.hit_ratio == 0.5
    assert c.warm is True


def test_none_usage_safe_zero_cold():
    c = get_cache_usage(None)
    assert c.hit_ratio == 0.0
    assert c.warm is False


def test_unparseable_string_safe_zero():
    c = get_cache_usage("not json")
    assert c.hit_ratio == 0.0
    assert c.warm is False
    assert c.cache_read == 0


# --- total_tokens (A2: token-budget ceiling) -------------------------------


def test_total_tokens_sums_all_four_counters():
    u = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 9000,
        "cache_creation_input_tokens": 500,
    }
    assert total_tokens(u) == 10700


def test_total_tokens_camel_and_nested_and_partial():
    # camelCase + nested result wrapper + a missing counter (-> 0)
    result = {"type": "result", "usage": {"inputTokens": 100, "outputTokens": 50}}
    assert total_tokens(result) == 150


def test_total_tokens_tolerates_none_and_garbage():
    assert total_tokens(None) == 0
    assert total_tokens("not json") == 0
    assert total_tokens({}) == 0
