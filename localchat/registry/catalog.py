"""Discover trusted Python tools and native ADK skills at startup."""
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from google.adk.skills import load_skill_from_dir
from google.adk.tools import FunctionTool
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset

RESERVED_TOOL_NAMES = {"load_skill", "load_skill_resource", "run_skill_script"}

class SkillLoadError(ValueError):
    """An explicitly requested skill cannot be used."""

@dataclass
class Registry:
    tools: list
    skills: dict

    def toolset(self):
        return SkillToolset(
            skills=list(self.skills.values()),
            discovery_mode=SkillDiscoveryMode.EAGER,
        )

    def prepare_skills(self, names, available_tools):
        selected = []
        for name in dict.fromkeys(names):
            skill = self.skills.get(name)
            if skill is None:
                raise SkillLoadError(f"Unknown skill: {name}. Choose a name from /capabilities.")
            required = skill.frontmatter.metadata.get("required_tools", [])
            missing = sorted(set(required) - available_tools)
            if missing:
                raise SkillLoadError(f"Skill '{name}' requires unavailable tools: {', '.join(missing)}")
            selected.append(skill)
        return selected

    def describe_skills(self):
        return [
            {"name": name, "description": skill.frontmatter.description,
             "required_tools": skill.frontmatter.metadata.get("required_tools", [])}
            for name, skill in self.skills.items()
        ]


def load_registry():
    root = Path(__file__).resolve().parents[1] / "capabilities"
    tools = []
    names = set(RESERVED_TOOL_NAMES)
    for path in sorted((root / "tools").glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = import_module(f"localchat.capabilities.tools.{path.stem}")
        for function in module.build_tools():
            tool = function if isinstance(function, FunctionTool) else FunctionTool(function)
            if tool.name in names:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            names.add(tool.name)
            tools.append(tool)
    skills = {}
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        skill = load_skill_from_dir(path.parent)
        name = skill.frontmatter.name
        if name in skills:
            raise ValueError(f"Duplicate skill name: {name}")
        skills[name] = skill
    return Registry(tools, skills)
