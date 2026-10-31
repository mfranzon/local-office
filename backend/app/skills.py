"""Skills: folders with a SKILL.md (Anthropic Agent Skills format) that teach the assistant an office task.

SKILL.md starts with YAML frontmatter (name, description) followed by Markdown instructions. Only names and
descriptions are shown to the router; the instructions are loaded when a skill runs (progressive disclosure).
Other Markdown files in the folder can be read by the skill. Scripts are never run: they are listed as ignored.
Built-in skills ship with the app; user skills live in DATA_DIR/skills.
"""
import io
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import BUILTIN_SKILLS, DATA_DIR
from .db import db, get_setting, set_setting

USER_SKILLS = DATA_DIR / "skills"
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_SKILL_BYTES = 2 * 2**20


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    path: Path
    builtin: bool
    enabled: bool = True

    @property
    def references(self) -> list[str]:
        return sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*.md") if p.name != "SKILL.md")

    @property
    def ignored(self) -> list[str]:
        """Files the app never runs or reads (scripts, binaries)."""
        return sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*")
                      if p.is_file() and p.suffix.lower() not in (".md", ".txt") and not p.name.startswith("."))

    def view(self) -> dict:
        return {"name": self.name, "description": self.description, "instructions": self.instructions,
                "builtin": self.builtin, "enabled": self.enabled, "references": self.references, "ignored": self.ignored}

    def read_reference(self, rel: str) -> str:
        path = (self.path / rel).resolve()
        if self.path.resolve() not in path.parents or path.suffix.lower() not in (".md", ".txt") or not path.is_file():
            return f"No file {rel} in this skill."
        return path.read_text(errors="replace")[:20000]


def parse(text: str) -> tuple[dict, str]:
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
    if not m:
        raise ValueError("SKILL.md must start with frontmatter between --- lines (name, description)")
    meta = yaml.safe_load(m[1]) or {}
    if not isinstance(meta, dict):
        raise ValueError("the frontmatter must be key: value pairs")
    name, desc = str(meta.get("name", "")).strip(), str(meta.get("description", "")).strip()
    if not NAME.match(name) or len(name) > 64:
        raise ValueError("name must be lowercase letters, digits and hyphens (e.g. invoice-table)")
    if not desc:
        raise ValueError("a description is required: it tells the assistant when to use the skill")
    return {"name": name, "description": desc[:1024]}, m[2].strip()


def _load(folder: Path, builtin: bool, disabled: set[str]) -> Skill | None:
    try:
        meta, body = parse((folder / "SKILL.md").read_text())
    except (OSError, ValueError):
        return None
    return Skill(meta["name"], meta["description"], body, folder, builtin, meta["name"] not in disabled)


def all_skills() -> list[Skill]:
    with db() as conn:
        disabled = set(get_setting(conn, "skills.disabled", []))
    found: dict[str, Skill] = {}
    for root, builtin in ((BUILTIN_SKILLS, True), (USER_SKILLS, False)):   # a user skill overrides a built-in one
        for folder in sorted(root.glob("*/")) if root.is_dir() else []:
            if skill := _load(folder, builtin, disabled):
                found[skill.name] = skill
    return sorted(found.values(), key=lambda s: s.name)


def get(name: str) -> Skill | None:
    return next((s for s in all_skills() if s.name == name), None)


def enabled() -> list[Skill]:
    return [s for s in all_skills() if s.enabled]


def set_enabled(name: str, on: bool):
    with db() as conn:
        disabled = set(get_setting(conn, "skills.disabled", []))
        disabled = disabled - {name} if on else disabled | {name}
        set_setting(conn, "skills.disabled", sorted(disabled))


def save(name: str, description: str, instructions: str) -> Skill:
    """Creates or edits a user skill."""
    text = yaml.safe_dump({"name": name, "description": description}, allow_unicode=True, sort_keys=False)
    content = f"---\n{text}---\n\n{instructions.strip()}\n"
    parse(content)   # validates
    folder = USER_SKILLS / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(content)
    return get(name)


def import_file(filename: str, data: bytes) -> Skill:
    """Imports a SKILL.md, or a .zip containing a skill folder."""
    if len(data) > MAX_SKILL_BYTES:
        raise ValueError("the skill is too large (2 MB at most)")
    if not filename.lower().endswith(".zip"):
        meta, body = parse(data.decode("utf-8", errors="replace"))
        return save(meta["name"], meta["description"], body)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        md = sorted((n for n in z.namelist() if n.endswith("SKILL.md")), key=len)
        if not md:
            raise ValueError("no SKILL.md in the zip")
        prefix = md[0][: -len("SKILL.md")]
        meta, _ = parse(z.read(md[0]).decode("utf-8", errors="replace"))
        folder = USER_SKILLS / meta["name"]
        shutil.rmtree(folder, ignore_errors=True)
        for info in z.infolist():
            rel = info.filename[len(prefix):] if info.filename.startswith(prefix) else None
            if not rel or info.is_dir() or ".." in Path(rel).parts or Path(rel).is_absolute():
                continue
            dest = folder / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(info))   # stored, never executed
    return get(meta["name"])


def delete(name: str) -> bool:
    folder = USER_SKILLS / name
    if not (folder / "SKILL.md").is_file():
        return False
    shutil.rmtree(folder)
    return True
