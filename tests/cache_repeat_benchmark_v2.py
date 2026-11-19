import argparse
import concurrent.futures
import http.client
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


local = threading.local()


def percentile(values, percent):
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return round(ordered[index], 2)


def now():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def make_connection(parts, timeout):
    host = parts.hostname
    port = parts.port

    if parts.scheme == "https":
        return http.client.HTTPSConnection(host, port or 443, timeout=timeout)

    if parts.scheme == "http":
        return http.client.HTTPConnection(host, port or 80, timeout=timeout)

    raise ValueError("Only HTTP and HTTPS URLs are supported")


def target_path(parts, path):
    prefix = parts.path.rstrip("/")
    return prefix + path


def control_request(parts, path, method="GET", payload=None, timeout=15):
    connection = make_connection(parts, timeout)

    try:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Connection": "close",
        }

        connection.request(method, target_path(parts, path), body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read().decode("utf-8")

        if response.status < 200 or response.status >= 300:
            raise RuntimeError(
                f"{method} {path}: HTTP {response.status}: {response_body[:300]}"
            )

        return response.status, json.loads(response_body) if response_body else None

    finally:
        connection.close()


def get_worker_connection(parts, timeout):
    connection = getattr(local, "connection", None)

    if connection is None or connection.sock is None:
        connection = make_connection(parts, timeout)

        started = time.perf_counter()
        connection.connect()
        connect_ms = (time.perf_counter() - started) * 1000

        local.connection = connection
        return connection, round(connect_ms, 2), True

    return connection, 0.0, False


def one_read(number, parts, path, timeout):
    started_at = now()
    total_started = time.perf_counter()
    connect_ms = 0.0
    request_ms = 0.0
    connection_created = False
    status = 0
    error = None

    try:
        connection, connect_ms, connection_created = get_worker_connection(
            parts, timeout
        )

        request_started = time.perf_counter()

        connection.request(
            "GET",
            target_path(parts, path),
            headers={
                "Accept": "application/json",
                "Connection": "keep-alive",
            },
        )

        response = connection.getresponse()
        response_body = response.read()

        request_ms = (time.perf_counter() - request_started) * 1000
        status = response.status

        if response.will_close:
            connection.close()
            local.connection = None

        if status < 200 or status >= 300:
            error = response_body.decode("utf-8", errors="replace")[:300]

    except Exception as exc:
        error = str(exc)[:300]
        connection = getattr(local, "connection", None)

        if connection is not None:
            connection.close()

        local.connection = None

    finished_at = now()
    total_ms = (time.perf_counter() - total_started) * 1000

    return {
        "number": number,
        "endpoint": f"GET {path}",
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "connect_ms": round(connect_ms, 2),
        "connection_created": connection_created,
        "request_ms": round(request_ms, 2),
        "total_ms": round(total_ms, 2),
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", choices=["redis-off", "redis-on"], required=True)
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()

    if args.requests < 1 or args.workers < 1 or args.timeout <= 0:
        raise SystemExit("Requests, workers, and timeout must be positive")

    parts = urlsplit(args.url)

    if not parts.hostname or parts.scheme not in ("http", "https"):
        raise SystemExit("A valid HTTP or HTTPS base URL is required")

    base_url = args.url.rstrip("/")
    expected_redis = args.mode == "redis-on"

    health_status, health = control_request(parts, "/health")
    if health_status != 200 or health.get("status") != "ok":
        raise SystemExit("API health check failed")

    stats = control_request(parts, "/cache/stats")[1]

    if stats.get("redis_enabled") is not expected_redis:
        raise SystemExit(
            f"Wrong cache mode: expected redis_enabled={expected_redis}, "
            f"got {stats.get('redis_enabled')}"
        )

    if expected_redis and not stats.get("redis_connected"):
        raise SystemExit("Redis is enabled but not connected")

    _, created = control_request(
        parts,
        "/items",
        method="POST",
        payload={"name": f"Redis benchmark v2 {time.time_ns()}"},
    )

    item_id = created["id"]
    path = f"/items/{item_id}"

    control_request(parts, path)
    control_request(parts, "/cache/reset", method="POST")

    started_at = now()
    test_started = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.workers
    ) as executor:
        results = list(
            executor.map(
                lambda number: one_read(
                    number, parts, path, args.timeout
                ),
                range(1, args.requests + 1),
            )
        )

    duration = time.perf_counter() - test_started
    finished_at = now()

    final_stats = control_request(parts, "/cache/stats")[1]

    total_latencies = [item["total_ms"] for item in results]
    request_latencies = [
        item["request_ms"] for item in results if item["status"] > 0
    ]
    connection_latencies = [
        item["connect_ms"]
        for item in results
        if item["connection_created"]
    ]

    errors = [
        item for item in results
        if item["status"] < 200 or item["status"] >= 300
    ]

    result = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": args.mode,
        "base_url": base_url,
        "requests": args.requests,
        "workers": args.workers,
        "timeout_seconds": args.timeout,
        "item_id": item_id,
        "test_started_at": started_at,
        "test_finished_at": finished_at,
        "duration_seconds": round(duration, 3),
        "rps": round(len(results) / duration, 2) if duration else 0,
        "errors": len(errors),
        "total_latency_ms": {
            "p50": percentile(total_latencies, 50),
            "p95": percentile(total_latencies, 95),
            "p99": percentile(total_latencies, 99),
            "max": round(max(total_latencies), 2) if total_latencies else 0,
        },
        "http_request_ms": {
            "p50": percentile(request_latencies, 50),
            "p95": percentile(request_latencies, 95),
            "p99": percentile(request_latencies, 99),
        },
        "connection_setup_ms": {
            "new_connections": len(connection_latencies),
            "p50": percentile(connection_latencies, 50),
            "p95": percentile(connection_latencies, 95),
            "max": round(max(connection_latencies), 2) if connection_latencies else 0,
        },
        "cache_stats": final_stats,
        "errors_sample": errors[:20],
        "raw_requests": results,
    }

    output_dir = Path("monitoring")
    output_dir.mkdir(exist_ok=True)

    run_id = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    versioned = output_dir / f"cache_benchmark_v2_{args.mode}_{run_id}.json"
    latest = output_dir / f"cache_benchmark_v2_{args.mode}_latest.json"

    serialized = json.dumps(result, indent=2, ensure_ascii=False)

    versioned.write_text(serialized, encoding="utf-8")
    latest.write_text(serialized, encoding="utf-8")

    for connection in [getattr(local, "connection", None)]:
        if connection is not None:
            connection.close()

    print(serialized)
    print(f"\nSaved: {versioned}")


if __name__ == "__main__":
    main()