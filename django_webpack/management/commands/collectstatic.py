import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from django_webpack.webpack import Webpack, CompileMode


class Command(BaseCommand):
    help = "Collect all static files and run webpack"

    def add_arguments(self, parser):
        parser.add_argument(
            "--public-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "public",
            help="Set PUBLIC_ROOT directory",
        )
        parser.add_argument(
            "--mode",
            type=CompileMode,
            choices=(
                CompileMode.DEVELOPMENT.value,
                CompileMode.PRODUCTION.value,
                CompileMode.NONE.value,
            ),
            default=CompileMode.DEVELOPMENT,
        )
        parser.add_argument(
            "--yarn-bin",
            type=Path,
            default=shutil.which("yarn"),
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            default=False,
        )

    def handle(self, *args, **options):
        yarn_bin = options["yarn_bin"]
        if not yarn_bin:
            raise CommandError("yarn binary not found.")

        with tempfile.TemporaryDirectory() as work_dir:
            public_root = os.environ.get("PUBLIC_ROOT", None)
            if not public_root:
                public_root = options["public_root"]

            wp = Webpack(
                yarn_bin,
                Path(work_dir),
                document_root=Path(public_root),
                mode=options["mode"],
            )
            wp.prepare_webpack_root()
            wp.run_webpack_build(watch=options["watch"])
