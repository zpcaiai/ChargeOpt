"""Install repository-managed ChargeOpt skills into the local Codex skill root."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills"
DEFAULT_TARGET = Path.home() / ".codex" / "skills"


def project_skill_names() -> list[str]:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    return [item["name"] for item in manifest["skills"] if not item.get("external")]


def install(target: Path, *, check_only: bool) -> list[str]:
    messages: list[str] = []
    for name in project_skill_names():
        source = SOURCE / name
        skill_file = source / "SKILL.md"
        if not skill_file.is_file():
            raise RuntimeError(f"Missing source skill: {skill_file}")
        destination = target / name
        if destination.is_symlink():
            if destination.resolve() != source.resolve():
                raise RuntimeError(f"Skill link points elsewhere: {destination}")
            messages.append(f"ok {name}")
            continue
        if destination.exists():
            raise RuntimeError(f"Refusing to replace existing skill directory: {destination}")
        if check_only:
            messages.append(f"missing {name}")
            continue
        target.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source, target_is_directory=True)
        messages.append(f"installed {name}")
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for message in install(args.target.expanduser(), check_only=args.check):
        print(message)


if __name__ == "__main__":
    main()
