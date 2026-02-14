from __future__ import annotations

import pytest
from silas.gates.predicates import PredicateChecker
from silas.models.gates import Gate, GateTrigger, GateType


def _gate(
    name: str = "g",
    *,
    gate_type: GateType = GateType.string_match,
    check: str | None = None,
    config: dict | None = None,
    extract: str | None = None,
    auto_approve: dict | None = None,
    require_approval: dict | None = None,
    block: dict | None = None,
    allowed_values: list[str] | None = None,
    approval_values: list[str] | None = None,
) -> Gate:
    return Gate(
        name=name,
        on=GateTrigger.every_user_message,
        type=gate_type,
        check=check,
        config=config or {},
        extract=extract,
        auto_approve=auto_approve,
        require_approval=require_approval,
        block=block,
        allowed_values=allowed_values,
        approval_values=approval_values,
    )


class TestStringMatch:
    def test_allowed_value(self) -> None:
        checker = PredicateChecker()
        gate = _gate(allowed_values=["yes", "no"])
        result = checker.evaluate(gate, {"value": "yes"})
        assert result.action == "continue"

    def test_blocked_value(self) -> None:
        checker = PredicateChecker()
        gate = _gate(allowed_values=["yes"], approval_values=["maybe"])
        result = checker.evaluate(gate, {"value": "nope"})
        assert result.action == "block"

    def test_approval_value(self) -> None:
        checker = PredicateChecker()
        gate = _gate(approval_values=["maybe"])
        result = checker.evaluate(gate, {"value": "maybe"})
        assert result.action == "require_approval"

    def test_case_insensitive(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            config={"case_sensitive": False},
            allowed_values=["yes"],
        )
        result = checker.evaluate(gate, {"value": "YES"})
        assert result.action == "continue"


class TestRegex:
    def test_matching_regex(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.regex, config={"pattern": r"\d{3}"})
        result = checker.evaluate(gate, {"value": "code 123 here"})
        assert result.action == "continue"

    def test_non_matching_regex(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.regex, config={"pattern": r"^\d+$"})
        result = checker.evaluate(gate, {"value": "no digits"})
        assert result.action == "block"

    def test_invalid_regex_blocks(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.regex, config={"pattern": r"[invalid"})
        result = checker.evaluate(gate, {"value": "test"})
        assert result.action == "block"
        assert "invalid regex" in result.reason

    def test_ignore_case_flag(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            gate_type=GateType.regex,
            config={"pattern": r"hello", "ignore_case": True},
        )
        result = checker.evaluate(gate, {"value": "HELLO world"})
        assert result.action == "continue"

    def test_no_pattern_blocks(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.regex, config={})
        result = checker.evaluate(gate, {"value": "test"})
        assert result.action == "block"

    def test_check_as_pattern(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.regex, check=r"\w+@\w+")
        result = checker.evaluate(gate, {"value": "user@host"})
        assert result.action == "continue"


class TestNumericRange:
    def test_auto_approve_in_range(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            gate_type=GateType.numeric_range,
            auto_approve={"min": 0, "max": 100},
        )
        result = checker.evaluate(gate, {"value": 50})
        assert result.action == "continue"

    def test_require_approval_in_range(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            gate_type=GateType.numeric_range,
            auto_approve={"min": 0, "max": 100},
            require_approval={"min": 100, "max": 500},
        )
        result = checker.evaluate(gate, {"value": 200})
        assert result.action == "require_approval"

    def test_blocked_outside(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            gate_type=GateType.numeric_range,
            block={"outside": [0, 1000]},
        )
        result = checker.evaluate(gate, {"value": 2000})
        assert result.action == "block"

    def test_non_numeric_blocks(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.numeric_range)
        result = checker.evaluate(gate, {"value": "not a number"})
        assert result.action == "block"

    def test_no_matching_range_blocks(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            gate_type=GateType.numeric_range,
            auto_approve={"min": 0, "max": 10},
        )
        result = checker.evaluate(gate, {"value": 50})
        assert result.action == "block"


class TestLength:
    def test_within_limits(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"min_chars": 1, "max_chars": 100}, check="length")
        result = checker.evaluate(gate, {"value": "hello"})
        assert result.action == "continue"

    def test_too_short(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"min_chars": 10}, check="length")
        result = checker.evaluate(gate, {"value": "hi"})
        assert result.action == "block"

    def test_too_long(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"max_chars": 5}, check="length")
        result = checker.evaluate(gate, {"value": "this is too long"})
        assert result.action == "block"

    def test_token_limits(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"max_tokens": 2}, check="length")
        result = checker.evaluate(gate, {"value": "one two three four five six"})
        assert result.action == "block"


class TestKeyword:
    def test_blocked_keyword(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"blocked_keywords": ["bad", "evil"]}, check="keyword")
        result = checker.evaluate(gate, {"value": "this is bad"})
        assert result.action == "block"

    def test_required_keyword_missing(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"required_keywords": ["agree"]}, check="keyword")
        result = checker.evaluate(gate, {"value": "I do not"})
        assert result.action == "require_approval"

    def test_keyword_passes(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            config={"blocked_keywords": ["bad"], "required_keywords": ["agree"]},
            check="keyword",
        )
        result = checker.evaluate(gate, {"value": "I agree"})
        assert result.action == "continue"

    def test_case_insensitive_keywords(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            config={"blocked_keywords": ["BAD"], "case_sensitive": False},
            check="keyword",
        )
        result = checker.evaluate(gate, {"value": "this is bad"})
        assert result.action == "block"


class TestApprovalAlways:
    def test_approval_always(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.approval_always)
        result = checker.evaluate(gate, {})
        assert result.action == "require_approval"


class TestLogicComposition:
    def test_and_logic_all_pass(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            config={
                "logic": "and",
                "predicates": [
                    {"type": "length", "min_chars": 1},
                    {"type": "keyword", "blocked_keywords": ["evil"]},
                ],
            }
        )
        result = checker.evaluate(gate, {"value": "hello world"})
        assert result.action == "continue"

    def test_and_logic_one_fails(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            config={
                "logic": "and",
                "predicates": [
                    {"type": "length", "min_chars": 1},
                    {"type": "keyword", "blocked_keywords": ["world"]},
                ],
            }
        )
        result = checker.evaluate(gate, {"value": "hello world"})
        assert result.action == "block"

    def test_or_logic_one_passes(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            config={
                "logic": "or",
                "predicates": [
                    {"type": "regex", "pattern": r"^\d+$"},
                    {"type": "keyword", "required_keywords": ["hello"]},
                ],
            }
        )
        result = checker.evaluate(gate, {"value": "hello"})
        assert result.action == "continue"

    def test_or_logic_all_fail(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            config={
                "logic": "or",
                "predicates": [
                    {"type": "regex", "pattern": r"^\d+$"},
                    {"type": "length", "max_chars": 2},
                ],
            }
        )
        result = checker.evaluate(gate, {"value": "hello"})
        assert result.action == "block"

    def test_invalid_logic_operator(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"logic": "xor", "predicates": [{"type": "length"}]})
        result = checker.evaluate(gate, {"value": "test"})
        assert result.action == "block"

    def test_invalid_predicates_list(self) -> None:
        checker = PredicateChecker()
        gate = _gate(config={"predicates": "not_a_list"})
        # string predicates -> _has_logic_predicates returns False, falls through
        result = checker.evaluate(gate, {"value": "test"})
        # should fall through to string_match default handling
        assert result.action == "block"


class TestExtractValue:
    def test_extract_from_context_key(self) -> None:
        checker = PredicateChecker()
        gate = _gate(
            extract="custom_field",
            allowed_values=["hello"],
        )
        result = checker.evaluate(gate, {"custom_field": "hello"})
        assert result.action == "continue"

    def test_extract_fallback_to_message(self) -> None:
        checker = PredicateChecker()
        gate = _gate(allowed_values=["hi"])
        result = checker.evaluate(gate, {"message": "hi"})
        assert result.action == "continue"


class TestAsyncCheck:
    @pytest.mark.asyncio
    async def test_async_check_delegates(self) -> None:
        checker = PredicateChecker()
        gate = _gate(allowed_values=["ok"])
        result = await checker.check(gate, {"value": "ok"})
        assert result.action == "continue"


class TestUnknownPredicate:
    def test_unknown_type_blocks(self) -> None:
        checker = PredicateChecker()
        gate = _gate(gate_type=GateType.string_match, check="totally_unknown_check")
        # Falls through to string_match evaluation
        result = checker.evaluate(gate, {"value": "test"})
        assert result.action == "block"
