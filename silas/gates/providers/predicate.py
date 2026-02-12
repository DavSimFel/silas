from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from silas.core.token_counter import HeuristicTokenCounter
from silas.models.gates import Gate, GateLane, GateResult, GateType

_AND = "and"
_OR = "or"
_RESERVED_CHECK_NAMES = {
    "regex",
    "length",
    "length_limit",
    "keyword",
    "keywords",
    "numeric_range",
    "string_match",
    "file_valid",
}


class PredicateChecker:
    """Deterministic gate checker for predicate-backed gates."""

    def __init__(
        self,
        token_counter: HeuristicTokenCounter | None = None,
        *,
        working_directory: str | Path | None = None,
        permitted_roots: Sequence[str | Path] | None = None,
    ) -> None:
        self._token_counter = token_counter or HeuristicTokenCounter()
        base_dir = Path(working_directory).resolve() if working_directory is not None else Path.cwd().resolve()
        if permitted_roots is None:
            roots: list[Path] = [base_dir]
            tmp_root = Path("/tmp").resolve()
            if tmp_root not in roots:
                roots.append(tmp_root)
            self._permitted_roots = roots
        else:
            self._permitted_roots = [Path(root).resolve() for root in permitted_roots]

    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        return self.evaluate(gate, context)

    def evaluate(self, gate: Gate, context: Mapping[str, object]) -> GateResult:
        if gate.type == GateType.approval_always:
            return self._result(gate.name, "require_approval", "approval_always gate")

        if self._has_logic_predicates(gate.config):
            return self._evaluate_logic_node(gate, context, gate.config)

        check_name = self._normalize(gate.check or gate.type.value)

        if gate.type == GateType.numeric_range:
            return self._evaluate_numeric_range(gate, context, gate.config)
        if gate.type == GateType.file_valid or check_name == "file_valid":
            return self._evaluate_file_valid(gate, context, gate.config)
        if gate.type == GateType.regex or check_name == "regex":
            return self._evaluate_regex(gate, context, gate.config)
        if self._is_length_check(gate.config, check_name):
            return self._evaluate_length(gate, context, gate.config)
        if self._is_keyword_check(gate.config, check_name):
            return self._evaluate_keyword(gate, context, gate.config)
        if gate.type == GateType.string_match:
            return self._evaluate_string_match(gate, context)

        return self._result(
            gate.name,
            "block",
            f"unknown predicate check: {gate.check or gate.type.value}",
        )

    def _evaluate_logic_node(
        self,
        gate: Gate,
        context: Mapping[str, object],
        node: Mapping[str, object],
    ) -> GateResult:
        raw_logic = node.get("logic", _AND)
        logic = self._normalize(raw_logic) if isinstance(raw_logic, str) else _AND
        if logic not in {_AND, _OR}:
            return self._result(gate.name, "block", f"invalid logic operator: {raw_logic!r}")

        raw_children = node.get("predicates")
        if not isinstance(raw_children, Sequence) or isinstance(raw_children, str) or not raw_children:
            return self._result(gate.name, "block", "invalid predicates list")

        child_results: list[GateResult] = []
        for child in raw_children:
            if not isinstance(child, Mapping):
                return self._result(gate.name, "block", "invalid predicate node")
            child_results.append(self._evaluate_predicate_node(gate, context, child))

        action = self._merge_actions(logic, child_results)
        reasons = "; ".join(result.reason for result in child_results)
        flags = sorted(
            {
                flag
                for result in child_results
                for flag in result.flags
            }
        )
        value = next((result.value for result in child_results if result.value is not None), None)
        return GateResult(
            gate_name=gate.name,
            lane=GateLane.policy,
            action=action,
            reason=f"composed({logic}): {reasons}",
            value=value,
            flags=flags,
        )

    def _evaluate_predicate_node(
        self,
        gate: Gate,
        context: Mapping[str, object],
        node: Mapping[str, object],
    ) -> GateResult:
        if self._has_logic_predicates(node):
            return self._evaluate_logic_node(gate, context, node)

        raw_type = node.get("type")
        if not isinstance(raw_type, str):
            return self._result(gate.name, "block", "predicate node missing type")

        predicate_type = self._normalize(raw_type)
        if predicate_type == "regex":
            return self._evaluate_regex(gate, context, node)
        if predicate_type in {"length", "length_limit"}:
            return self._evaluate_length(gate, context, node)
        if predicate_type in {"keyword", "keywords"}:
            return self._evaluate_keyword(gate, context, node)
        if predicate_type == "file_valid":
            return self._evaluate_file_valid(gate, context, node)

        return self._result(gate.name, "block", f"unknown predicate type: {raw_type!r}")

    def _evaluate_numeric_range(
        self,
        gate: Gate,
        context: Mapping[str, object],
        config: Mapping[str, object],
    ) -> GateResult:
        extracted = self._extract_value(gate, context, config)
        value = self._coerce_float(extracted)
        if value is None:
            return self._result(gate.name, "block", f"value is not numeric: {extracted!r}")

        outside = gate.block.get("outside") if gate.block else None
        if isinstance(outside, list) and len(outside) == 2:
            low = self._coerce_float(outside[0])
            high = self._coerce_float(outside[1])
            if low is not None and high is not None and not (low <= value <= high):
                return self._result(
                    gate.name,
                    "block",
                    f"value {value} outside [{low}, {high}]",
                    value=value,
                )

        auto = self._range_bounds(gate.auto_approve)
        if auto is not None and auto[0] <= value <= auto[1]:
            return self._result(
                gate.name,
                "continue",
                f"value {value} inside auto-approve range",
                value=value,
            )

        approval = self._range_bounds(gate.require_approval)
        if approval is not None and approval[0] <= value <= approval[1]:
            return self._result(
                gate.name,
                "require_approval",
                f"value {value} requires approval",
                value=value,
            )

        return self._result(gate.name, "block", f"value {value} did not match any allowed range", value=value)

    def _evaluate_file_valid(
        self,
        gate: Gate,
        context: Mapping[str, object],
        config: Mapping[str, object],
    ) -> GateResult:
        extracted = self._extract_value(gate, context, config)
        raw_path = self._coerce_path_text(extracted)
        if raw_path is None:
            return self._result(gate.name, "block", "file path is required")

        try:
            resolved_path = self._resolve_permitted_path(raw_path)
        except ValueError as exc:
            return self._result(gate.name, "block", str(exc))

        if not resolved_path.exists() or not resolved_path.is_file():
            return self._result(gate.name, "block", "File not found")

        pattern = self._extract_file_pattern(gate, config)
        if pattern is not None:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                return self._result(gate.name, "block", f"invalid file pattern: {exc}")
            if compiled.search(str(resolved_path)) is None:
                return self._result(
                    gate.name,
                    "block",
                    f"file path does not match pattern {pattern!r}",
                )

        size_status = self._check_file_size(gate, config, resolved_path)
        if size_status is not None:
            return size_status

        return self._result(
            gate.name,
            "continue",
            f"file valid: {resolved_path}",
            value=str(resolved_path),
        )

    def _extract_file_pattern(self, gate: Gate, config: Mapping[str, object]) -> str | None:
        raw_pattern = config.get("pattern") or config.get("path_pattern")
        if isinstance(raw_pattern, str) and raw_pattern.strip():
            return raw_pattern.strip()

        if isinstance(gate.check, str):
            candidate = gate.check.strip()
            if candidate and self._normalize(candidate) not in _RESERVED_CHECK_NAMES:
                return candidate
        return None

    def _check_file_size(
        self,
        gate: Gate,
        config: Mapping[str, object],
        path: Path,
    ) -> GateResult | None:
        max_size_raw = config.get("max_size_bytes")
        if max_size_raw is None:
            return None
        max_size = self._coerce_int(max_size_raw)
        if max_size is None or max_size < 0:
            return self._result(
                gate.name,
                "block",
                f"invalid max_size_bytes: {max_size_raw!r}",
            )

        size_bytes = path.stat().st_size
        if size_bytes > max_size:
            return self._result(
                gate.name,
                "block",
                f"file size {size_bytes} exceeds max_size_bytes {max_size}",
                value=float(size_bytes),
            )
        return None

    def _coerce_path_text(self, value: object) -> str | None:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        return None

    def _resolve_permitted_path(self, raw_path: str) -> Path:
        input_path = Path(raw_path)
        if ".." in input_path.parts:
            raise ValueError("Path outside permitted directories")

        if input_path.is_absolute():
            candidate = input_path.resolve()
        else:
            candidate = (self._permitted_roots[0] / input_path).resolve()

        if not any(self._is_relative_to(candidate, root) for root in self._permitted_roots):
            raise ValueError("Path outside permitted directories")
        return candidate

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _evaluate_string_match(self, gate: Gate, context: Mapping[str, object]) -> GateResult:
        value = self._as_text(self._extract_value(gate, context, gate.config))
        case_sensitive = bool(gate.config.get("case_sensitive", True))
        expected = value if case_sensitive else value.lower()

        allowed_values = self._normalize_values(gate.allowed_values, case_sensitive)
        approval_values = self._normalize_values(gate.approval_values, case_sensitive)

        if expected in allowed_values:
            return self._result(gate.name, "continue", f"value {value!r} is allowed", value=value)
        if expected in approval_values:
            return self._result(
                gate.name,
                "require_approval",
                f"value {value!r} requires approval",
                value=value,
            )
        return self._result(gate.name, "block", f"value {value!r} is blocked", value=value)

    def _evaluate_regex(
        self,
        gate: Gate,
        context: Mapping[str, object],
        config: Mapping[str, object],
    ) -> GateResult:
        pattern = self._extract_pattern(gate, config)
        if pattern is None:
            return self._result(gate.name, "block", "regex pattern is required")

        flags = 0
        if bool(config.get("ignore_case")):
            flags |= re.IGNORECASE
        if bool(config.get("multiline")):
            flags |= re.MULTILINE

        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            return self._result(gate.name, "block", f"invalid regex pattern: {exc}")

        value = self._as_text(self._extract_value(gate, context, config))
        if compiled.search(value):
            return self._result(gate.name, "continue", f"value matched regex {pattern!r}", value=value)
        return self._result(gate.name, "block", f"value did not match regex {pattern!r}", value=value)

    def _evaluate_length(
        self,
        gate: Gate,
        context: Mapping[str, object],
        config: Mapping[str, object],
    ) -> GateResult:
        value = self._as_text(self._extract_value(gate, context, config))
        char_count = len(value)
        token_count = self._token_counter.count(value)

        min_chars = self._coerce_int(config.get("min_chars"))
        max_chars = self._coerce_int(config.get("max_chars"))
        min_tokens = self._coerce_int(config.get("min_tokens"))
        max_tokens = self._coerce_int(config.get("max_tokens"))

        if min_chars is not None and char_count < min_chars:
            return self._result(
                gate.name,
                "block",
                f"length chars {char_count} < min_chars {min_chars}",
                value=float(char_count),
            )
        if max_chars is not None and char_count > max_chars:
            return self._result(
                gate.name,
                "block",
                f"length chars {char_count} > max_chars {max_chars}",
                value=float(char_count),
            )
        if min_tokens is not None and token_count < min_tokens:
            return self._result(
                gate.name,
                "block",
                f"length tokens {token_count} < min_tokens {min_tokens}",
                value=float(token_count),
            )
        if max_tokens is not None and token_count > max_tokens:
            return self._result(
                gate.name,
                "block",
                f"length tokens {token_count} > max_tokens {max_tokens}",
                value=float(token_count),
            )

        return self._result(
            gate.name,
            "continue",
            f"length ok chars={char_count} tokens={token_count}",
            value=float(token_count),
        )

    def _evaluate_keyword(
        self,
        gate: Gate,
        context: Mapping[str, object],
        config: Mapping[str, object],
    ) -> GateResult:
        text = self._as_text(self._extract_value(gate, context, config))
        case_sensitive = bool(config.get("case_sensitive", False))
        haystack = text if case_sensitive else text.lower()

        blocked = self._keyword_list(
            config.get("blocked_keywords")
            or config.get("block_keywords")
            or config.get("blocked")
            or config.get("block")
        )
        required = self._keyword_list(
            config.get("required_keywords")
            or config.get("require_keywords")
            or config.get("required")
            or config.get("require")
        )
        if not case_sensitive:
            blocked = [token.lower() for token in blocked]
            required = [token.lower() for token in required]

        blocked_hits = [token for token in blocked if token in haystack]
        if blocked_hits:
            return self._result(
                gate.name,
                "block",
                f"blocked keywords found: {', '.join(blocked_hits)}",
                value=", ".join(blocked_hits),
            )

        missing = [token for token in required if token not in haystack]
        if missing:
            return self._result(
                gate.name,
                "require_approval",
                f"required keywords missing: {', '.join(missing)}",
                value=", ".join(missing),
            )

        return self._result(gate.name, "continue", "keyword checks passed")

    def _merge_actions(
        self,
        logic: str,
        results: Sequence[GateResult],
    ) -> Literal["continue", "block", "require_approval"]:
        if logic == _AND:
            if any(result.action == "block" for result in results):
                return "block"
            if any(result.action == "require_approval" for result in results):
                return "require_approval"
            return "continue"

        if any(result.action == "continue" for result in results):
            return "continue"
        if any(result.action == "require_approval" for result in results):
            return "require_approval"
        return "block"

    def _extract_value(
        self,
        gate: Gate,
        context: Mapping[str, object],
        config: Mapping[str, object],
    ) -> object:
        extract = config.get("extract")
        if not isinstance(extract, str):
            extract = gate.extract
        if isinstance(extract, str) and extract in context:
            return context[extract]

        for key in ("value", "message", "response", "text", "step_output", "tool_args"):
            if key in context:
                return context[key]
        return None

    def _extract_pattern(self, gate: Gate, config: Mapping[str, object]) -> str | None:
        configured = config.get("pattern")
        if isinstance(configured, str) and configured:
            return configured

        if isinstance(gate.check, str):
            candidate = gate.check.strip()
            if candidate and self._normalize(candidate) not in _RESERVED_CHECK_NAMES:
                return candidate
        return None

    def _normalize_values(self, values: Sequence[str] | None, case_sensitive: bool) -> set[str]:
        if not values:
            return set()
        if case_sensitive:
            return {value for value in values}
        return {value.lower() for value in values}

    def _keyword_list(self, value: object) -> list[str]:
        if not isinstance(value, Sequence) or isinstance(value, str):
            return []
        tokens: list[str] = []
        for item in value:
            if isinstance(item, str) and item:
                tokens.append(item)
        return tokens

    def _as_text(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, sort_keys=True)
        if value is None:
            return ""
        return str(value)

    def _range_bounds(self, payload: Mapping[str, object] | None) -> tuple[float, float] | None:
        if not isinstance(payload, Mapping):
            return None
        low = self._coerce_float(payload.get("min"))
        high = self._coerce_float(payload.get("max"))
        if low is None or high is None:
            return None
        return (low, high)

    def _coerce_float(self, value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return float(stripped)
            except ValueError:
                return None
        return None

    def _coerce_int(self, value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if not stripped.lstrip("-").isdigit():
                return None
            return int(stripped)
        return None

    def _normalize(self, value: str) -> str:
        return value.strip().lower().replace("-", "_").replace(" ", "_")

    def _has_logic_predicates(self, config: Mapping[str, object]) -> bool:
        predicates = config.get("predicates")
        return isinstance(predicates, Sequence) and not isinstance(predicates, str)

    def _is_length_check(self, config: Mapping[str, object], check_name: str) -> bool:
        if check_name in {"length", "length_limit"}:
            return True
        keys = {"min_chars", "max_chars", "min_tokens", "max_tokens"}
        return any(key in config for key in keys)

    def _is_keyword_check(self, config: Mapping[str, object], check_name: str) -> bool:
        if check_name in {"keyword", "keywords"}:
            return True
        keys = {
            "blocked_keywords",
            "block_keywords",
            "blocked",
            "block",
            "required_keywords",
            "require_keywords",
            "required",
            "require",
        }
        return any(key in config for key in keys)

    def _result(
        self,
        gate_name: str,
        action: Literal["continue", "block", "require_approval"],
        reason: str,
        *,
        value: str | float | None = None,
        flags: list[str] | None = None,
    ) -> GateResult:
        return GateResult(
            gate_name=gate_name,
            lane=GateLane.policy,
            action=action,
            reason=reason,
            value=value,
            flags=flags or [],
        )


__all__ = ["PredicateChecker"]
