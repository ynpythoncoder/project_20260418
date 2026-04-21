# tools/developments/management/structure_tools.py
# File Path: tools/developments/management/structure_tools.py
# Programming Language: Python 3
# Framework: Django
# Dependencies: standard library, django

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Iterable


from django.conf import settings
from django.core.management import call_command


DEFAULT_MANIFEST_FILE_NAME = "structure_manifest.json"
DEFAULT_AUTOGEN_FILE_NAME = "installed_apps_autogen.py"


@dataclass
class CandidateApp:
    source_layer_dir: Path
    source_app_dir: Path
    target_app_dir: Path
    app_name: str
    dotted_app_path: str
    skip_reason: str | None = None


@dataclass
class DeployReport:
    project_root: Path
    zip_path: Path
    zip_root_name: str
    extracted_root: Path | None = None
    checked: list[str] = field(default_factory=list)
    candidates: list[CandidateApp] = field(default_factory=list)
    created_app_dirs: list[Path] = field(default_factory=list)
    created_files: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def get_settings_package_dir() -> Path:
    settings_module_name = os.environ["DJANGO_SETTINGS_MODULE"]
    settings_module = import_module(settings_module_name)
    return Path(settings_module.__file__).resolve().parent


def get_runtime_project_root() -> Path:
    """
    settings.BASE_DIR から上方向に探索し、
    .venv / pyproject.toml / .git のいずれかがあればそこを採用する。
    見つからなければ BASE_DIR の親を採用する。
    """
    base_dir = Path(settings.BASE_DIR).resolve()
    candidates = [base_dir] + list(base_dir.parents)

    for candidate in candidates:
        if (candidate / ".venv").exists():
            return candidate
        if (candidate / "pyproject.toml").exists():
            return candidate
        if (candidate / ".git").exists():
            return candidate

    if base_dir.parent != base_dir:
        return base_dir.parent
    return base_dir


def get_allowed_concrete_layer_names() -> set[str]:
    configured = getattr(settings, "STRUCTURE_CONCRETE_LAYER_NAMES", None)
    if configured:
        return {str(name).strip() for name in configured if str(name).strip()}
    return set()


def is_concrete_layer_dir_name(name: str) -> bool:
    configured = get_allowed_concrete_layer_names()
    if configured:
        return name in configured
    return len(name) == 4 and name.isalpha() and name.islower() and name != "abst"


def is_layer_dir_name(name: str) -> bool:
    return name == "abst" or is_concrete_layer_dir_name(name)


def read_manifest_project_name(extracted_root: Path) -> str | None:
    manifest_path = extracted_root / DEFAULT_MANIFEST_FILE_NAME
    if not manifest_path.exists():
        return None

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{DEFAULT_MANIFEST_FILE_NAME} のJSON解析に失敗しました: {exc}"
        ) from exc

    project_name = payload.get("project_name")
    if project_name is None:
        return None

    return str(project_name)


def validate_zip_file_extension(zip_path: Path) -> None:
    if zip_path.suffix.lower() != ".zip":
        raise ValueError("ZIP形式のみ対応しています。拡張子 .zip のみ許可されます。")


def should_ignore_name(name: str) -> bool:
    ignored_names = {
        "__MACOSX",
        ".DS_Store",
        "Thumbs.db",
    }
    return name in ignored_names or name.startswith(".")


def is_structure_root_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False

    has_manifest = (path / DEFAULT_MANIFEST_FILE_NAME).exists()
    has_abst = (path / "abst").is_dir()
    has_concrete_layer = any(
        child.is_dir() and is_concrete_layer_dir_name(child.name)
        for child in path.iterdir()
    )
    return has_manifest or has_abst or has_concrete_layer


def discover_structure_root_candidates_from_zip(zip_path: Path) -> list[tuple[str, int]]:
    """
    ZIP内部のパス一覧から構造ルート候補を推定する。
    スコアが高いほど有力候補。
    """
    score_map: dict[str, int] = {}

    with zipfile.ZipFile(zip_path, "r") as zip_file:
        for raw_name in zip_file.namelist():
            normalized = raw_name.strip("/").replace("\\", "/")
            if not normalized:
                continue

            parts = [part for part in normalized.split("/") if part]
            if not parts:
                continue
            if any(should_ignore_name(part) for part in parts):
                continue

            for index, part in enumerate(parts):
                if part == DEFAULT_MANIFEST_FILE_NAME and index >= 1:
                    root = "/".join(parts[:index])
                    score_map[root] = score_map.get(root, 0) + 100

                if part == "abst" and index >= 1:
                    root = "/".join(parts[:index])
                    score_map[root] = score_map.get(root, 0) + 50

                if is_concrete_layer_dir_name(part) and part != "abst" and index >= 1:
                    root = "/".join(parts[:index])
                    score_map[root] = score_map.get(root, 0) + 20

    ranked = sorted(score_map.items(), key=lambda item: (-item[1], item[0]))
    return ranked


def find_extracted_structure_root(temp_root: Path) -> Path:
    candidates: list[Path] = []

    all_dirs = [temp_root] + [path for path in sorted(temp_root.rglob("*")) if path.is_dir()]

    for path in all_dirs:
        if should_ignore_name(path.name):
            continue
        if is_structure_root_dir(path):
            candidates.append(path)

    top_level_candidates: list[Path] = []
    for candidate in candidates:
        if any(parent in candidates for parent in candidate.parents if parent != candidate):
            continue
        top_level_candidates.append(candidate)

    if not top_level_candidates:
        raise FileNotFoundError(
            "ZIP内に構造ルートが見つかりませんでした。 "
            f"{DEFAULT_MANIFEST_FILE_NAME} または abst / 具現レイヤーを含むディレクトリが必要です。"
        )

    if len(top_level_candidates) != 1:
        candidate_paths = [str(path.relative_to(temp_root)) for path in top_level_candidates]
        raise ValueError(
            "構造ルートを一意に特定できませんでした。 "
            f"候補: {candidate_paths}"
        )

    return top_level_candidates[0]


def extract_zip_to_temporary_dir(zip_path: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temp_dir = tempfile.TemporaryDirectory(prefix="deploystructure_")
    temp_root = Path(temp_dir.name)

    ranked_candidates = discover_structure_root_candidates_from_zip(zip_path)

    with zipfile.ZipFile(zip_path, "r") as zip_file:
        zip_file.extractall(temp_root)

    if ranked_candidates:
        best_root_text, _best_score = ranked_candidates[0]
        best_root = temp_root / Path(best_root_text)
        if best_root.exists() and best_root.is_dir() and is_structure_root_dir(best_root):
            return temp_dir, best_root

    extracted_root = find_extracted_structure_root(temp_root)
    if not extracted_root.exists():
        temp_dir.cleanup()
        raise FileNotFoundError("ZIP展開後に構造ルートが見つかりませんでした。")

    return temp_dir, extracted_root


def validate_project_identity(extracted_root: Path) -> tuple[Path, str]:
    project_root = get_runtime_project_root()
    runtime_project_name = project_root.name
    manifest_project_name = read_manifest_project_name(extracted_root)

    if manifest_project_name is not None and manifest_project_name != runtime_project_name:
        raise ValueError(
            "構造展開を中止しました。\n"
            "\n"
            f"理由:\n"
            f"- 実行中プロジェクト: {runtime_project_name}\n"
            f"- manifest project_name: {manifest_project_name}\n"
            "\n"
            "一致しないため処理できません。"
        )

    return project_root, runtime_project_name


def iter_layer_dirs(project_root: Path) -> Iterable[Path]:
    for path in project_root.rglob("*"):
        if path.is_dir() and is_layer_dir_name(path.name):
            yield path


def build_dotted_path_from_runtime_root(project_root: Path, target_dir: Path) -> str:
    relative_parts = target_dir.relative_to(project_root).parts
    return ".".join(relative_parts)


def should_skip_nested_dir(path: Path) -> str | None:
    if path.name.startswith("."):
        return "隠しディレクトリのためスキップ"
    if path.name == "__pycache__":
        return "__pycache__ のためスキップ"
    if path.name == "migrations":
        return "migrations ディレクトリのためスキップ"
    return None


def iter_nested_app_dirs(layer_dir: Path) -> Iterable[Path]:
    for path in sorted(layer_dir.rglob("*")):
        if not path.is_dir():
            continue
        if path == layer_dir:
            continue
        skip_reason = should_skip_nested_dir(path)
        if skip_reason:
            continue
        yield path


def find_candidate_apps(extracted_project_root: Path, runtime_project_root: Path) -> list[CandidateApp]:
    candidates: list[CandidateApp] = []
    seen_target_dirs: set[Path] = set()

    for layer_dir in iter_layer_dirs(extracted_project_root):
        for child_dir in iter_nested_app_dirs(layer_dir):
            relative_child_path = child_dir.relative_to(extracted_project_root)
            target_app_dir = runtime_project_root / relative_child_path

            if target_app_dir in seen_target_dirs:
                continue
            seen_target_dirs.add(target_app_dir)

            app_name = child_dir.name
            dotted_app_path = build_dotted_path_from_runtime_root(runtime_project_root, target_app_dir)

            candidate = CandidateApp(
                source_layer_dir=layer_dir,
                source_app_dir=child_dir,
                target_app_dir=target_app_dir,
                app_name=app_name,
                dotted_app_path=dotted_app_path,
            )

            invalid_reason = should_skip_nested_dir(child_dir)
            if invalid_reason:
                candidate.skip_reason = invalid_reason
            elif not app_name.isidentifier():
                candidate.skip_reason = "ディレクトリ名がPython識別子ではないためスキップ"
            elif (target_app_dir / "apps.py").exists():
                candidate.skip_reason = "既に apps.py が存在するためスキップ"

            candidates.append(candidate)

    return candidates


def ensure_directory(path: Path) -> bool:
    if path.exists():
        return False
    path.mkdir(parents=True, exist_ok=True)
    return True


def ensure_package_chain(project_root: Path, target_dir: Path) -> list[Path]:
    created_init_files: list[Path] = []
    current = target_dir

    while True:
        init_file = current / "__init__.py"
        if not init_file.exists():
            init_file.touch()
            created_init_files.append(init_file)

        if current == project_root:
            break

        if current.parent == current:
            break

        current = current.parent

    return created_init_files


def generate_startapp_to_temp(app_name: str) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix=f"startapp_{app_name}_"))
    call_command("startapp", app_name, str(temp_dir))
    return temp_dir


def merge_missing_files_only(source_dir: Path, target_dir: Path) -> tuple[list[Path], list[Path]]:
    created_dirs: list[Path] = []
    created_files: list[Path] = []

    for source_path in sorted(source_dir.rglob("*")):
        relative_path = source_path.relative_to(source_dir)
        destination_path = target_dir / relative_path

        if source_path.is_dir():
            if ensure_directory(destination_path):
                created_dirs.append(destination_path)
            continue

        ensure_directory(destination_path.parent)

        if destination_path.exists():
            continue

        shutil.copy2(source_path, destination_path)
        created_files.append(destination_path)

    return created_dirs, created_files


def rewrite_apps_py_name_if_created(target_app_dir: Path, dotted_app_path: str) -> bool:
    apps_py_path = target_app_dir / "apps.py"
    if not apps_py_path.exists():
        return False

    content = apps_py_path.read_text(encoding="utf-8")
    replaced = False

    lines = content.splitlines()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("name = "):
            indent = line[: len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}name = "{dotted_app_path}"')
            replaced = True
        else:
            new_lines.append(line)

    if replaced:
        apps_py_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return replaced


def can_probably_import_as_django_app(project_root: Path, app_dir: Path) -> bool:
    current = app_dir

    while True:
        init_file = current / "__init__.py"
        if not init_file.exists():
            return False

        if current == project_root:
            return True

        if current.parent == current:
            return False

        current = current.parent


def deploy_candidates(
    project_root: Path,
    candidates: list[CandidateApp],
    dry_run: bool = False,
) -> tuple[list[Path], list[Path], list[tuple[Path, str]], list[str]]:
    created_dirs: list[Path] = []
    created_files: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    warnings: list[str] = []

    for candidate in candidates:
        if candidate.skip_reason:
            skipped.append((candidate.target_app_dir, candidate.skip_reason))
            continue

        if dry_run:
            continue

        ensure_directory(candidate.target_app_dir)
        package_init_files = ensure_package_chain(project_root, candidate.target_app_dir)
        created_files.extend(package_init_files)

        temp_generated_dir = generate_startapp_to_temp(candidate.app_name)
        try:
            merged_dirs, merged_files = merge_missing_files_only(
                source_dir=temp_generated_dir,
                target_dir=candidate.target_app_dir,
            )
            created_dirs.extend(merged_dirs)
            created_files.extend(merged_files)

            apps_py_path = candidate.target_app_dir / "apps.py"
            if apps_py_path.exists():
                rewrite_apps_py_name_if_created(
                    target_app_dir=candidate.target_app_dir,
                    dotted_app_path=candidate.dotted_app_path,
                )

            if not can_probably_import_as_django_app(project_root, candidate.target_app_dir):
                warnings.append(
                    "Djangoアプリとしてのimport解決に注意が必要です: "
                    f"{candidate.target_app_dir}"
                )
        finally:
            shutil.rmtree(temp_generated_dir, ignore_errors=True)

    return created_dirs, created_files, skipped, warnings


def collect_installed_app_candidates(runtime_project_root: Path) -> list[str]:
    dotted_paths: list[str] = []

    for apps_py_path in sorted(runtime_project_root.rglob("apps.py")):
        app_dir = apps_py_path.parent
        if app_dir.name == "migrations":
            continue

        relative_dir = app_dir.relative_to(runtime_project_root)
        if not relative_dir.parts:
            continue

        if not all(part.isidentifier() for part in relative_dir.parts):
            continue

        dotted_paths.append(".".join(relative_dir.parts))

    seen: set[str] = set()
    unique_paths: list[str] = []

    for dotted in dotted_paths:
        if dotted in seen:
            continue
        seen.add(dotted)
        unique_paths.append(dotted)

    return unique_paths


def write_installed_apps_autogen(dotted_paths: list[str]) -> Path:
    settings_package_dir = get_settings_package_dir()
    output_path = settings_package_dir / DEFAULT_AUTOGEN_FILE_NAME

    lines = [
        "# This file is auto-generated by syncinstalledapps.",
        "INSTALLED_APPS_AUTOGEN = [",
    ]
    for dotted_path in dotted_paths:
        lines.append(f'    "{dotted_path}",')
    lines.append("]")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path