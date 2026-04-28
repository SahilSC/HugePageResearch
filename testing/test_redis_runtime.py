# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from redis_runtime import (
    RedisEndpoint,
    load_repo_redis_cli_path,
    load_repo_redis_endpoint,
    load_repo_redis_server_path,
)


class RedisRuntimeConfigTest(unittest.TestCase):
    def _write_config(self, text: str) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        config_path = Path(tmpdir.name) / "redis.conf"
        config_path.write_text(text, encoding="utf-8")
        return config_path

    def _write_binary(self, directory: Path, name: str) -> Path:
        binary_path = directory / name
        binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
        binary_path.chmod(0o755)
        return binary_path

    def test_load_repo_redis_endpoint_reads_current_repo_shape(self):
        config_path = self._write_config(
            "bind 127.0.0.1 -::1\n"
            "port 6379\n",
        )

        self.assertEqual(
            load_repo_redis_endpoint(config_path),
            RedisEndpoint(host="127.0.0.1", port=6379),
        )

    def test_load_repo_redis_endpoint_ignores_comments_and_whitespace(self):
        config_path = self._write_config(
            "\n"
            "   # comment only line\n"
            "bind   -::1   10.0.0.5   # prefer first usable bind token\n"
            "  port   6380   \n",
        )

        self.assertEqual(
            load_repo_redis_endpoint(config_path),
            RedisEndpoint(host="::1", port=6380),
        )

    def test_load_repo_redis_endpoint_requires_bind(self):
        config_path = self._write_config("port 6379\n")

        with self.assertRaisesRegex(RuntimeError, "bind"):
            load_repo_redis_endpoint(config_path)

    def test_load_repo_redis_endpoint_requires_port(self):
        config_path = self._write_config("bind 127.0.0.1 -::1\n")

        with self.assertRaisesRegex(RuntimeError, "port"):
            load_repo_redis_endpoint(config_path)

    def test_load_repo_redis_endpoint_requires_integer_port(self):
        config_path = self._write_config(
            "bind 127.0.0.1 -::1\n"
            "port not-a-number\n",
        )

        with self.assertRaisesRegex(RuntimeError, "not an integer"):
            load_repo_redis_endpoint(config_path)

    def test_load_repo_redis_server_path_uses_expected_binary_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            build_dir = Path(tmp_dir)
            server_path = self._write_binary(build_dir, "redis-server")

            self.assertEqual(
                load_repo_redis_server_path(build_dir=build_dir),
                server_path,
            )

    def test_load_repo_redis_server_path_raises_when_binary_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            build_dir = Path(tmp_dir)

            with self.assertRaisesRegex(RuntimeError, "redis-server"):
                load_repo_redis_server_path(build_dir=build_dir)

    def test_load_repo_redis_cli_path_uses_expected_binary_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            build_dir = Path(tmp_dir)
            cli_path = self._write_binary(build_dir, "redis-cli")

            self.assertEqual(
                load_repo_redis_cli_path(build_dir=build_dir),
                cli_path,
            )

    def test_load_repo_redis_cli_path_raises_when_binary_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            build_dir = Path(tmp_dir)

            with self.assertRaisesRegex(RuntimeError, "redis-cli"):
                load_repo_redis_cli_path(build_dir=build_dir)


if __name__ == "__main__":
    unittest.main()
