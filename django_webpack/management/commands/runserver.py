import re
import os
import shutil
import socket
import tempfile
import uvicorn
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.regex_helper import _lazy_re_compile

from django_webpack.webpack import Webpack, CompileMode

naiveip_re = _lazy_re_compile(
    r"""^(?:
(?P<addr>
    (?P<ipv4>\d{1,3}(?:\.\d{1,3}){3}) |         # IPv4 address
    (?P<ipv6>\[[a-fA-F0-9:]+\]) |               # IPv6 address
    (?P<fqdn>[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*) # FQDN
):)?(?P<port>\d+)$""",
    re.X,
)


class Command(BaseCommand):
    help = "Starts a lightweight web server for development."
    default_addr = "127.0.0.1"
    default_addr_ipv6 = "::1"
    default_port = "8000"

    def add_arguments(self, parser):
        parser.add_argument(
            "addrport", nargs="?", help="Optional port number, or ipaddr:port"
        )
        parser.add_argument(
            "--public-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "public",
            help="Set PUBLIC_ROOT directory",
        )
        parser.add_argument(
            "--ipv6",
            "-6",
            action="store_true",
            dest="use_ipv6",
            help="Tells Django to use an IPv6 address.",
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

    def handle(self, *args, **options):
        reload_dirs = [Path(settings.BASE_DIR).resolve()]
        for app_config in apps.get_app_configs():
            m = import_module(app_config.name)
            path = m.__path__[0]
            if "site-packages" in path:
                continue
            reload_dirs.append(path)

        self.use_ipv6 = options["use_ipv6"]
        if self.use_ipv6 and not socket.has_ipv6:
            raise CommandError("Your Python does not support IPv6.")
        self._raw_ipv6 = False
        if not options["addrport"]:
            self.addr = ""
            self.port = self.default_port
        else:
            m = re.match(naiveip_re, options["addrport"])
            if m is None:
                raise CommandError(
                    '"%s" is not a valid port number '
                    "or address:port pair." % options["addrport"]
                )
            self.addr, _ipv4, _ipv6, _fqdn, self.port = m.groups()
            if not self.port.isdigit():
                raise CommandError("%r is not a valid port number." % self.port)
            if self.addr:
                if _ipv6:
                    self.addr = self.addr[1:-1]
                    self.use_ipv6 = True
                    self._raw_ipv6 = True
                elif self.use_ipv6 and not _fqdn:
                    raise CommandError('"%s" is not a valid IPv6 address.' % self.addr)
        if not self.addr:
            self.addr = self.default_addr_ipv6 if self.use_ipv6 else self.default_addr
            self._raw_ipv6 = self.use_ipv6

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

            def run_webpack():
                # TODO detect updated staticfiles list
                wp.prepare_webpack_root()
                wp.run_webpack_build(watch=True)

            loop = asyncio.get_event_loop()
            executor = ThreadPoolExecutor(1)
            loop.run_in_executor(executor, run_webpack)
            os.environ.setdefault("PUBLIC_ROOT", public_root)
            uvicorn.run(
                "django_webpack.asgi:app",
                host=self.addr,
                port=int(self.port),
                reload=True,
                reload_dirs=reload_dirs,
            )
            loop.close()
