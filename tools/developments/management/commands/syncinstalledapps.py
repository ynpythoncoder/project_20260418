# tools/developments/management/commands/syncinstalledapps.py

from __future__ import annotations

from django.core.management.base import BaseCommand

from ..structure_tools import (
    collect_installed_app_candidates,
    get_runtime_project_root,
    write_installed_apps_autogen,
)


class Command(BaseCommand):
    help = "生成済みアプリ一覧を installed_apps_autogen.py に同期します。"

    def handle(self, *args, **options):
        project_root = get_runtime_project_root()
        dotted_paths = collect_installed_app_candidates(project_root)
        output_path = write_installed_apps_autogen(dotted_paths)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== syncinstalledapps report ==="))
        self.stdout.write(f"- プロジェクトルート: {project_root}")
        self.stdout.write(f"- 出力先: {output_path}")

        if not dotted_paths:
            self.stdout.write("- 検出アプリ: なし")
            return

        self.stdout.write("- 検出アプリ:")
        for dotted_path in dotted_paths:
            self.stdout.write(f"  - {dotted_path}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("installed_apps_autogen.py を更新しました。"))