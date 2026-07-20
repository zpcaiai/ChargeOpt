from __future__ import annotations

import json
from pathlib import Path

from scripts.install_chargeopt_skills import install, project_skill_names

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    header, body = text[4:].split("\n---\n", 1)
    values = {}
    for line in header.splitlines():
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    assert "## Workflow" in body or "## Orchestration workflow" in body
    assert "## Acceptance" in body or "## Completion report" in body
    return values


def test_skill_suite_has_valid_metadata_and_registry():
    manifest = json.loads((SKILLS / "manifest.json").read_text(encoding="utf-8"))
    entries = {item["name"]: item for item in manifest["skills"]}
    local_names = {name for name, item in entries.items() if not item.get("external")}

    assert manifest["suite"] == "chargeopt-energy-platform"
    assert len(local_names) == 12
    assert set(project_skill_names()) == local_names
    registry = (SKILLS / "README.md").read_text(encoding="utf-8")
    for name in local_names:
        path = SKILLS / name / "SKILL.md"
        metadata = _frontmatter(path)
        assert metadata["name"] == name
        assert len(metadata["description"]) >= 120
        assert "Use " in metadata["description"]
        assert name in registry


def test_skill_dependency_graph_is_known_acyclic_and_phase_ordered():
    manifest = json.loads((SKILLS / "manifest.json").read_text(encoding="utf-8"))
    entries = {item["name"]: item for item in manifest["skills"]}
    visited: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        assert name in entries
        if name in visited:
            return
        assert name not in active, f"dependency cycle at {name}"
        active.add(name)
        for dependency in entries[name].get("dependencies", []):
            assert entries[dependency]["phase"] <= entries[name]["phase"]
            visit(dependency)
        active.remove(name)
        visited.add(name)

    for skill_name in entries:
        visit(skill_name)
    assert visited == set(entries)


def test_orchestrator_has_program_and_realistic_evals():
    orchestrator = SKILLS / "chargeopt-energy-platform"
    program = (orchestrator / "references" / "program.md").read_text(encoding="utf-8")
    evals = json.loads((orchestrator / "evals" / "evals.json").read_text(encoding="utf-8"))

    for phase in range(7):
        assert f"Phase {phase}" in program
    assert len(evals["evals"]) == 3
    assert {item["id"] for item in evals["evals"]} == {1, 2, 3}
    assert all(item["expected_output"] for item in evals["evals"])


def test_skill_installer_creates_links_and_is_idempotent(tmp_path):
    first = install(tmp_path, check_only=False)
    second = install(tmp_path, check_only=False)

    assert len(first) == len(project_skill_names())
    assert all(message.startswith("installed ") for message in first)
    assert all(message.startswith("ok ") for message in second)
    for name in project_skill_names():
        assert (tmp_path / name / "SKILL.md").is_file()
