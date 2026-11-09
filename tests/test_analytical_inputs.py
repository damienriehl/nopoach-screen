import json
from dataclasses import asdict, replace
from hashlib import sha256

import pytest

from nopoach.analytical_inputs import (
    AnalyticalInputs,
    BundleRef,
    ClusterSettings,
    list_bundles,
    publish_bundle,
    read_bundle,
)
from nopoach.store.workspace import Workspace


def publish(workspace, body=b"document\r\n"):
    return publish_bundle(
        workspace,
        kind="contracts",
        body=body,
        metadata={"source": "Synthetic review", "population": {"workers": 2}},
        normalized_text="document\n",
        transformation="utf8-newlines-v1",
    )


def test_exact_content_metadata_receipts_and_settings_survive_reopen(tmp_path):
    folder = tmp_path / "workspace"
    with Workspace.open(folder) as workspace:
        tables = workspace.table_names()
        ref = publish(workspace)
        bundle = read_bundle(workspace, ref)
        assert bundle.body == b"document\r\n"
        assert bundle.normalized_text == "document\n"
        assert bundle.raw_sha256 == sha256(bundle.body).hexdigest()
        assert bundle.normalized_sha256 == sha256(b"document\n").hexdigest()
        assert bundle.transformation == "utf8-newlines-v1"
        assert bundle.metadata["population"] == {"workers": 2}
        bundle.metadata["population"]["workers"] = 900
        assert bundle.metadata["population"] == {"workers": 2}
        assert workspace.table_names() == tables
        receipt = next(
            r for r in workspace.provenance.records() if r.id == bundle.provenance_id
        )
        assert receipt.sha256 == bundle.raw_sha256
        settings = AnalyticalInputs(
            contracts=ref, cluster_settings=ClusterSettings(2010, 2014)
        )
        encoded = settings.to_json()
        assert AnalyticalInputs.from_json(encoded) == settings
        assert BundleRef(**asdict(ref)) == ref
    with Workspace.open(folder) as workspace:
        assert list_bundles(workspace) == (ref,)
        assert read_bundle(workspace, ref).body == b"document\r\n"
        assert AnalyticalInputs.from_json(encoded).contracts == ref


def test_identical_import_reuses_version_but_changed_content_keeps_both(tmp_path):
    with Workspace.open(tmp_path / "workspace") as workspace:
        first = publish(workspace)
        assert publish(workspace) == first
        assert len(workspace.provenance.records()) == 1
        second = publish(workspace, b"changed\r\n")
        assert second != first
        assert set(list_bundles(workspace, kind="contracts")) == {first, second}
        assert list_bundles(workspace, kind="j2j") == ()
        assert read_bundle(workspace, first).body == b"document\r\n"


@pytest.mark.parametrize("identifier", ["../escape", "/absolute", "a" * 63, "z" * 64])
def test_supplied_paths_are_not_bundle_identifiers(tmp_path, identifier):
    with Workspace.open(tmp_path / "workspace") as workspace:
        with pytest.raises(ValueError, match="identifier"):
            read_bundle(workspace, BundleRef("contracts", identifier, "a" * 64))


def test_symlinked_storage_is_rejected(tmp_path):
    with Workspace.open(tmp_path / "workspace") as workspace:
        outside = tmp_path / "outside"
        outside.mkdir()
        (workspace.root / "analysis").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink"):
            publish(workspace)
        assert list(outside.iterdir()) == []


def test_symlinked_content_is_rejected_even_when_bytes_match(tmp_path):
    with Workspace.open(tmp_path / "workspace") as workspace:
        ref = publish(workspace)
        path = workspace.root / "analysis" / "inputs" / ref.bundle_id / "raw.bin"
        backup = tmp_path / "original.bin"
        path.rename(backup)
        path.symlink_to(backup)
        with pytest.raises(ValueError, match="symlink"):
            read_bundle(workspace, ref)


@pytest.mark.parametrize("filename", ["raw.bin", "normalized.txt", "manifest.json"])
def test_changed_bytes_fail_integrity_checks(tmp_path, filename):
    with Workspace.open(tmp_path / "workspace") as workspace:
        ref = publish(workspace)
        path = workspace.root / "analysis" / "inputs" / ref.bundle_id / filename
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with pytest.raises(ValueError, match="integrity"):
            read_bundle(workspace, ref)


def test_unsupported_manifest_and_descriptor_versions_are_rejected(tmp_path):
    with Workspace.open(tmp_path / "workspace") as workspace:
        ref = publish(workspace)
        path = workspace.root / "analysis" / "inputs" / ref.bundle_id / "manifest.json"
        data = json.loads(path.read_bytes())
        data["version"] = 99
        body = json.dumps(data).encode()
        path.write_bytes(body)
        with pytest.raises(ValueError, match="version"):
            read_bundle(
                workspace, replace(ref, manifest_sha256=sha256(body).hexdigest())
            )
    with pytest.raises(ValueError, match="version"):
        AnalyticalInputs.from_json('{"version":99}')


def test_interrupted_publication_is_not_selectable(tmp_path, monkeypatch):
    import nopoach.analytical_inputs as inputs

    with Workspace.open(tmp_path / "workspace") as workspace:
        ref = publish(workspace)

        def fail_rename(*args):
            raise OSError("synthetic interruption")

        monkeypatch.setattr(inputs.os, "rename", fail_rename)
        with pytest.raises(OSError, match="interruption"):
            publish(workspace, b"second")
        assert list_bundles(workspace) == (ref,)
        assert read_bundle(workspace, ref).body == b"document\r\n"


def test_default_selection_and_kind_checks():
    assert AnalyticalInputs.from_json("{}") == AnalyticalInputs()
    assert (
        AnalyticalInputs.from_json(AnalyticalInputs().to_json()) == AnalyticalInputs()
    )
    with pytest.raises(ValueError, match="kind"):
        AnalyticalInputs(j2j=BundleRef("contracts", "a" * 64, "b" * 64))
    with pytest.raises(ValueError, match="cluster"):
        ClusterSettings(2015, 2010)
    with pytest.raises(ValueError, match="cluster"):
        ClusterSettings(2010, 2015, groups=5)


def test_invalid_metadata_does_not_publish_or_replace_selection(tmp_path):
    with Workspace.open(tmp_path / "workspace") as workspace:
        ref = publish(workspace)
        with pytest.raises(ValueError):
            publish_bundle(
                workspace, kind="j2j", body=b"csv", metadata={"x": float("nan")}
            )
        assert list_bundles(workspace) == (ref,)


@pytest.mark.network_off
def test_byte_limits_and_missing_transformation_reject_before_publication(
    tmp_path, monkeypatch
):
    import nopoach.analytical_inputs as inputs

    monkeypatch.setattr(inputs, "MAX_RAW_BYTES", 4)
    monkeypatch.setattr(inputs, "MAX_NORMALIZED_BYTES", 4)
    with Workspace.open(tmp_path / "workspace") as workspace:
        for options in (
            {"body": b"12345"},
            {"body": b"ok", "normalized_text": "text"},
            {
                "body": b"ok",
                "normalized_text": "12345",
                "transformation": "identity-v1",
            },
        ):
            with pytest.raises(ValueError):
                publish_bundle(workspace, kind="contracts", metadata={}, **options)
        assert list_bundles(workspace) == ()
        assert workspace.provenance.records() == ()
