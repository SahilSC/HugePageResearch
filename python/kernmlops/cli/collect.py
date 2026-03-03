import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread
from time import sleep
from typing import cast

import data_collection
import data_schema
import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_schema import get_user_group_ids
from kernmlops_benchmark import (
    Benchmark,
    BenchmarkNotConfiguredError,
    BenchmarkNotRunningError,
)
from kernmlops_config import ConfigBase
from pytimeparse.timeparse import timeparse


def wait_for_END(run_event: Event, read):
    while run_event.is_set() and "END" not in read.readline():
        continue
    run_event.clear()


def poll_instrumentation(
    benchmark: Benchmark,
    bpf_programs: list[data_collection.bpf.BPFProgram],
    queue: Queue,
    run_event: Event,
    poll_rate: float = 0.5,
) -> int:
    return_code = None
    while return_code is None and run_event.is_set():
        try:
            for bpf_program in bpf_programs:
                bpf_program.poll()
            if poll_rate > 0:
                sleep(poll_rate)
            return_code = benchmark.poll()
            # clean data when missed samples - or detect?
        except BenchmarkNotRunningError:
            continue

    if not run_event.is_set():
        benchmark.kill()
        return_code = 0 if benchmark.name() == "faux" else 1

    # Poll again to clean out all buffers
    for bpf_program in bpf_programs:
        try:
            bpf_program.poll()
        except Exception:
            pass
    return_code = return_code if return_code is not None else 1
    queue.put(return_code)
    return return_code


def signal_handler_factory(event: Event):
    return lambda x, y: event.clear()


def _safe_chown(path: Path, ids: tuple[int, int] | None) -> None:
    if ids is None:
        return
    try:
        os.chown(path, ids[0], ids[1])
    except PermissionError:
        # Non-root users may not be able to chown even to the same owner.
        pass


def _discover_redis_server_tgids(process_trace_df: pl.DataFrame) -> set[int]:
    if "name" not in process_trace_df.columns or "tgid" not in process_trace_df.columns:
        return set()
    rows = process_trace_df.filter(pl.col("name").str.starts_with("redis-server"))
    if rows.is_empty():
        return set()
    return set(rows.select("tgid").unique().to_series().to_list())


def _clean_redis_collection(
    *,
    curated_dir: Path,
    collection_id: str,
    verbose: bool,
    ids: tuple[int, int] | None,
) -> str | None:
    benchmark_name = "redis"
    raw_run_dir = curated_dir / benchmark_name / collection_id
    if not raw_run_dir.is_dir():
        print(
            f"warning: redis cleaner skipped, run directory not found: {raw_run_dir}",
            file=sys.stderr,
        )
        return None

    process_trace_files = sorted(raw_run_dir.glob("process_trace.*.parquet"))
    if not process_trace_files:
        raise RuntimeError(
            "redis cleaner failed fast: missing process_trace parquet "
            "(add process_trace hook or run with --no-clean)"
        )

    process_trace_df = pl.concat(
        [pl.read_parquet(file_path) for file_path in process_trace_files],
        how="diagonal_relaxed",
    )
    redis_tgids = _discover_redis_server_tgids(process_trace_df)
    if len(redis_tgids) != 1:
        raise RuntimeError("more than 1 redis_tgids\n" + redis_tgids)
    
    if not redis_tgids:
        raise RuntimeError(
            "redis cleaner failed fast: could not find redis-server TGID in process_trace "
            "(run with --no-clean to skip cleaning)"
        )

    cleaned_collection_id = f"cleaned{collection_id}"
    cleaned_run_dir = curated_dir / benchmark_name / cleaned_collection_id
    cleaned_run_dir.mkdir(parents=True, exist_ok=False)
    _safe_chown(cleaned_run_dir, ids)

    if verbose:
        sorted_tgids = ", ".join(str(tgid) for tgid in sorted(redis_tgids))
        print(f"Redis cleaner target TGID(s): {sorted_tgids}")

    for parquet_file in sorted(raw_run_dir.glob("*.parquet")):
        table_df = pl.read_parquet(parquet_file)
        before_rows = len(table_df)

        if "tgid" in table_df.columns:
            table_df = table_df.filter(pl.col("tgid").is_in(sorted(redis_tgids)))
        else:
            print(f"redis cleaner info: {parquet_file.name} has no tgid; copied without filtering")

        if "collection_id" in table_df.columns:
            table_df = table_df.with_columns(pl.lit(cleaned_collection_id).alias("collection_id"))

        out_file = cleaned_run_dir / parquet_file.name
        table_df.write_parquet(out_file)
        _safe_chown(out_file, ids)

        if verbose and ("pid" in table_df.columns or "tgid" in table_df.columns):
            after_rows = len(table_df)
            removed = before_rows - after_rows
            pct = (removed / before_rows * 100.0) if before_rows else 0.0
            print(
                f"redis cleaner {parquet_file.name}: {before_rows} -> {after_rows} rows "
                f"({pct:.1f}% removed)"
            )

    return cleaned_collection_id


def output_collections_to_file(
    collection_id: str,
    collection_tables: list[data_schema.CollectionTable],
    bpf_programs: list[BPFProgram],
    name: str,
    benchmark_name: str,
    verbose: bool,
    output_dir: Path,
    ids: tuple[int, int] | None = None,
):
    for bpf_program in bpf_programs:
        collection_tables.extend(bpf_program.pop_data())
    for collection_table in collection_tables:
        with pl.Config(tbl_cols=-1):
            if verbose:
                print(f"{collection_table.name()}: {collection_table.table}")
        full_path = Path(
            output_dir
            / benchmark_name
            / collection_id
            / f"{collection_table.name()}.{name}.parquet"
        )
        collection_table.table.write_parquet(full_path)
        if ids is not None:
            os.chown(full_path, ids[0], ids[1])
    return collection_tables


def output_data_thread(
    collection_id: str,
    bpf_programs: list[BPFProgram],
    benchmark_name: str,
    run_event: Event,
    verbose: bool,
    output_dir: Path,
    lock: Lock,
    ended: bool,
    output_interval: int | float,
    user_id: int,
    group_id: int,
):
    num: int = 0
    sleep(output_interval)
    while run_event.is_set():
        lock.acquire()
        try:
            if ended:
                lock.release()
                return
            output_collections_to_file(
                collection_id,
                [],
                bpf_programs,
                str(num),
                benchmark_name,
                verbose,
                output_dir,
                (user_id, group_id),
            )
        except Exception as e:
            print(e)
        lock.release()
        num += 1
        sleep(output_interval)


def run_collect(
    *,
    config: ConfigBase,
    benchmark: Benchmark,
    verbose: bool,
    collection_prefix: str | None,
    clean: bool,
):
    if not benchmark.is_configured():
        raise BenchmarkNotConfiguredError(
            f"benchmark {benchmark.name()} is not configured"
        )
    benchmark.setup()

    generic_config = cast(
        data_collection.GenericCollectorConfig,
        getattr(getattr(config, "collector_config"), "generic"),
    )
    bpf_programs = generic_config.get_hooks(
        hugepage_harness=getattr(config, "hugepage_harness", None),
        benchmark=benchmark,
    )
    system_info = data_collection.machine_info().to_polars()
    system_info = system_info.unnest(system_info.columns)
    collection_id = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    if collection_prefix:
        collection_id = f"{collection_prefix}-{collection_id}"
    output_dir = (
        generic_config.get_output_dir() / "curated"
        if bpf_programs
        else generic_config.get_output_dir() / "baseline"
    )
    queue = Queue(maxsize=1)
    run_event = Event()
    run_event.set()

    for bpf_program in bpf_programs:
        bpf_program.load(collection_id)
        if verbose:
            print(f"{bpf_program.name()} BPF program loaded")
    if verbose:
        print("Finished loading BPF programs")

    # Configure signal capture
    signal.signal(signal.SIGINT, signal_handler_factory(run_event))
    signal.signal(signal.SIGALRM, signal_handler_factory(run_event))
    signal.signal(signal.SIGUSR1, signal_handler_factory(run_event))

    # Create stdin killer daemon
    read_thread = Thread(target=wait_for_END, args=(run_event, sys.stdin))
    read_thread.daemon = True
    read_thread.start()

    # Create polling thread
    poll_thread = Thread(
        target=poll_instrumentation,
        args=(benchmark, bpf_programs, queue, run_event, generic_config.poll_rate),
    )
    poll_thread.start()

    # Create output thread
    output_interval_parse: int | float | None = timeparse(
        generic_config.output_interval
    )
    output_interval = 60
    if output_interval_parse is not None:
        output_interval = output_interval_parse
    ended = False
    output_lock = Lock()
    (user_id, group_id) = get_user_group_ids()
    Path(output_dir / benchmark.name() / collection_id).mkdir(
        parents=True, exist_ok=True
    )
    os.chown(generic_config.get_output_dir(), user_id, group_id)
    os.chown(output_dir, user_id, group_id)
    os.chown(Path(output_dir / benchmark.name()), user_id, group_id)
    os.chown(Path(output_dir / benchmark.name() / collection_id), user_id, group_id)
    output_thread = Thread(
        target=output_data_thread,
        args=(
            collection_id,
            bpf_programs,
            benchmark.name(),
            run_event,
            generic_config.output_dfs,
            output_dir,
            output_lock,
            ended,
            output_interval,
            user_id,
            group_id,
        ),
    )
    output_thread.daemon = True
    output_thread.start()

    tick = datetime.now()

    benchmark.run()

    if verbose:
        print(f"Started benchmark {benchmark.name()}")
    return_code = queue.get()

    collection_time_sec = (datetime.now() - tick).total_seconds()
    poll_thread.join()
    for bpf_program in bpf_programs:
        bpf_program.close()

    if verbose:
        print(f"Benchmark ran for {collection_time_sec}s")

    collection_tables: list[data_schema.CollectionTable] = [
        data_schema.SystemInfoTable.from_df(
            system_info.with_columns(
                [
                    pl.lit(collection_id).alias("collection_id"),
                    pl.lit(collection_time_sec).alias("collection_time_sec"),
                    pl.lit(os.getpid()).alias("collection_pid"),
                    pl.lit(benchmark.name()).alias("benchmark_name"),
                    pl.lit([hook.name() for hook in bpf_programs])
                    .cast(pl.List(pl.String()))
                    .alias("hooks"),
                ]
            )
        )
    ]

    output_lock.acquire()
    ended = True
    collection_tables = output_collections_to_file(
        collection_id,
        collection_tables,
        bpf_programs,
        "end",
        benchmark.name(),
        generic_config.output_dfs,
        output_dir,
        (user_id, group_id),
    )
    output_lock.release()
    collection_data = data_schema.CollectionData.from_tables(collection_tables)

    if generic_config.output_graphs:
        collection_data.graph(
            out_dir=generic_config.get_output_dir() / "graphs",
            use_matplot=True,
            show=False,
        )

    cleaned_collection_id: str | None = None
    if clean and benchmark.name() == "redis":
        cleaned_collection_id = _clean_redis_collection(
            curated_dir=generic_config.get_output_dir() / "curated",
            collection_id=collection_id,
            verbose=verbose,
            ids=(user_id, group_id),
        )

    print(f"Collection_id: {collection_id}")
    if cleaned_collection_id is not None:
        print(f"Cleaned_collection_id: {cleaned_collection_id}")

    return return_code
