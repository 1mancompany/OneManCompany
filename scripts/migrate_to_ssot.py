#!/usr/bin/env python3
"""One-time migration for Single Source of Truth refactoring.

Adds runtime: section to employee profiles that lack it.
Infers project.yaml status from task_tree.yaml if status is missing/stale.
Run once after deploying the refactored code.
"""
import yaml
from pathlib import Path

from onemancompany.core.config import EMPLOYEES_DIR, PROJECTS_DIR


def migrate_employee_profiles():
    """Add runtime: section with defaults to profiles that lack it."""
    if not EMPLOYEES_DIR.exists():
        return
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile = emp_dir / "profile.yaml"
        if not profile.exists():
            continue
        data = yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
        if "runtime" not in data:
            data["runtime"] = {
                "status": "idle",
                "is_listening": False,
                "current_task_summary": "",
                "api_online": True,
                "needs_setup": False,
            }
            profile.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            print(f"  Added runtime: to {emp_dir.name}/profile.yaml")


def migrate_project_statuses():
    """Infer project.yaml status from task_tree.yaml if status is missing."""
    if not PROJECTS_DIR.exists():
        return
    for pdir in sorted(PROJECTS_DIR.iterdir()):
        if not pdir.is_dir():
            continue
        pyaml = pdir / "project.yaml"
        if not pyaml.exists():
            continue
        data = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
        if data.get("status"):
            continue  # already has status
        # Try to infer from tree
        tree_path = pdir / "task_tree.yaml"
        if tree_path.exists():
            tree = yaml.safe_load(tree_path.read_text(encoding="utf-8")) or {}
            nodes = tree.get("nodes", [])
            if not nodes:
                data["status"] = "pending"
            elif all(n.get("status") in ("accepted", "completed") for n in nodes):
                data["status"] = "completed"
            elif any(n.get("status") == "failed" for n in nodes):
                data["status"] = "failed"
            else:
                data["status"] = "in_progress"
        else:
            data["status"] = "pending"
        pyaml.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        print(f"  Set status={data['status']} for project {pdir.name}")


if __name__ == "__main__":
    print("Migrating employee profiles...")
    migrate_employee_profiles()
    print("Migrating project statuses...")
    migrate_project_statuses()
    print("Done.")
