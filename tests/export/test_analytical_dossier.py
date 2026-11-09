import runpy
from dataclasses import replace
from pathlib import Path

from docx import Document

from nopoach.analytical_inputs import AnalyticalInputs, ClusterSettings
from nopoach.export.dossier import export_dossier
from nopoach.market.clusters import confirm_group
from nopoach.pipeline import propose_flow_market, run_screen
from nopoach.store.workspace import Workspace


def helpers():
    return runpy.run_path(
        str(Path(__file__).parents[1] / "test_pipeline_analytical.py")
    )


def text_of(document):
    return "\n".join(
        [p.text for p in document.paragraphs]
        + [
            cell.text
            for table in document.tables
            for row in table.rows
            for cell in row.cells
        ]
    )


def test_dossier_uses_frozen_context_and_sources_without_raw_bundle_reads(tmp_path):
    helper = helpers()
    with Workspace.open(tmp_path / "workspace") as workspace:
        market, _ = helper["load_analytical_fixture"](workspace)
        inputs = AnalyticalInputs(
            j2j=helper["public_input"](workspace),
            contracts=helper["contracts_input"](workspace, market),
        )
        result = run_screen(workspace, market, analytical_inputs=inputs)
        pair = next(
            row.pair
            for row in result.unreliable
            if row.pair.employer_a_id == "a" and row.pair.employer_b_id == "b"
        )
        result = replace(result, references=())
        workspace.connection.execute("UPDATE employers SET name = 'Changed later'")
        for source in result.analytical.sources:
            raw = workspace.root / "analysis/inputs" / source.bundle_id / "raw.bin"
            raw.write_bytes(raw.read_bytes() + b"invalid")
        output = export_dossier(workspace, result, pair, tmp_path / "analytical.docx")
        document = Document(output)
        text = text_of(document)
        assert "Public mobility context" in text
        assert "Context only:" in text and "context_only" not in text
        assert "Contract review context" in text
        assert "60" in text and "100" in text
        assert result.analytical.public_context.reason in text
        for source in result.analytical.sources:
            assert source.manifest_sha256 in text
            assert source.raw_sha256 in text
            assert source.provenance_id in text
        for candidate in result.analytical.contracts.candidates:
            assert candidate.text in text
            assert f"characters {candidate.start}–{candidate.end}" in text
        assert "private-worker-key" not in text and "worker_key" not in text
        assert "not workforce representative" in text
        assert "Synthetic contracts" in text
        assert "Changed later" not in text
        assert "does not" in text
        wording_policy = runpy.run_path(
            str(Path(__file__).parents[1] / "rank/test_wording.py")
        )
        assert not wording_policy["BANNED"].search(text)
        other_pair = next(
            row.pair
            for row in result.unreliable
            if row.pair.employer_a_id == "a" and row.pair.employer_b_id == "c"
        )
        other = export_dossier(workspace, result, other_pair, tmp_path / "other.docx")
        other_text = text_of(Document(other))
        assert "synthetic-contract-a" in other_text
        assert "synthetic-contract-b" not in other_text


def test_flow_dossier_reports_selection_exclusions_and_public_unavailability(tmp_path):
    helper = helpers()
    with Workspace.open(tmp_path / "workspace") as workspace:
        _, population = helper["load_analytical_fixture"](
            workspace, cross_geographies=True
        )
        inputs = AnalyticalInputs(
            flow_population=population,
            cluster_settings=ClusterSettings(2015, 2019),
            j2j=helper["public_input"](workspace),
        )
        market, proposal = propose_flow_market(
            workspace, inputs, start_year=2020, end_year=2021
        )
        market = confirm_group(proposal, market, 0, add={"d"})
        result = run_screen(
            workspace, market, analytical_inputs=inputs, cluster_proposal=proposal
        )
        pair = (result.mutual + tuple(row.pair for row in result.unreliable))[0]
        output = export_dossier(workspace, result, pair, tmp_path / "flow.docx")
        text = text_of(Document(output))
        assert "Flow-defined population" in text
        assert proposal.method in text and proposal.digest in text
        assert "Cross-boundary pairs omitted: 8" in text
        assert "Excluded employers: Synthetic e, Synthetic f" in text
        assert result.analytical.public_context.reason in text
