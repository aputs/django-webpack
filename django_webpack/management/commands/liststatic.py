import json
import os
import sys
from enum import Enum
from pathlib import Path

from django.contrib.staticfiles.finders import get_finders
from django.core.management.base import BaseCommand, CommandError
from django_webpack.static import get_staticfiles


class OutputFormat(Enum):
    JSON = "json"
    RAW = "raw"


class Command(BaseCommand):
    help = "List all static files"

    def add_arguments(self, parser):
        parser.add_argument("--format", type=OutputFormat, default=OutputFormat.JSON)

    def handle(self, *args, **options):
        found_files = get_staticfiles()
        format = options.get("format")
        if format == OutputFormat.JSON:
            json.dump(found_files, sys.stdout, indent=2)
        else:
            for k, (p, f) in found_files.items():
                sys.stdout.write(str(Path(p) / f))
                sys.stdout.write("\n")
