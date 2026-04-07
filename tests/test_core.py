"""
Alf-E Integration Tests — The basics that must pass before any code change.
"""

import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.playbook_loader import load_playbook
from engine.playbook_schema import PlaybookConfig, UserRole, ActionApproval
from engine.memory import Memory
from engine.model_router import ModelRouter


# ── Playbook Tests ───────────────────────────────────────────────────────

def test_load_cole_sandbox():
    """Cole sandbox playbook loads and validates correctly."""
    pb = load_playbook(Path("playbooks/cole_sandbox.toml"))
    assert pb.name == "Cole Family Sandbox"
    assert pb.version == "2.3.1"
    assert "default" in pb.llm
    assert "heavy" in pb.llm
    assert "fast" in pb.llm
    assert pb.home_assistant is not None
    assert "solar_watts" in pb.sensors
    assert "tesla_soc" in pb.sensors
    assert len(pb.users) >= 1
    print("  ✅ Cole sandbox loads correctly")


def test_load_device_trader():
    """Device Trader playbook loads and validates correctly."""
    pb = load_playbook(Path("playbooks/device_trader.toml"))
    assert pb.name == "Alf-E — Device Trader Ops"
    assert len(pb.connectors) > 0
    assert len(pb.actions) > 0
    assert len(pb.boundaries) > 0
    assert len(pb.scheduled_ops) > 0
    print("  ✅ Device Trader loads correctly")


def test_user_roles():
    """User roles are parsed correctly."""
    pb = load_playbook(Path("playbooks/cole_sandbox.toml"))
    owner = pb.get_owner()
    assert owner is not None
    assert owner.role == UserRole.owner
    assert owner.name == "Fraser"
    print("  ✅ User roles parsed correctly")


def test_action_approval_tiers():
    """Action approval tiers are parsed."""
    pb = load_playbook(Path("playbooks/cole_sandbox.toml"))
    for action in pb.actions:
        assert action.approval in ActionApproval
    print("  ✅ Action approval tiers valid")


# ── Memory Tests ─────────────────────────────────────────────────────────

def test_memory_save_load():
    """Memory saves and loads messages correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        mem = Memory(db_path=db_path)

        mem.save_message("user", "Hello Alf-E", user_id="fraser")
        mem.save_message("assistant", "G'day mate!", user_id="fraser", model_used="haiku")

        msgs = mem.load_messages(user_id="fraser")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        print("  ✅ Memory save/load works")


def test_memory_user_isolation():
    """Different users have isolated message histories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        mem = Memory(db_path=db_path)

        mem.save_message("user", "Fraser's message", user_id="fraser")
        mem.save_message("user", "Family message", user_id="family")

        fraser_msgs = mem.load_messages(user_id="fraser")
        family_msgs = mem.load_messages(user_id="family")

        assert len(fraser_msgs) == 1
        assert len(family_msgs) == 1
        assert fraser_msgs[0]["content"] == "Fraser's message"
        assert family_msgs[0]["content"] == "Family message"
        print("  ✅ User isolation works")


def test_memory_cost_tracking():
    """Cost summary works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        mem = Memory(db_path=db_path)

        mem.save_message("assistant", "test", cost_usd=0.001, tokens_input=100, tokens_output=50)
        summary = mem.get_cost_summary(30)
        assert summary["messages"] == 1
        assert summary["cost_usd"] == 0.001
        print("  ✅ Cost tracking works")


# ── Router Tests ─────────────────────────────────────────────────────────

def test_model_router_tier_classification():
    """Router classifies tasks into correct tiers."""
    pb = load_playbook(Path("playbooks/cole_sandbox.toml"))
    router = ModelRouter(pb.llm)

    _, fast_config = router.route("What's the weather?")
    assert fast_config.model in ["gemini-2.5-flash", "claude-haiku-4-5-20251001"]

    _, heavy_config = router.route("Analyse my energy usage over the last month and prepare a comprehensive report with scenarios")
    assert heavy_config.model in ["claude-sonnet-4-20250514"]

    print("  ✅ Router tier classification works")


# ── Run All ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 Alf-E Integration Tests\n" + "=" * 40)

    tests = [
        test_load_cole_sandbox,
        test_load_device_trader,
        test_user_roles,
        test_action_approval_tiers,
        test_memory_save_load,
        test_memory_user_isolation,
        test_memory_cost_tracking,
        test_model_router_tier_classification,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("🎉 All tests passed!")
    else:
        print("⚠️  Some tests failed — fix before committing.")
        sys.exit(1)
