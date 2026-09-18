from pathlib import Path

import pytest

from apto3d_files import (
    list_component_files,
    read_component_file,
    resolve_apto3d_path,
    write_component_file,
)


def test_allowed_component_files(tmp_path: Path):
    for path in ("__init__.py", "api.py", "manifest.json", "strings.json", "translations/pt-BR.json"):
        resolved = resolve_apto3d_path(path, root=tmp_path)
        assert resolved.is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("path", [
    "../secrets.yaml",
    "../../configuration.yaml",
    "/tmp/escape.py",
    "translations/../../escape.py",
    "www/index.html",
    "translations/nested/en.json",
    "notes.txt",
])
def test_rejects_escape_and_unsupported_files(tmp_path: Path, path: str):
    with pytest.raises(ValueError):
        resolve_apto3d_path(path, root=tmp_path)


def test_atomic_write_read_and_list(tmp_path: Path):
    result = write_component_file("__init__.py", "VALUE = 1\n", root=tmp_path)
    assert result["success"] is True
    assert result["bytes"] == len(b"VALUE = 1\n")
    read = read_component_file("__init__.py", root=tmp_path)
    assert read["content"] == "VALUE = 1\n"
    assert list_component_files(root=tmp_path) == ["__init__.py"]


def test_creates_component_and_translation_directories(tmp_path: Path):
    root = tmp_path / "custom_components" / "apto3d"
    write_component_file("translations/en.json", "{}", root=root)
    assert (root / "translations" / "en.json").read_text() == "{}"


def test_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "apto3d"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "translations").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        write_component_file("translations/en.json", "{}", root=root)


def test_rejects_existing_file_symlink(tmp_path: Path):
    root = tmp_path / "apto3d"
    outside = tmp_path / "outside.py"
    root.mkdir()
    outside.write_text("secret")
    (root / "evil.py").symlink_to(outside)
    with pytest.raises(ValueError):
        read_component_file("evil.py", root=root)
    with pytest.raises(ValueError):
        write_component_file("evil.py", "changed", root=root)
