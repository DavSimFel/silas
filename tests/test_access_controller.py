from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from silas.gates.access import SilasAccessController
from silas.models.gates import AccessLevel
from silas.models.messages import TaintLevel


class TestAccessControllerInit:
    def test_default_levels_created(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        assert ctrl.get_access_level("stranger") == "anonymous"

    def test_owner_gets_owner_level(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        assert ctrl.get_access_level("owner1") == "owner"

    def test_custom_default_level(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1", default_level="authenticated")
        assert ctrl.get_access_level("stranger") == "authenticated"

    def test_invalid_default_level_raises(self) -> None:
        with pytest.raises(ValueError, match="default level must exist"):
            SilasAccessController(owner_id="owner1", default_level="nonexistent")

    def test_custom_access_levels_merged(self) -> None:
        custom = {
            "vip": AccessLevel(description="VIP", tools=["search"], requires=["vip_gate"]),
        }
        ctrl = SilasAccessController(owner_id="owner1", access_levels=custom)
        # default levels still present
        assert ctrl.get_access_level("stranger") == "anonymous"


class TestOwnerAccess:
    def test_owner_by_id(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        assert ctrl.get_access_level("owner1") == "owner"
        assert ctrl.get_allowed_tools("owner1") == ["*"]

    def test_owner_by_taint(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        assert ctrl.get_access_level("stranger", taint=TaintLevel.owner) == "owner"

    def test_owner_filter_tools_returns_all(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        tools = ctrl.filter_tools("owner1", ["a", "b", "c"])
        assert tools == ["a", "b", "c"]


class TestGatePassed:
    def test_gate_promotes_level(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        level = ctrl.gate_passed("user1", "authenticated")
        assert level == "authenticated"

    def test_gate_promotes_to_trusted(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.gate_passed("user1", "authenticated")
        level = ctrl.gate_passed("user1", "trusted")
        assert level == "trusted"

    def test_gate_does_not_demote(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.gate_passed("user1", "trusted")
        # passing only authenticated gate doesn't demote
        level = ctrl.gate_passed("user1", "some_other_gate")
        assert level == "trusted"

    def test_gate_passed_for_owner(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        level = ctrl.gate_passed("owner1", "anything")
        assert level == "owner"

    def test_customer_context_stored(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.gate_passed("user1", "authenticated", customer_context={"plan": "pro"})
        ctx = ctrl.get_customer_context("user1")
        assert ctx == {"plan": "pro"}

    def test_customer_context_none_for_owner(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        assert ctrl.get_customer_context("owner1") is None


class TestFilterTools:
    def test_anonymous_gets_nothing(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        assert ctrl.filter_tools("stranger", ["a", "b"]) == []

    def test_custom_level_filters(self) -> None:
        custom = {
            "authenticated": AccessLevel(
                description="Auth", tools=["search", "read"], requires=["authenticated"]
            ),
        }
        ctrl = SilasAccessController(owner_id="owner1", access_levels=custom)
        ctrl.gate_passed("user1", "authenticated")
        result = ctrl.filter_tools("user1", ["search", "write", "read"])
        assert result == ["search", "read"]


class TestExpiration:
    def test_level_expires(self) -> None:
        custom = {
            "authenticated": AccessLevel(
                description="Auth",
                tools=["search"],
                requires=["authenticated"],
                expires_after=60,
            ),
        }
        ctrl = SilasAccessController(owner_id="owner1", access_levels=custom)
        ctrl.gate_passed("user1", "authenticated")
        assert ctrl.get_access_level("user1") == "authenticated"

        # simulate time passing
        past = datetime.now(UTC) - timedelta(seconds=120)
        state = ctrl._state_by_connection["user1"]
        state.granted_at = past

        assert ctrl.get_access_level("user1") == "anonymous"

    def test_no_expiry_means_permanent(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.gate_passed("user1", "authenticated")
        # default levels have no expires_after
        past = datetime.now(UTC) - timedelta(days=365)
        state = ctrl._state_by_connection["user1"]
        state.granted_at = past
        assert ctrl.get_access_level("user1") == "authenticated"


class TestStateSnapshot:
    def test_snapshot_content(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.gate_passed("user1", "authenticated")
        snap = ctrl.state_snapshot("user1")
        assert snap["level_name"] == "authenticated"
        assert "authenticated" in snap["verified_gates"]
        assert snap["customer_context"] is None

    def test_snapshot_new_connection(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        snap = ctrl.state_snapshot("new_user")
        assert snap["level_name"] == "anonymous"
        assert snap["verified_gates"] == []


class TestUpdateAccessLevels:
    def test_update_levels(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.gate_passed("user1", "authenticated")
        assert ctrl.get_access_level("user1") == "authenticated"

        new_levels = {
            "authenticated": AccessLevel(
                description="New auth", tools=["new_tool"], requires=["authenticated"]
            ),
        }
        ctrl.update_access_levels(new_levels)
        # user still has authenticated level
        assert ctrl.get_allowed_tools("user1") == ["new_tool"]

    def test_update_with_reset(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.gate_passed("user1", "authenticated")
        ctrl.update_access_levels({}, reset_non_owner_state=True)
        assert ctrl.get_access_level("user1") == "anonymous"

    def test_update_preserves_owner(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1")
        ctrl.get_access_level("owner1")  # ensure owner state exists
        ctrl.update_access_levels({}, reset_non_owner_state=True)
        assert ctrl.get_access_level("owner1") == "owner"

    def test_update_keeps_merged_defaults(self) -> None:
        ctrl = SilasAccessController(owner_id="owner1", default_level="authenticated")
        # updating with custom levels still keeps defaults merged
        ctrl.update_access_levels({"vip": AccessLevel(description="VIP", tools=["x"], requires=[])})
        # authenticated still exists from defaults
        assert ctrl.get_access_level("owner1") == "owner"
