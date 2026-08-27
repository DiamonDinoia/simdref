"""Tests for fixes to DiamonDinoia/simdref#23, #24 and #25.

Covers:
- #23 ``show --arch`` no longer stamps ``[measured]`` on empty perf rows,
  and drops the variants the pinned core cannot execute.
- #24 the ``llm`` payload keys latency and throughput by microarchitecture,
  carries the port string, and reports catalog provenance.
- #25 ``install.sh`` builds with build isolation on, so a fresh
  ``uv venv`` without setuptools still installs.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from simdref import cli
from simdref.annotate import arch_perf, arch_perf_tag
from simdref.cli import (
    LLM_EXIT_USAGE,
    _llm_schema_payload,
    _llm_timing,
    _merge_timing,
    _pin_arch,
    _resolve_llm_arch_or_exit,
)
from simdref.models import InstructionRecord

REPO_ROOT = Path(__file__).resolve().parents[1]


def _entry(lat: str | None, cpi: str | None, *, ports: str | None = None, kind="measured") -> dict:
    measurement: dict = {}
    if cpi is not None:
        measurement["TP_loop"] = cpi
    if ports is not None:
        measurement["ports"] = ports
    return {
        "latencies": [{"cycles": lat}] if lat is not None else [],
        "measurement": measurement,
        "source_kind": kind,
    }


def _record(mnemonic: str, form: str, arch_details: dict) -> InstructionRecord:
    return InstructionRecord(
        mnemonic=mnemonic,
        form=form,
        summary=f"{mnemonic} summary.",
        arch_details=arch_details,
    )


# ---------------------------------------------------------------------------
# #23 — show --arch
# ---------------------------------------------------------------------------


def test_arch_perf_tag_marks_an_empty_row_missing():
    assert arch_perf_tag("ZEN2", None, None, "measured") == "[missing:ZEN2]"


def test_arch_perf_tag_keeps_provenance_when_a_value_exists():
    assert arch_perf_tag("ZEN2", 6.0, None, "measured") == "[ZEN2, measured]"
    assert arch_perf_tag("ZEN2", None, 1.27, "modeled") == "[ZEN2, modeled]"


def test_arch_perf_reports_no_values_for_an_absent_core():
    rec = _record("vpermpd", "vpermpd ymm, ymm, imm8", {"ZEN4": _entry("4", "0.50")})
    lat, cpi, _ = arch_perf(rec, "ZEN2")
    assert (lat, cpi) == (None, None)


def _summary_output(monkeypatch, capsys, records, arch, as_json=False):
    monkeypatch.setattr(cli, "ensure_runtime", lambda: None)
    rc = cli._print_non_interactive_summary("vpermpd", records=records, arch=arch, as_json=as_json)
    return rc, capsys.readouterr().out


def test_show_arch_omits_variants_the_core_cannot_execute(monkeypatch, capsys):
    runnable = _record("vpermpd", "vpermpd ymm, ymm, imm8", {"ZEN2": _entry("6", "1.27")})
    avx512 = _record("vpermpd", "vpermpd ymm, k, ymm, imm8", {"ZEN4": _entry("4", "0.50")})
    rc, out = _summary_output(monkeypatch, capsys, [runnable, avx512], "znver2")

    assert rc == 0
    assert "(1 variant)" in out
    assert "1 further variant(s) omitted: ZEN2 cannot execute them." in out
    assert "[ZEN2, measured]: lat=6.0c cpi=1.27" in out
    assert avx512.key not in out


def test_show_arch_never_labels_an_empty_row_measured(monkeypatch, capsys):
    # ZEN2 is present but carries no latency and no throughput.
    rec = _record("vpermpd", "vpermpd ymm, ymm, imm8", {"ZEN2": _entry(None, None)})
    rc, out = _summary_output(monkeypatch, capsys, [rec], "znver2")

    assert rc == 0
    assert "[missing:ZEN2]: lat=-c cpi=-" in out
    assert "measured" not in out


def test_show_arch_json_counts_the_omitted_variants(monkeypatch, capsys):
    runnable = _record("vpermpd", "vpermpd ymm, ymm, imm8", {"ZEN2": _entry("6", "1.27")})
    avx512 = _record("vpermpd", "vpermpd ymm, k, ymm, imm8", {"ZEN4": _entry("4", "0.50")})
    rc, out = _summary_output(monkeypatch, capsys, [runnable, avx512], "znver2", as_json=True)

    assert rc == 0
    payload = json.loads(out)
    assert payload["arch"] == "ZEN2"
    assert payload["variants_not_runnable"] == 1
    assert [v["key"] for v in payload["variants"]] == [runnable.key]


def test_show_without_arch_lists_every_variant(monkeypatch, capsys):
    # Positive control: the filter must fire only under --arch.
    runnable = _record("vpermpd", "vpermpd ymm, ymm, imm8", {"ZEN2": _entry("6", "1.27")})
    avx512 = _record("vpermpd", "vpermpd ymm, k, ymm, imm8", {"ZEN4": _entry("4", "0.50")})
    rc, out = _summary_output(monkeypatch, capsys, [runnable, avx512], None)

    assert rc == 0
    assert "(2 variants)" in out
    assert "omitted" not in out


# ---------------------------------------------------------------------------
# #24 — llm payload
# ---------------------------------------------------------------------------


def test_llm_timing_keys_values_by_microarchitecture():
    arch_details = {
        "SKX": _entry("4", "0.50", ports="1*p01"),
        "ZEN2": _entry("6", "1.27"),
    }
    timing = _llm_timing(arch_details)

    assert timing["SKX"] == {
        "lat": 4.0,
        "cpi": 0.5,
        "source_kind": "measured",
        "ports": "1*p01",
    }
    assert timing["ZEN2"]["lat"] == 6.0
    assert timing["ZEN2"]["cpi"] == 1.27
    # No port data upstream for this core: the key is absent, not empty.
    assert "ports" not in timing["ZEN2"]


def test_llm_timing_drops_cores_with_no_values():
    timing = _llm_timing({"SKX": _entry("4", "0.50"), "ZEN2": _entry(None, None)})
    assert set(timing) == {"SKX"}


def test_llm_timing_carries_the_modeled_label():
    timing = _llm_timing({"neoverse-v2": _entry("4", "0.25", kind="modeled")})
    assert timing["neoverse-v2"]["source_kind"] == "modeled"


def test_merge_timing_keeps_the_lowest_value_per_core():
    reg = {"SKX": {"lat": 4.0, "cpi": 0.5, "source_kind": "measured", "ports": "1*p01"}}
    mem = {"SKX": {"lat": 11.0, "cpi": 0.5, "source_kind": "measured"}}
    merged = _merge_timing([mem, reg])

    assert merged["SKX"]["lat"] == 4.0
    assert merged["SKX"]["ports"] == "1*p01"


def test_merge_timing_unions_the_cores():
    merged = _merge_timing([{"SKX": {"lat": 4.0, "cpi": 0.5}}, {"ZEN4": {"lat": 4.0, "cpi": 0.5}}])
    assert sorted(merged) == ["SKX", "ZEN4"]


def test_pin_arch_rekeys_the_scalars_and_drops_unrunnable_forms():
    records = [
        {
            "query": "VFMADD132PD (YMM, YMM, YMM)",
            "lat": "4",
            "cpi": "0.50",
            "timing": {
                "SKX": {"lat": 4.0, "cpi": 0.5, "source_kind": "measured", "ports": "1*p01"},
                "ZEN2": {"lat": 5.0, "cpi": 1.0, "source_kind": "measured"},
            },
        },
        {
            "query": "VFMADD132PD (YMM, K, YMM, YMM)",
            "lat": "4",
            "cpi": "0.50",
            "timing": {"SKX": {"lat": 4.0, "cpi": 0.5, "source_kind": "measured"}},
        },
    ]
    pinned = _pin_arch(records, "ZEN2")

    assert [r["query"] for r in pinned] == ["VFMADD132PD (YMM, YMM, YMM)"]
    assert pinned[0]["arch"] == "ZEN2"
    assert pinned[0]["lat"] == "5"
    assert pinned[0]["cpi"] == "1.00"
    assert set(pinned[0]["timing"]) == {"ZEN2"}
    # The input records are left untouched for the unpinned callers.
    assert set(records[0]["timing"]) == {"SKX", "ZEN2"}


def test_pin_arch_reports_a_missing_scalar_as_dash():
    records = [
        {"query": "x", "timing": {"SKX": {"lat": None, "cpi": 0.5, "source_kind": "modeled"}}}
    ]
    pinned = _pin_arch(records, "SKX")
    assert pinned[0]["lat"] == "-"
    assert pinned[0]["source_kinds"] == ["modeled"]


def test_resolve_llm_arch_maps_an_alias_to_its_canonical_id():
    assert _resolve_llm_arch_or_exit("znver4") == "ZEN4"
    assert _resolve_llm_arch_or_exit("skylake-x") == "SKX"
    assert _resolve_llm_arch_or_exit(None) is None


def test_resolve_llm_arch_exits_usage_on_an_unknown_core():
    with pytest.raises(typer.Exit) as excinfo:
        _resolve_llm_arch_or_exit("not-a-core")
    assert excinfo.value.exit_code == LLM_EXIT_USAGE


def test_schema_declares_per_microarchitecture_timing():
    props = _llm_schema_payload()["properties"]["result"]["properties"]
    assert "timing" in props
    entry = props["timing"]["additionalProperties"]["properties"]
    for field in ("lat", "cpi", "ports", "source_kind"):
        assert field in entry
    assert "arch" in props


# ---------------------------------------------------------------------------
# #24 — end-to-end against the dev catalog
# ---------------------------------------------------------------------------


def _skip_without_catalog():
    from simdref.storage import SQLITE_PATH

    if not SQLITE_PATH.exists():
        pytest.skip("no local catalog at data/derived/catalog.db")


def test_llm_query_payload_carries_catalog_provenance():
    _skip_without_catalog()
    result = CliRunner().invoke(cli.app, ["llm", "query", "VFMADD132PD (YMM, YMM, YMM)"])
    if result.exit_code != 0:
        pytest.skip("catalog does not carry VFMADD132PD (YMM, YMM, YMM)")
    payload = json.loads(result.output)
    assert payload["generated_at"]
    assert payload["source_versions"]
    timing = payload["results"][0]["timing"]
    # Latency differs across parts implementing the same ISA, so more than
    # one core must appear and at least two must disagree.
    assert len(timing) > 1
    assert len({entry["lat"] for entry in timing.values()}) > 1


def test_llm_query_arch_pins_one_microarchitecture():
    _skip_without_catalog()
    result = CliRunner().invoke(
        cli.app, ["llm", "query", "VFMADD132PD (YMM, YMM, YMM)", "--arch", "znver4"]
    )
    if result.exit_code != 0:
        pytest.skip("catalog does not carry VFMADD132PD (YMM, YMM, YMM)")
    payload = json.loads(result.output)
    assert payload["arch"] == "ZEN4"
    for record in payload["results"]:
        assert set(record["timing"]) == {"ZEN4"}


def test_llm_query_unknown_arch_exits_usage():
    result = CliRunner().invoke(cli.app, ["llm", "query", "vfmadd132pd", "--arch", "not-a-core"])
    assert result.exit_code == LLM_EXIT_USAGE
    assert "unknown --arch" in result.output


# ---------------------------------------------------------------------------
# #25 — install.sh
# ---------------------------------------------------------------------------


def test_install_script_keeps_build_isolation():
    # A fresh `uv venv` has no setuptools, so --no-build-isolation makes the
    # wheel build fail on the missing backend.
    lines = [
        line
        for line in (REPO_ROOT / "install.sh").read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert any("uv pip install" in line for line in lines)
    assert not any("--no-build-isolation" in line for line in lines)


# ---------------------------------------------------------------------------
# #24 — the documented saturation formula
# ---------------------------------------------------------------------------

SATURATION_DOC = REPO_ROOT / "skill" / "references" / "workflow.md"


def _saturating_chains(lat: float, cpi: float) -> int:
    """Independent chains needed to saturate a unit: ``ceil(lat / cpi)``.

    One chain issues 1 instruction per *lat* cycles, so *n* chains reach
    ``n / lat`` instr/cycle; the unit peaks at ``1 / cpi``. Equality gives
    ``n = lat / cpi``.
    """
    return math.ceil(lat / cpi)


@pytest.mark.parametrize(
    ("lat", "cpi", "expected"),
    [
        # Reference accumulator counts, Agner Fog "Optimizing Assembly" §9.4:
        # Skylake addpd (4c latency, 2/cycle) needs 8; Haswell FMA (5c, 2/cycle) needs 10.
        (4.0, 0.5, 8),
        (5.0, 0.5, 10),
        # cpi >= 1 (no parallel pipe): the chain count is the latency.
        (3.0, 1.0, 3),
        (6.0, 1.27, 5),
    ],
)
def test_saturating_chain_count_matches_the_reference(lat, cpi, expected):
    assert _saturating_chains(lat, cpi) == expected


def test_saturating_chains_actually_reach_peak_throughput():
    # Positive control: the formula must saturate, and one chain fewer must not.
    for lat, cpi in ((4.0, 0.5), (5.0, 0.5), (6.0, 1.27), (3.0, 1.0)):
        peak = 1.0 / cpi
        n = _saturating_chains(lat, cpi)
        assert min(n / lat, peak) == pytest.approx(peak)
        assert (n - 1) / lat < peak


def test_schema_documents_the_saturation_formula():
    props = _llm_schema_payload()["properties"]["result"]["properties"]
    cpi = props["timing"]["additionalProperties"]["properties"]["cpi"]
    assert "ceil(lat / cpi)" in cpi["description"]


def test_workflow_doc_quotes_the_formula_and_a_consistent_example():
    text = SATURATION_DOC.read_text()
    assert "ceil(lat / cpi)" in text
    match = re.search(r"reads `lat=([\d.]+) cpi=([\d.]+)`, giving (\d+) chains", text)
    assert match, "worked example missing from the unrolling section"
    lat, cpi, chains = float(match.group(1)), float(match.group(2)), int(match.group(3))
    assert chains == _saturating_chains(lat, cpi)


def test_workflow_example_numbers_match_the_catalog():
    _skip_without_catalog()
    result = CliRunner().invoke(
        cli.app, ["llm", "query", "VFMADD132PD (YMM, YMM, YMM)", "--arch", "znver4"]
    )
    if result.exit_code != 0:
        pytest.skip("catalog does not carry VFMADD132PD (YMM, YMM, YMM)")
    entry = json.loads(result.output)["results"][0]["timing"]["ZEN4"]
    match = re.search(r"reads `lat=([\d.]+) cpi=([\d.]+)`", SATURATION_DOC.read_text())
    assert (entry["lat"], entry["cpi"]) == (float(match.group(1)), float(match.group(2)))


# ---------------------------------------------------------------------------
# #24 — provenance of a merged timing map
# ---------------------------------------------------------------------------


def test_merge_timing_labels_a_disagreeing_core_mixed():
    measured = {"SKX": {"lat": 6.0, "cpi": 0.5, "source_kind": "measured"}}
    modeled = {"SKX": {"lat": 4.0, "cpi": 0.5, "source_kind": "modeled"}}
    merged = _merge_timing([measured, modeled])
    # The winning latency came from the modeled form, so neither single label holds.
    assert merged["SKX"]["lat"] == 4.0
    assert merged["SKX"]["source_kind"] == "mixed"


def test_merge_timing_keeps_a_unanimous_label():
    a = {"SKX": {"lat": 6.0, "cpi": 0.5, "source_kind": "measured"}}
    b = {"SKX": {"lat": 4.0, "cpi": 0.5, "source_kind": "measured"}}
    assert _merge_timing([a, b])["SKX"]["source_kind"] == "measured"


def test_intrinsic_timing_merges_every_linked_form():
    _skip_without_catalog()
    from simdref.storage import load_intrinsic_from_db, open_db

    with open_db() as conn:
        intrinsic = load_intrinsic_from_db(conn, "_mm256_fmadd_pd")
        if intrinsic is None:
            pytest.skip("catalog does not carry _mm256_fmadd_pd")
        assert len(intrinsic.instruction_refs) > 1, "expected several FMA forms to merge"
        timing = cli._intrinsic_timing(conn, intrinsic)

    assert "ZEN4" in timing and "SKX" in timing
    for core, entry in timing.items():
        assert entry["lat"] is not None or entry["cpi"] is not None, core
        assert entry["source_kind"] in ("measured", "modeled", "mixed"), core
