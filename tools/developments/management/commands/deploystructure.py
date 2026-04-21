# tools/developments/management/commands/deploystructure.py

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ..structure_tools import (
    DeployReport,
    deploy_candidates,
    extract_zip_to_temporary_dir,
    find_candidate_apps,
    read_manifest_project_name,
    read_zip_root_dir_name,
    validate_project_identity,
    validate_zip_file_extension,
)


class Command(BaseCommand):
    help = "ZIP構造を検査し、安全条件を満たす場合のみ startapp 相当の生成を行います。"

    def add_arguments(self, parser):
        parser.add_argument("zip_path", type=str, help="入力ZIPファイルパス")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="実際には生成せず、対象とスキップ理由のみ表示します",
        )

    def handle(self, *args, **options):
        zip_path = Path(options["zip_path"]).expanduser().resolve()
        dry_run = options["dry_run"]

        if not zip_path.exists():
            raise CommandError(f"ZIPファイルが見つかりません: {zip_path}")

        try:
            validate_zip_file_extension(zip_path)
            zip_root_name = read_zip_root_dir_name(zip_path)
            project_root, runtime_project_name = validate_project_identity(zip_path, zip_root_name)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        report = DeployReport(
            project_root=project_root,
            zip_path=zip_path,
            zip_root_name=zip_root_name,
        )
        report.checked.extend(
            [
                f"実行中プロジェクト: {runtime_project_name}",
                f"ZIPファイル名: {zip_path.stem}",
                f"ZIP内ルート: {zip_root_name}",
            ]
        )

        temp_dir = None
        try:
            temp_dir, extracted_root = extract_zip_to_temporary_dir(zip_path)
            report.extracted_root = extracted_root

            manifest_project_name = read_manifest_project_name(extracted_root)
            if manifest_project_name is not None and manifest_project_name != runtime_project_name:
                raise CommandError(
                    "structure_manifest.json の project_name が一致しません。\n"
                    f"- 実行中プロジェクト: {runtime_project_name}\n"
                    f"- manifest project_name: {manifest_project_name}"
                )

            candidates = find_candidate_apps(
                extracted_project_root=extracted_root,
                runtime_project_root=project_root,
            )
            report.candidates.extend(candidates)

            created_dirs, created_files, skipped, warnings = deploy_candidates(
                project_root=project_root,
                candidates=candidates,
                dry_run=dry_run,
            )
            report.created_app_dirs.extend(created_dirs)
            report.created_files.extend(created_files)
            report.skipped.extend(skipped)
            report.warnings.extend(warnings)
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

        self._print_report(report, dry_run=dry_run)

    def _print_report(self, report: DeployReport, dry_run: bool) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== deploystructure report ==="))

        self.stdout.write("")
        self.stdout.write("【安全チェック】")
        for line in report.checked:
            self.stdout.write(f"- {line}")

        if report.extracted_root is not None:
            self.stdout.write(f"- 展開確認ルート: {report.extracted_root}")

        self.stdout.write("")
        self.stdout.write("【生成候補】")
        if not report.candidates:
            self.stdout.write("- 候補なし")
        else:
            for candidate in report.candidates:
                suffix = f" [SKIP: {candidate.skip_reason}]" if candidate.skip_reason else ""
                self.stdout.write(f"- {candidate.target_app_dir} ({candidate.dotted_app_path}){suffix}")

        self.stdout.write("")
        self.stdout.write("【スキップ一覧】")
        if not report.skipped:
            self.stdout.write("- なし")
        else:
            for path, reason in report.skipped:
                self.stdout.write(f"- {path} :: {reason}")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("dry-run のため、実際の生成は行っていません。"))
            return

        self.stdout.write("")
        self.stdout.write("【新規作成ディレクトリ】")
        if not report.created_app_dirs:
            self.stdout.write("- なし")
        else:
            for path in report.created_app_dirs:
                self.stdout.write(f"- {path}")

        self.stdout.write("")
        self.stdout.write("【新規作成ファイル】")
        if not report.created_files:
            self.stdout.write("- なし")
        else:
            for path in report.created_files:
                self.stdout.write(f"- {path}")

        self.stdout.write("")
        self.stdout.write("【警告】")
        if not report.warnings:
            self.stdout.write("- なし")
        else:
            for warning in report.warnings:
                self.stdout.write(f"- {warning}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("deploystructure が完了しました。"))