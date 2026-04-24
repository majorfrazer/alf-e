"""
Smoke test: every *.toml in playbooks/ must parse and validate.

Catches the "Harley → admin" crash class: schema enum drift between
a playbook tweak and the UserConfig enum. Runs in CI on every push.
"""

from pathlib import Path
import pytest

from engine.playbook_loader import load_playbook


PLAYBOOK_DIR = Path(__file__).parent.parent / "playbooks"
PLAYBOOKS = sorted(PLAYBOOK_DIR.glob("*.toml"))


@pytest.mark.parametrize("path", PLAYBOOKS, ids=[p.name for p in PLAYBOOKS])
def test_playbook_loads(path: Path) -> None:
    pb = load_playbook(path)
    assert pb.name, f"{path.name}: missing [metadata].name"
    assert pb.llm, f"{path.name}: no [llm.*] providers defined"
    assert any(u.role.value == "owner" for u in pb.users) or not pb.users, \
        f"{path.name}: if users are defined, at least one must be role='owner'"
