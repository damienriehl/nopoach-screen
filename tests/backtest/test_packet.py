import json
from datetime import date

from nopoach.backtest.packet import create_packet, replay_packet, verify_packet
from nopoach.backtest.run import CaseDefinition
from nopoach.market.define import Geography, Market
from nopoach.store.workspace import Workspace


def _case():
    return CaseDefinition(
        "invented",
        "Invented case",
        ("A", "B"),
        "Invented workers",
        "Invented metro",
        2020,
        2021,
        date(2022, 1, 1),
        (("A", "B"),),
        (),
        "Synthetic packet test.",
    )


def test_packet_has_fixed_hashed_members_and_verifies_read_only(tmp_path):
    root = tmp_path / "agency"
    with Workspace.open(root) as workspace:
        market = Market(frozenset({"11-1011"}), Geography("metro", "00000"), 2020, 2021)
        packet = create_packet(workspace, {"invented": market}, cases=(_case(),))
        assert packet.finalized and packet.manifest_sha256
        assert {path.name for path in packet.path.iterdir()} == {
            "request.json",
            "sources.json",
            "environment.json",
            "report.json",
            "report.md",
            "review.md",
            "manifest.json",
        }
        manifest = json.loads((packet.path / "manifest.json").read_text())
        assert set(manifest["members"]) == {
            "request.json",
            "sources.json",
            "environment.json",
            "report.json",
            "report.md",
            "review.md",
        }
        assert (
            "does not establish historical" in (packet.path / "review.md").read_text()
        )
    result = verify_packet(packet.path, workspace=root, check_software=True)
    assert (result.integrity, result.inputs, result.compatibility) == (
        "verified",
        "matched",
        "matched",
    )


def test_changed_member_fails_integrity_without_workspace(tmp_path):
    with Workspace.open(tmp_path / "agency") as workspace:
        market = Market(frozenset({"11-1011"}), Geography("metro", "00000"), 2020, 2021)
        packet = create_packet(workspace, {"invented": market}, cases=(_case(),))
        (packet.path / "report.md").write_text("changed")
        result = verify_packet(packet.path)
        assert result.integrity == "failed"
        assert result.inputs == "not checked"


def test_explicit_replay_writes_new_packet_and_preserves_original(tmp_path):
    with Workspace.open(tmp_path / "agency") as workspace:
        market = Market(frozenset({"11-1011"}), Geography("metro", "00000"), 2020, 2021)
        original = create_packet(
            workspace, {"invented": market}, cases=(_case(),), party_ids={"A": "a"}
        )
        before = {path.name: path.read_bytes() for path in original.path.iterdir()}
        replay = replay_packet(original.path, workspace)
        assert replay.status == "reproduced"
        assert replay.packet is not None and replay.packet.path != original.path
        assert {
            path.name: path.read_bytes() for path in original.path.iterdir()
        } == before
