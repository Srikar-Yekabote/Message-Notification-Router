"""Build code.zip for submission: the code, prompts, caches and docs, never secrets.

The model-response cache (cache/llm/) is included by default, so whoever unpacks the
zip can replay the submitted run exactly, without an API key or any quota.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import List, Tuple

from llm import REPO_ROOT

INCLUDED = ("code", "cache/media", "cache/llm", ".env.example", ".gitignore", "AGENTIC_WORKFLOW.md", "requirements.txt")
SKIPPED_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIPPED_SUFFIXES = {".pyc", ".tmp"}
# Groq keys start with gsk_. Also refuse any line that gives GROQ_API_KEY a real value (the README's
# placeholder is allowed). The pattern is split so this file does not match itself.
SECRET_PATTERNS = (
    re.compile(rb"gsk_[A-Za-z0-9]{20,}"),
    re.compile(rb"GROQ_API_KEY" rb"=[ \t]*(?!your-key-here)[^\s#]"),
)


class SecretFound(Exception):
    """A file that looks like it holds a secret was about to be packaged."""


def package_files(root: Path = REPO_ROOT, include_llm_cache: bool = True) -> List[Path]:
    """The files that go into code.zip, relative to the repository root, sorted."""
    files = []
    for entry in INCLUDED:
        if entry == "cache/llm" and not include_llm_cache:
            continue
        path = root / entry
        candidates = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for p in candidates:
            relative = p.relative_to(root)
            if p.name == ".env" or SKIPPED_PARTS & set(relative.parts) or p.suffix in SKIPPED_SUFFIXES:
                continue
            files.append(relative)
    return files


def build_package(root: Path = REPO_ROOT, destination: Path = None, include_llm_cache: bool = True) -> Tuple[Path, int, int]:
    """Write the zip and return (path, number of files, size in bytes). Raises SecretFound before writing."""
    destination = destination or root / "code.zip"
    files = package_files(root, include_llm_cache)
    for relative in files:
        content = (root / relative).read_bytes()
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            raise SecretFound(f"{relative} looks like it contains an API key; not packaging")
    tmp = destination.with_name(destination.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in files:
            bundle.write(root / relative, relative.as_posix())
    tmp.replace(destination)
    return destination, len(files), destination.stat().st_size
