#!/bin/bash
# Quick-and-dirty: run redis-server for 10s, capture /proc/<pid>/smaps every second.
# Repeats for NUM_RUNS runs. Output: smaps_run<N>_<second>.txt

set -e

NUM_RUNS=2
DURATION=100
OUTDIR="smaps_output"
REDIS_CONF="./config/redis.conf"

mkdir -p "$OUTDIR"

for run in $(seq 1 $NUM_RUNS); do
    echo "=== Run $run ==="

    # Start redis-server in background
    redis-server "$REDIS_CONF" --dir /tmp --logfile "" &
    REDIS_PID=$!
    echo "redis-server PID: $REDIS_PID"

    # Wait until redis is ready (up to 10 pings)
    for i in $(seq 1 10); do
        if redis-cli ping 2>/dev/null | grep -q PONG; then
            echo "Redis ready"
            break
        fi
        sleep 0.5
    done

    # Collect smaps every second for DURATION seconds
    for sec in $(seq 1 $DURATION); do
        OUTFILE="$OUTDIR/run${run}_${sec}.txt"
        sudo cat "/proc/$REDIS_PID/smaps" > "$OUTFILE" 2>/dev/null || echo "(smaps unavailable)" > "$OUTFILE"
        echo "  [$sec/$DURATION] Wrote $OUTFILE"
        sleep 1
    done

    # Stop redis
    kill "$REDIS_PID" 2>/dev/null && wait "$REDIS_PID" 2>/dev/null || true
    echo "=== Run $run done ==="
    echo ""
done

echo "Done. Files in $OUTDIR/:"
ls "$OUTDIR/"
