from __future__ import annotations

import os
import tempfile
from pathlib import Path

APTO3D_COMPONENT_ROOT = Path("/config/custom_components/apto3d")
_MAX_READ_CHARS = 2_000_000


def _root(root: Path = APTO3D_COMPONENT_ROOT) -> Path:
    return root.resolve(strict=False)


def resolve_apto3d_path(path: str, *, root: Path = APTO3D_COMPONENT_ROOT) -> Path:
    raw = path.strip()
    if not raw:
        raise ValueError("path is required.")

    base = _root(root)
    candidate = Path(raw)
    if candidate.is_absolute():
        candidate = candidate.resolve(strict=False)
    else:
        candidate = (base / candidate).resolve(strict=False)

    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError("path must stay inside /config/custom_components/apto3d/") from exc

    if not relative.parts:
        raise ValueError("path must identify a component file.")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("invalid path.")
    _validate_component_file(relative)
    return candidate


def _validate_component_file(relative: Path) -> None:
    parts = relative.parts
    if len(parts) == 1:
        name = parts[0]
        if name.endswith(".py") or name in {"manifest.json", "strings.json"}:
            return
    if len(parts) == 2 and parts[0] == "translations" and parts[1].endswith(".json"):
        return
    raise ValueError(
        "unsupported component file; allowed: root .py, manifest.json, strings.json, "
        "and translations/*.json"
    )


def read_component_file(path: str, *, root: Path = APTO3D_COMPONENT_ROOT, max_chars: int = 500_000) -> dict:
    target = resolve_apto3d_path(path, root=root)
    if not target.is_file() or target.is_symlink():
        raise ValueError("file does not exist or is not a regular component file.")
    data = target.read_bytes()
    if b"\x00" in data:
        raise ValueError("binary files are not supported.")
    content = data.decode("utf-8")
    max_chars = max(1, min(int(max_chars), _MAX_READ_CHARS))
    return {
        "path": str(target),
        "bytes": len(data),
        "truncated": len(content) > max_chars,
        "content": content[:max_chars],
    }


def write_component_file(path: str, content: str, *, root: Path = APTO3D_COMPONENT_ROOT) -> dict:
    base = _root(root)
    base.mkdir(parents=True, exist_ok=True)
    target = resolve_apto3d_path(path, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Re-resolve after directory creation so newly introduced symlinks cannot escape.
    target = resolve_apto3d_path(path, root=root)
    if target.exists() and target.is_symlink():
        raise ValueError("refusing to replace a symlink.")

    encoded = content.encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return {"path": str(target), "bytes": len(encoded), "success": True}


def list_component_files(*, root: Path = APTO3D_COMPONENT_ROOT) -> list[str]:
    base = _root(root)
    if not base.exists():
        return []
    files: list[str] = []
    for candidate in base.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            relative = candidate.resolve().relative_to(base)
            _validate_component_file(relative)
        except (ValueError, OSError):
            continue
        files.append(relative.as_posix())
    return sorted(files)
