import os
import re
import enum
import sys
import asyncio
import posixpath
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from django.conf import settings
from django.contrib.staticfiles.finders import get_finders
from jinja2 import Template


class CompileMode(enum.Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    NONE = "none"


WEBPACK_CONFIG_TEMPLATE = """// generated webpack configuration
const path = require("path");
const CopyPlugin = require("copy-webpack-plugin");
const CssMinimizerPlugin = require("css-minimizer-webpack-plugin");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");
const TerserPlugin = require("terser-webpack-plugin");
const ImageMinimizerPlugin = require("image-minimizer-webpack-plugin");
const { extendDefaultPlugins } = require("svgo");
const { WebpackManifestPlugin } = require("webpack-manifest-plugin");

module.exports = {
  mode: "{{ mode }}",
  entry: "./dummy.js",
  module: {
    rules: [
      { test: /\.js$/, use: "babel-loader" },
      { test: /.s?css$/, use: [MiniCssExtractPlugin.loader, "css-loader", "sass-loader"] },
      { test: /\.(jpe?g|png|gif|svg)$/i, type: "asset" },
    ],
  },
  output: {
    publicPath: "{{ public_path }}",
    filename: "[name].js",
    clean: true,
  },
  plugins: [
    new CopyPlugin({
      patterns: [
{%- for cf in copy_files %}
        { from: "{{ cf.source }}", to: "{{ cf.dest }}" },
{%- endfor %}
      ],
    }),
    new ImageMinimizerPlugin({
      minimizerOptions: {
        plugins: [
          ["gifsicle", { interlaced: true }],
          ["jpegtran", { progressive: true }],
          ["optipng", { optimizationLevel: 5 }],
        ],
      },
    }),
    new WebpackManifestPlugin({}),
  ],
  optimization: {
    moduleIds: 'deterministic',
    runtimeChunk: 'single',
    minimizer: [
      new TerserPlugin(),
      new CssMinimizerPlugin({
        test: /\.css$/i,
        minimizerOptions: {
          preset: [
            "default",
            {
              discardComments: { removeAll: true },
            },
          ],
        },
      }),
    ],
  },
};
"""

BASE_DIR = Path(__file__).parent.parent.resolve()

staticfiles_matcher = re.compile(r"^%s(?P<path>.*)$" % settings.STATIC_URL)
staticfiles_prefix = posixpath.normpath(Path(settings.STATIC_URL).resolve()).lstrip("/")


async def _read_stream(stream, cb):
    while True:
        line = await stream.readline()
        if line:
            cb(line)
        else:
            break


async def _stream_subprocess(cmd, stdout_cb, stderr_cb):
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        await asyncio.wait(
            [
                _read_stream(process.stdout, stdout_cb),
                _read_stream(process.stderr, stderr_cb),
            ]
        )
        rc = await process.wait()
        return process.pid, rc
    except OSError as e:
        # the program will hang if we let any exception propagate
        return e


def execute(*aws):
    """run the given coroutines in an asyncio loop
    returns a list containing the values returned from each coroutine.
    """
    loop = asyncio.get_event_loop()
    rc = loop.run_until_complete(asyncio.gather(*aws))
    loop.close()
    return rc


def printer(label):
    def pr(*args, **kw):
        print(label, *args, **kw)

    return pr


def name_it(start=0, template="s{}"):
    """a simple generator for task names"""
    while True:
        yield template.format(start)
        start += 1


class Webpack:
    def __init__(
        self,
        yarn_bin: str,
        work_dir: Path,
        document_root: Path = None,
        mode: CompileMode = CompileMode.PRODUCTION,
        config: str = WEBPACK_CONFIG_TEMPLATE,
        extra_context: dict = {},
    ):
        self.mode = mode
        self.config = config
        self.extra_context = extra_context
        self.work_dir = work_dir
        self.yarn_bin = yarn_bin
        self.document_root = document_root if document_root else work_dir / "dist"
        app_package_json = Path(settings.BASE_DIR) / "package.json"
        self.package_json_path = (
            app_package_json if app_package_json.exists() else BASE_DIR / "package.json"
        )
        app_yarn_lock = Path(settings.BASE_DIR) / "yarn.lock"
        self.yarn_lock_path = (
            app_yarn_lock if app_yarn_lock.exists() else BASE_DIR / "yarn.lock"
        )
        app_node_modules = Path(settings.BASE_DIR) / "node_modules"
        self.node_modules_path = app_node_modules if app_node_modules.exists() else None
        self.webpack_config_file = self.work_dir / "webpack.config.js"

    def get_staticfiles(self, ignore_patterns: List = []) -> Dict[str, Tuple[str, str]]:
        found_files = {}
        for finder in get_finders():
            for path, storage in finder.list(ignore_patterns):
                if getattr(storage, "prefix", None):
                    prefixed_path = os.path.join(storage.prefix, path)
                else:
                    prefixed_path = path

                if prefixed_path not in found_files:
                    found_files[prefixed_path] = (storage.location, path)

        return found_files

    def prepare_webpack_root(self):
        static_files = self.get_staticfiles()

        copy_files = []
        for k, v in static_files.items():
            copy_files.append(
                dict(
                    source=Path(v[0]) / v[1],
                    dest=Path(staticfiles_prefix) / k,
                )
            )

        webpack_config = Template(self.config)
        context = dict(
            mode=self.mode.value,
            public_path=f".{os.path.sep}",
            copy_files=copy_files,
        )

        context.update(self.extra_context)
        with open(self.webpack_config_file, "w") as cf:
            cf.write(webpack_config.render(**context))

        # TODO entry should be configurable
        with open(self.work_dir / "dummy.js", "w") as df:
            df.write("// empty file, since webpack need's an entrypoint")

        os.symlink(self.package_json_path, self.work_dir / "package.json")
        os.symlink(self.yarn_lock_path, self.work_dir / "yarn.lock")
        if self.node_modules_path:
            os.symlink(self.node_modules_path, self.work_dir / "node_modules")

        subprocess.run([self.yarn_bin], cwd=self.work_dir)

    def run_webpack_build(self, watch: bool = False):
        cmd = [
            self.yarn_bin,
            "webpack",
            "--config",
            self.webpack_config_file,
            "--output-path",
            self.document_root,
        ]
        if watch:
            cmd.append("--watch")
        subprocess.run(cmd, cwd=self.work_dir)
