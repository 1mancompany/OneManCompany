#!/usr/bin/env python3
"""Import a GitHub repo as a OneManCompany talent package.

Usage:
    .venv/bin/python scripts/import_github_talent.py https://github.com/deanpeters/Product-Manager-Skills.git
    .venv/bin/python scripts/import_github_talent.py https://github.com/user/repo --talent-id my-talent --non-interactive
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TALENTS_DIR = PROJECT_ROOT / "src" / "onemancompany" / "talent_market" / "talents"

ROLE_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "Manager": [
        ("product manager", 5), ("product management", 5), ("roadmap", 3),
        ("prd", 3), ("sprint", 2), ("stakeholder", 2), ("prioritization", 3),
        ("strategy", 2), ("okr", 2), ("discovery", 2), ("user story", 3),
        ("positioning", 2), ("persona", 2), ("backlog", 2),
    ],
    "Engineer": [
        ("engineer", 3), ("coding", 3), ("software developer", 5),
        ("programming", 3), ("architecture", 2), ("debug", 2), ("api", 1),
    ],
    "Designer": [
        ("ux design", 5), ("wireframe", 3), ("prototype", 2), ("figma", 3),
        ("ui design", 5), ("interaction design", 4), ("visual design", 4),
    ],
    "Analyst": [
        ("data analyst", 5), ("metrics", 1), ("analytics", 2),
        ("dashboard", 2), ("sql", 3),
    ],
    "Marketing": [
        ("marketing", 3), ("seo", 3), ("campaign", 2), ("brand", 1),
        ("content marketing", 4),
    ],
    "QA": [
        ("qa", 3), ("quality assurance", 5), ("test plan", 3),
        ("automation test", 3),
    ],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SkillInfo:
    name: str           # kebab-case
    description: str    # from frontmatter or first paragraph
    type: str           # component | interactive | workflow
    theme: str          # from frontmatter
    estimated_time: str # from frontmatter
    source_path: Path
    content: str        # markdown body (frontmatter stripped)


@dataclass
class RepoAnalysis:
    repo_name: str
    readme_title: str
    readme_description: str
    has_claude_md: bool
    claude_md_content: str
    has_agents_md: bool
    agents_md_content: str
    skills: list[SkillInfo]
    tool_files: list[Path]
    has_launch_sh: bool
    detected_role: str
    detected_hosting: str


@dataclass
class ImportConfig:
    talent_id: str
    name: str
    description: str
    role: str
    hosting: str
    skills_to_import: list[SkillInfo]
    system_prompt_content: str


# ---------------------------------------------------------------------------
# Git clone
# ---------------------------------------------------------------------------

def repo_name_from_url(url: str) -> str:
    """Extract repository name from a git URL."""
    # Handle URLs like https://github.com/user/Repo-Name.git or git@...
    name = url.rstrip("/").rsplit("/", 1)[-1]
    name = name.removesuffix(".git")
    return name


def clone_repo(url: str, branch: str | None, dest: Path) -> Path:
    """Clone repo with --depth 1. Try branch, fall back to main/master."""
    branches_to_try = [branch] if branch else ["main", "master"]
    for b in branches_to_try:
        cmd = ["git", "clone", "--depth", "1", "--branch", b, url, str(dest)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  Cloned branch '{b}' successfully.")
            return dest
        # Clean up failed attempt
        if dest.exists():
            shutil.rmtree(dest)
    # Last resort: clone without specifying branch
    cmd = ["git", "clone", "--depth", "1", url, str(dest)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: git clone failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("  Cloned default branch.")
    return dest


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    body = parts[2].lstrip("\n")
    return meta, body


def extract_first_paragraph(text: str) -> str:
    """Extract the first non-heading paragraph from markdown."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def extract_readme_info(readme_path: Path) -> tuple[str, str]:
    """Extract title and first meaningful paragraph from README.md."""
    if not readme_path.exists():
        return "", ""
    text = readme_path.read_text(encoding="utf-8")
    title = ""
    description = ""
    in_code_block = False
    for line in text.splitlines():
        stripped = line.strip()
        # Track code fences
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if stripped.startswith("# ") and not title:
            title = stripped.lstrip("# ").strip()
        elif (
            stripped
            and not stripped.startswith("#")
            and not stripped.startswith("[")
            and not stripped.startswith("|")
            and not stripped.startswith("![")
            and not stripped.startswith("---")
            and not stripped.startswith(">")
            and title
            and not description
        ):
            # Strip markdown bold/italic markers
            description = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", stripped)
    return title, description


def to_kebab_case(s: str) -> str:
    """Convert a string to kebab-case."""
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s.lower()


def is_agent_prompt(claude_md: str) -> bool:
    """Check if CLAUDE.md looks like an agent system prompt (vs dev docs)."""
    lower = claude_md[:500].lower()
    agent_signals = ["you are", "your role", "your responsibility", "as an agent"]
    dev_signals = ["## project", "## build", "## test", "coding style", "## development"]
    agent_score = sum(1 for s in agent_signals if s in lower)
    dev_score = sum(1 for s in dev_signals if s in lower)
    return agent_score > dev_score


# ---------------------------------------------------------------------------
# Skill discovery
# ---------------------------------------------------------------------------

def discover_skills(repo_dir: Path) -> list[SkillInfo]:
    """Discover skill files in priority order."""
    skills: list[SkillInfo] = []

    # Priority 1: skills/*/SKILL.md (Product-Manager-Skills pattern)
    skills_dir = repo_dir / "skills"
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_dir.is_dir() and skill_md.exists():
                skills.append(_parse_skill_file(skill_md, skill_dir.name))
        if skills:
            return skills

    # Priority 2: skills/*.md (flat)
    if skills_dir.exists():
        for md_file in sorted(skills_dir.glob("*.md")):
            if md_file.is_file():
                skills.append(_parse_skill_file(md_file, md_file.stem))
        if skills:
            return skills

    # Priority 3: prompts/*.md
    prompts_dir = repo_dir / "prompts"
    if prompts_dir.exists():
        for md_file in sorted(prompts_dir.glob("*.md")):
            if md_file.is_file():
                skills.append(_parse_skill_file(md_file, md_file.stem))
        if skills:
            return skills

    # Priority 4: .claude/skills/*/SKILL.md
    claude_skills = repo_dir / ".claude" / "skills"
    if claude_skills.exists():
        for skill_dir in sorted(claude_skills.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_dir.is_dir() and skill_md.exists():
                skills.append(_parse_skill_file(skill_md, skill_dir.name))

    return skills


def _parse_skill_file(path: Path, default_name: str) -> SkillInfo:
    """Parse a single SKILL.md into SkillInfo."""
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    name = meta.get("name", default_name)
    description = meta.get("description", extract_first_paragraph(body))
    skill_type = meta.get("type", "component")
    theme = meta.get("theme", "")
    estimated_time = meta.get("estimated_time", "")
    return SkillInfo(
        name=to_kebab_case(name),
        description=description[:200] if description else "",
        type=skill_type,
        theme=theme,
        estimated_time=estimated_time,
        source_path=path,
        content=body,
    )


# ---------------------------------------------------------------------------
# Repo analysis
# ---------------------------------------------------------------------------

def detect_repo_structure(repo_dir: Path, repo_name: str | None = None) -> RepoAnalysis:
    """Analyze a cloned repo and return structured analysis."""
    repo_name = repo_name or repo_dir.name

    # README
    readme_title, readme_desc = extract_readme_info(repo_dir / "README.md")

    # CLAUDE.md
    claude_md_path = repo_dir / "CLAUDE.md"
    has_claude_md = claude_md_path.exists()
    claude_md_content = claude_md_path.read_text(encoding="utf-8") if has_claude_md else ""

    # AGENTS.md
    agents_md_path = repo_dir / "AGENTS.md"
    has_agents_md = agents_md_path.exists()
    agents_md_content = agents_md_path.read_text(encoding="utf-8") if has_agents_md else ""

    # Skills
    skills = discover_skills(repo_dir)

    # Tool files (Python files with @tool decorator)
    tool_files: list[Path] = []
    for py_file in repo_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            if "@tool" in content:
                tool_files.append(py_file.relative_to(repo_dir))
        except (UnicodeDecodeError, PermissionError):
            pass

    # launch.sh
    has_launch_sh = (repo_dir / "launch.sh").exists()

    # Role detection
    detected_role = detect_role(readme_title, readme_desc, claude_md_content)

    # Hosting detection
    detected_hosting = "self" if has_launch_sh else "company"

    return RepoAnalysis(
        repo_name=repo_name,
        readme_title=readme_title,
        readme_description=readme_desc,
        has_claude_md=has_claude_md,
        claude_md_content=claude_md_content,
        has_agents_md=has_agents_md,
        agents_md_content=agents_md_content,
        skills=skills,
        tool_files=tool_files,
        has_launch_sh=has_launch_sh,
        detected_role=detected_role,
        detected_hosting=detected_hosting,
    )


def detect_role(title: str, description: str, claude_md: str) -> str:
    """Detect role from weighted keywords in README + CLAUDE.md."""
    combined = f"{title} {description} {claude_md}".lower()
    scores: dict[str, int] = {}
    for role, kw_weights in ROLE_KEYWORDS.items():
        scores[role] = sum(combined.count(kw) * weight for kw, weight in kw_weights)
    if not scores or max(scores.values()) == 0:
        return "Engineer"  # default
    return max(scores, key=scores.get)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Interactive confirmation
# ---------------------------------------------------------------------------

def interactive_confirm(analysis: RepoAnalysis) -> ImportConfig:
    """Interactively confirm import settings with the user."""
    default_id = to_kebab_case(analysis.repo_name)
    default_name = analysis.readme_title or analysis.repo_name
    default_desc = analysis.readme_description or f"Talent imported from {analysis.repo_name}"

    print("\n=== Import Configuration ===\n")
    print(f"  Repository:   {analysis.repo_name}")
    print(f"  Skills found: {len(analysis.skills)}")
    print(f"  CLAUDE.md:    {'yes' if analysis.has_claude_md else 'no'}")
    print(f"  AGENTS.md:    {'yes' if analysis.has_agents_md else 'no'}")
    print(f"  Tool files:   {len(analysis.tool_files)}")
    print(f"  launch.sh:    {'yes' if analysis.has_launch_sh else 'no'}")
    print()

    talent_id = _prompt("Talent ID", default_id)
    name = _prompt("Name", default_name)
    description = _prompt("Description", default_desc)
    role = _prompt("Role", analysis.detected_role)
    hosting = _prompt("Hosting (company/self)", analysis.detected_hosting)

    # Skill selection
    print(f"\n  Found {len(analysis.skills)} skills:")
    for i, s in enumerate(analysis.skills, 1):
        print(f"    {i:3d}. {s.name} ({s.type})")
    import_all = _prompt("Import all skills? (y/n)", "y")
    if import_all.lower() == "y":
        skills_to_import = list(analysis.skills)
    else:
        indices_str = input("  Enter skill numbers to import (comma-separated): ").strip()
        indices = [int(x.strip()) - 1 for x in indices_str.split(",") if x.strip().isdigit()]
        skills_to_import = [analysis.skills[i] for i in indices if 0 <= i < len(analysis.skills)]

    system_prompt = _build_system_prompt(analysis, name, len(skills_to_import))

    return ImportConfig(
        talent_id=talent_id,
        name=name,
        description=description,
        role=role,
        hosting=hosting,
        skills_to_import=skills_to_import,
        system_prompt_content=system_prompt,
    )


def non_interactive_config(analysis: RepoAnalysis, overrides: dict) -> ImportConfig:
    """Build config without user interaction."""
    talent_id = overrides.get("talent_id") or to_kebab_case(analysis.repo_name)
    name = analysis.readme_title or analysis.repo_name
    description = analysis.readme_description or f"Talent imported from {analysis.repo_name}"
    role = overrides.get("role") or analysis.detected_role
    hosting = overrides.get("hosting") or analysis.detected_hosting

    system_prompt = _build_system_prompt(analysis, name, len(analysis.skills))

    return ImportConfig(
        talent_id=talent_id,
        name=name,
        description=description,
        role=role,
        hosting=hosting,
        skills_to_import=list(analysis.skills),
        system_prompt_content=system_prompt,
    )


def _prompt(label: str, default: str) -> str:
    val = input(f"  {label} [{default}]: ").strip()
    return val if val else default


def _build_system_prompt(analysis: RepoAnalysis, name: str, skill_count: int) -> str:
    """Build a system prompt, incorporating CLAUDE.md when present.

    CLAUDE.md is always preserved as a file in the talent directory for
    Claude CLI to discover automatically.  The system_prompt_template is
    a concise persona instruction for the LangChain / company-hosted path.
    """
    role_lower = analysis.detected_role.lower()
    base = (
        f"You are a senior {role_lower} equipped with {skill_count} professional frameworks. "
        f"Use your skills library to select the right tool for each challenge. "
        f"Ground analysis in frameworks, not generic advice."
    )
    if analysis.has_claude_md and is_agent_prompt(analysis.claude_md_content):
        # CLAUDE.md is already a persona prompt — use it directly
        return analysis.claude_md_content
    return base


# ---------------------------------------------------------------------------
# Personality tag inference
# ---------------------------------------------------------------------------

def infer_personality_tags(role: str, skills: list[SkillInfo]) -> list[str]:
    """Infer personality tags from role and skills."""
    tags: set[str] = set()
    role_tags = {
        "Manager": ["strategic", "analytical", "organized"],
        "Engineer": ["systematic", "thorough", "autonomous"],
        "Designer": ["creative", "empathetic", "detail-oriented"],
        "Analyst": ["analytical", "data-driven", "precise"],
        "Marketing": ["creative", "strategic", "communicative"],
        "QA": ["meticulous", "systematic", "thorough"],
    }
    tags.update(role_tags.get(role, ["professional"]))

    # Add tags from skill themes
    skill_types = {s.type for s in skills}
    if "interactive" in skill_types:
        tags.add("collaborative")
    if "workflow" in skill_types:
        tags.add("process-oriented")
    return sorted(tags)


# ---------------------------------------------------------------------------
# Generate talent package
# ---------------------------------------------------------------------------

def generate_talent_package(config: ImportConfig, analysis: RepoAnalysis, dry_run: bool = False) -> Path:
    """Write the talent package to TALENTS_DIR."""
    talent_dir = TALENTS_DIR / config.talent_id

    if dry_run:
        print(f"\n=== DRY RUN — would write to {talent_dir} ===\n")
        _preview_package(config, analysis)
        return talent_dir

    if talent_dir.exists():
        print(f"\n  WARNING: {talent_dir} already exists. Overwriting...")
        shutil.rmtree(talent_dir)

    talent_dir.mkdir(parents=True)
    skills_dir = talent_dir / "skills"
    skills_dir.mkdir()

    # Write skills
    for skill in config.skills_to_import:
        skill_filename = f"{skill.name}.md"
        header_parts = []
        if skill.type:
            header_parts.append(f"Type: {skill.type}")
        if skill.theme:
            header_parts.append(f"Theme: {skill.theme}")
        if skill.estimated_time:
            header_parts.append(f"Time: {skill.estimated_time}")

        # Build skill content: heading + optional metadata line + body
        heading = skill.name.replace("-", " ").title()
        lines = [f"# {heading}\n"]
        if header_parts:
            lines.append(f"> {' | '.join(header_parts)}\n")
        lines.append("")
        lines.append(skill.content)
        (skills_dir / skill_filename).write_text("\n".join(lines), encoding="utf-8")

    # Write agent/prompt_sections if AGENTS.md exists
    if analysis.has_agents_md:
        agent_dir = talent_dir / "agent"
        prompt_sections_dir = agent_dir / "prompt_sections"
        prompt_sections_dir.mkdir(parents=True)
        (prompt_sections_dir / "agents_guide.md").write_text(
            analysis.agents_md_content, encoding="utf-8"
        )
        manifest = {
            "prompt_sections": [
                {
                    "name": "agents_guide",
                    "file": "prompt_sections/agents_guide.md",
                    "priority": 40,
                }
            ]
        }
        (agent_dir / "manifest.yaml").write_text(
            yaml.dump(manifest, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    # Copy CLAUDE.md if present (Claude CLI auto-discovers it in cwd)
    if analysis.has_claude_md:
        (talent_dir / "CLAUDE.md").write_text(
            analysis.claude_md_content, encoding="utf-8"
        )

    # Copy tool files if any
    if analysis.tool_files:
        tools_dir = talent_dir / "tools"
        tools_dir.mkdir(exist_ok=True)
        for tf in analysis.tool_files:
            src = Path(analysis.skills[0].source_path).parents[1] / tf if analysis.skills else None
            # Tool files are relative to repo root — we stored them relative
            # We need the actual repo dir. Derive from first skill's path.
            if analysis.skills:
                repo_root = analysis.skills[0].source_path
                # Walk up to find repo root (has .git)
                p = repo_root
                while p.parent != p:
                    if (p / ".git").exists():
                        break
                    p = p.parent
                src = p / tf
                if src.exists():
                    dest = tools_dir / tf.name
                    shutil.copy2(src, dest)

    # Write profile.yaml
    profile = {
        "id": config.talent_id,
        "name": config.name,
        "description": config.description,
        "role": config.role,
        "remote": False,
        "api_provider": "openrouter",
        "llm_model": "",
        "temperature": 0.4,
        "hosting": config.hosting,
        "auth_method": "api_key",
        "hiring_fee": 1.00,
        "salary_per_1m_tokens": 0.0,
        "skills": [s.name for s in config.skills_to_import],
        "personality_tags": infer_personality_tags(config.role, config.skills_to_import),
        "system_prompt_template": config.system_prompt_content,
    }
    profile_yaml = yaml.dump(
        profile,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )
    (talent_dir / "profile.yaml").write_text(profile_yaml, encoding="utf-8")

    return talent_dir


def _preview_package(config: ImportConfig, analysis: RepoAnalysis):
    """Print what would be generated."""
    print(f"  talent_id:   {config.talent_id}")
    print(f"  name:        {config.name}")
    print(f"  description: {config.description[:80]}...")
    print(f"  role:        {config.role}")
    print(f"  hosting:     {config.hosting}")
    print(f"  skills:      {len(config.skills_to_import)}")
    for s in config.skills_to_import:
        print(f"    - {s.name} ({s.type})")
    if analysis.has_claude_md:
        print("  CLAUDE.md:   yes (preserved for Claude CLI)")
    if analysis.has_agents_md:
        print("  agent/prompt_sections/agents_guide.md: yes")
    print(f"  system_prompt: {config.system_prompt_content[:80]}...")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import a GitHub repo as a OneManCompany talent package."
    )
    parser.add_argument("url", help="Git repository URL to import")
    parser.add_argument("--talent-id", help="Override auto-detected talent ID")
    parser.add_argument("--role", help="Override role (Engineer/Manager/Designer/Analyst/Marketing/QA)")
    parser.add_argument("--hosting", help="Override hosting (company/self)")
    parser.add_argument("--branch", help="Git branch to clone (default: main, fallback master)")
    parser.add_argument("--non-interactive", action="store_true", help="Skip interactive prompts")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    repo_name = repo_name_from_url(args.url)
    print(f"\n[1/4] Cloning repository ({repo_name})...")
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = clone_repo(args.url, args.branch, Path(tmpdir) / "repo")

        print(f"[2/4] Analyzing repository structure...")
        analysis = detect_repo_structure(repo_dir, repo_name=repo_name)

        print(f"  Repository:   {analysis.repo_name}")
        print(f"  Title:        {analysis.readme_title}")
        print(f"  Skills:       {len(analysis.skills)}")
        print(f"  Role:         {analysis.detected_role}")
        print(f"  Hosting:      {analysis.detected_hosting}")

        print(f"[3/4] Configuring import...")
        overrides = {}
        if args.talent_id:
            overrides["talent_id"] = args.talent_id
        if args.role:
            overrides["role"] = args.role
        if args.hosting:
            overrides["hosting"] = args.hosting

        if args.non_interactive:
            config = non_interactive_config(analysis, overrides)
        else:
            config = interactive_confirm(analysis)
            # Apply CLI overrides on top of interactive choices
            if args.talent_id:
                config.talent_id = args.talent_id
            if args.role:
                config.role = args.role
            if args.hosting:
                config.hosting = args.hosting

        print(f"[4/4] Generating talent package...")
        talent_dir = generate_talent_package(config, analysis, dry_run=args.dry_run)

        if not args.dry_run:
            skill_count = len(config.skills_to_import)
            print(f"\n  Done! Talent package written to:")
            print(f"    {talent_dir}")
            print(f"    - profile.yaml")
            print(f"    - skills/ ({skill_count} files)")
            if analysis.has_claude_md:
                print(f"    - CLAUDE.md (preserved for Claude CLI)")
            if analysis.has_agents_md:
                print(f"    - agent/manifest.yaml + prompt_sections/")
            print()


if __name__ == "__main__":
    main()
