"""Helpers for repo-managed Redis runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DEFAULT_REDIS_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "redis.conf"
)
_DEFAULT_REDIS_BUILD_DIR = Path("/tmp/redis-7.4.2/src")


@dataclass(frozen=True)
class RedisEndpoint:
    """Redis client endpoint loaded from the repo's Redis config.

    Attributes:
        host: Host or bind address clients should use.
        port: TCP port clients should use.
    """

    host: str
    port: int


def load_repo_redis_server_path(
    *,
    tcmalloc: bool = False,
    build_dir: Path = _DEFAULT_REDIS_BUILD_DIR,
) -> Path:
    """Return the checked-in Redis server binary used by this repo.

    The Redis benchmark and replay flows require the host Redis build that can
    load ``redis-module/vaptr.so``. On this machine that is the Redis `7.4.2`
    build under ``/tmp/redis-7.4.2/src``.

    Example output:
        {"path": "/tmp/redis-7.4.2/src/redis-server"}

    Args:
        tcmalloc: Whether to resolve the tcmalloc-flavoured server binary.
        build_dir: Directory that contains the validated Redis build outputs.

    Returns:
        Absolute path to the Redis server binary.

    Raises:
        RuntimeError: If the expected Redis server binary is missing.
    """

    binary_name = "redis-server-tcmalloc" if tcmalloc else "redis-server"
    binary_path = build_dir / binary_name
    if not binary_path.is_file():
        raise RuntimeError(
            f"Expected repo Redis server binary at {binary_path}, but it is missing"
        )
    return binary_path


def load_repo_redis_cli_path(
    build_dir: Path = _DEFAULT_REDIS_BUILD_DIR,
) -> Path:
    """Return the checked-in Redis CLI binary used by this repo.

    Example output:
        {"path": "/tmp/redis-7.4.2/src/redis-cli"}

    Args:
        build_dir: Directory that contains the validated Redis build outputs.

    Returns:
        Absolute path to the Redis CLI binary.

    Raises:
        RuntimeError: If the expected Redis CLI binary is missing.
    """

    binary_path = build_dir / "redis-cli"
    if not binary_path.is_file():
        raise RuntimeError(
            f"Expected repo Redis CLI binary at {binary_path}, but it is missing"
        )
    return binary_path


def load_repo_redis_endpoint(
    config_path: Path = _DEFAULT_REDIS_CONFIG_PATH,
) -> RedisEndpoint:
    """Read the benchmark Redis host and port from ``config/redis.conf``.

    The repo starts Redis from the checked-in config file, so benchmark and
    replay helpers should target the same bind/port instead of keeping a second
    hardcoded endpoint in Python.

    Example output:
        {"host": "127.0.0.1", "port": 6379}

    Args:
        config_path: Path to the repo-managed Redis config file.

    Returns:
        The parsed host and port from the Redis config file.

    Raises:
        RuntimeError: If the config file cannot be read or does not define a
            usable ``bind`` and ``port`` pair.
    """

    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(
            f"Failed to read Redis config at {config_path}: {exc}"
        ) from exc

    bind_host: str | None = None
    port: int | None = None

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        tokens = line.split()
        directive = tokens[0]

        if directive == "bind":
            usable_hosts = [
                token.lstrip("-") for token in tokens[1:] if token.lstrip("-")
            ]
            if not usable_hosts:
                raise RuntimeError(
                    f"Redis config bind directive in {config_path} does not contain a usable host"
                )
            bind_host = usable_hosts[0]
            continue

        if directive == "port":
            if len(tokens) < 2:
                raise RuntimeError(
                    f"Redis config port directive in {config_path} is missing a value"
                )
            try:
                port = int(tokens[1])
            except ValueError as exc:
                raise RuntimeError(
                    f"Redis config port directive in {config_path} is not an integer: {tokens[1]!r}"
                ) from exc

    if bind_host is None:
        raise RuntimeError(
            f"Redis config at {config_path} is missing a usable bind directive"
        )
    if port is None:
        raise RuntimeError(
            f"Redis config at {config_path} is missing a port directive"
        )

    return RedisEndpoint(host=bind_host, port=port)
