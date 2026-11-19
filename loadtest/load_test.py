import argparse
import asyncio
import json
import math
import random
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode


def request(base_url, method, path, payload=None, timeout=10):
    start = time.perf_counter()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode()
            return response.status, (time.perf_counter() - start) * 1000, body
    except urllib.error.HTTPError as error:
        return error.code, (time.perf_counter() - start) * 1000, error.read().decode()
    except Exception as error:
        return 0, (time.perf_counter() - start) * 1000, str(error)


def percentile(values, percent):
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return round(ordered[index], 2)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--users", type=int, default=30)
    parser.add_argument("--ramp-up", type=int, default=180)
    parser.add_argument("--steady", type=int, default=300)
    parser.add_argument("--ramp-down", type=int, default=120)
    parser.add_argument("--think-time", type=float, default=0.2)
    parser.add_argument("--request-timeout", type=float, default=10)
    parser.add_argument("--read-min", type=int, default=8)
    parser.add_argument("--read-max", type=int, default=10)
    args = parser.parse_args()

    if args.users < 1 or min(args.ramp_up, args.steady, args.ramp_down) < 1:
        raise SystemExit("Users and phase durations must be positive")
    if args.think_time < 0 or args.request_timeout <= 0:
        raise SystemExit("Think time must be non-negative and request timeout must be positive")
    if args.read_min < 1 or args.read_max < args.read_min:
        raise SystemExit("Read count range is invalid")

    base_url = args.url.rstrip("/")
    request_timeout = args.request_timeout

    health_status, _, health_body = await asyncio.to_thread(
        request, base_url, "GET", "/health", None, request_timeout
    )
    if health_status != 200:
        raise SystemExit(f"API health check failed: {health_status} {health_body}")

    metrics = {
        "all": [],
        "phases": {"ramp_up": [], "steady": [], "ramp_down": []},
        "endpoints": {},
        "errors": [],
    }
    state = {"phase": "ramp_up"}

    async def send(method, path, payload=None):
        request_phase = state["phase"]
        status, latency, body = await asyncio.to_thread(
            request, base_url, method, path, payload, request_timeout
        )
        endpoint = f"{method} {path.split('?')[0]}"
        result = {
            "status": status,
            "latency_ms": round(latency, 2),
            "endpoint": endpoint,
            "phase": request_phase,
            "elapsed_seconds": round(time.perf_counter() - test_start, 3),
        }
        metrics["all"].append(result)
        metrics["phases"][request_phase].append(result)

        if endpoint not in metrics["endpoints"]:
            metrics["endpoints"][endpoint] = {"requests": 0, "errors": 0, "latencies_ms": []}
        metrics["endpoints"][endpoint]["requests"] += 1
        metrics["endpoints"][endpoint]["latencies_ms"].append(round(latency, 2))

        if status < 200 or status >= 300:
            metrics["endpoints"][endpoint]["errors"] += 1
            if len(metrics["errors"]) < 20:
                metrics["errors"].append({
                    "endpoint": endpoint,
                    "status": status,
                    "latency_ms": round(latency, 2),
                    "details": body[:300],
                })
        return status, body

    async def virtual_user(user_number, stop_event):
        status, body = await send(
            "POST",
            "/items",
            {"name": f"Load test item user-{user_number}-{time.time_ns()}"},
        )

        item_id = None
        if 200 <= status < 300:
            try:
                item_id = json.loads(body).get("id")
            except (ValueError, AttributeError):
                pass

        if item_id is not None:
            item_path = f"/items/{item_id}"
        else:
            item_path = "/items?" + urlencode({"limit": 50, "offset": 0})

        while not stop_event.is_set():
            read_count = random.randint(args.read_min, args.read_max)
            reads = [item_path] * max(0, read_count - 1)
            reads.append("/items?" + urlencode({"limit": 50, "offset": 0}))
            random.shuffle(reads)

            for index, path in enumerate(reads):
                if stop_event.is_set():
                    break
                await send("GET", path)
                if index < len(reads) - 1 and args.think_time:
                    await asyncio.sleep(args.think_time)

    stop_events = []
    tasks = []
    timeline = {"test_started_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    test_start = time.perf_counter()
    ramp_start = time.perf_counter()

    print(f"Target: {base_url}")
    print(f"Virtual users: {args.users}")
    print(f"Ramp-up: {args.ramp_up}s | Steady: {args.steady}s | Ramp-down: {args.ramp_down}s")
    print(f"Read requests per cycle: {args.read_min}-{args.read_max}")
    print(f"Think time: {args.think_time}s | Request timeout: {request_timeout}s")

    interval = args.ramp_up / args.users
    for user_number in range(args.users):
        stop_event = asyncio.Event()
        stop_events.append(stop_event)
        tasks.append(asyncio.create_task(virtual_user(user_number + 1, stop_event)))
        print(f"\rRamp-up: {user_number + 1}/{args.users} users", end="", flush=True)
        if user_number < args.users - 1:
            await asyncio.sleep(interval)

    remaining = args.ramp_up - (time.perf_counter() - ramp_start)
    if remaining > 0:
        await asyncio.sleep(remaining)

    state["phase"] = "steady"
    timeline["steady_started_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    print("\nSteady load started")
    await asyncio.sleep(args.steady)

    state["phase"] = "ramp_down"
    timeline["ramp_down_started_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    print("Ramp-down started")
    interval = args.ramp_down / args.users
    for user_number, stop_event in enumerate(stop_events):
        stop_event.set()
        print(f"\rRamp-down: {user_number + 1}/{args.users} users stopped", end="", flush=True)
        if user_number < args.users - 1:
            await asyncio.sleep(interval)

    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=request_timeout + 15)
    except asyncio.TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    duration = time.perf_counter() - test_start
    timeline["test_finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    def summarize(entries, phase_duration):
        latencies = [entry["latency_ms"] for entry in entries]
        errors = sum(1 for entry in entries if entry["status"] < 200 or entry["status"] >= 300)
        return {
            "requests": len(entries),
            "errors": errors,
            "rps": round(len(entries) / phase_duration, 2) if phase_duration else 0,
            "p50_ms": percentile(latencies, 50),
            "p95_ms": percentile(latencies, 95),
            "p99_ms": percentile(latencies, 99),
        }

    timestamp = datetime.now().astimezone()
    result = {
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "timeline": timeline,
        "base_url": base_url,
        "virtual_users": args.users,
        "ramp_up_seconds": args.ramp_up,
        "steady_seconds": args.steady,
        "ramp_down_seconds": args.ramp_down,
        "read_requests_per_cycle": [args.read_min, args.read_max],
        "think_time_seconds": args.think_time,
        "request_timeout_seconds": request_timeout,
        "records_created": (
            metrics["endpoints"].get("POST /items", {}).get("requests", 0)
            - metrics["endpoints"].get("POST /items", {}).get("errors", 0)
        ),
        "duration_seconds": round(duration, 2),
        "total": summarize(metrics["all"], duration),
        "phases": {
            name: summarize(
                entries,
                {"ramp_up": args.ramp_up, "steady": args.steady, "ramp_down": args.ramp_down}[name],
            )
            for name, entries in metrics["phases"].items()
        },
        "endpoints": {
            endpoint: {
                "requests": values["requests"],
                "errors": values["errors"],
                "p50_ms": percentile(values["latencies_ms"], 50),
                "p95_ms": percentile(values["latencies_ms"], 95),
                "p99_ms": percentile(values["latencies_ms"], 99),
                "max_ms": round(max(values["latencies_ms"]), 2) if values["latencies_ms"] else 0,
            }
            for endpoint, values in metrics["endpoints"].items()
        },
        "errors_sample": metrics["errors"],
    }

    output_dir = Path("monitoring")
    output_dir.mkdir(exist_ok=True)
    run_id = timestamp.strftime("%Y%m%d_%H%M%S")
    versioned_output = output_dir / f"load_test_{run_id}.json"
    latest_output = output_dir / "load_test_latest.json"
    versioned_raw_output = output_dir / f"load_test_{run_id}_raw.json"
    latest_raw_output = output_dir / "load_test_latest_raw.json"
    raw_result = {
        "timestamp": result["timestamp"],
        "timeline": timeline,
        "base_url": base_url,
        "request_count": len(metrics["all"]),
        "requests": metrics["all"],
    }
    result["raw_result_file"] = versioned_raw_output.name
    serialized = json.dumps(result, indent=2, ensure_ascii=False)
    serialized_raw = json.dumps(raw_result, indent=2, ensure_ascii=False)
    versioned_output.write_text(serialized, encoding="utf-8")
    latest_output.write_text(serialized, encoding="utf-8")
    versioned_raw_output.write_text(serialized_raw, encoding="utf-8")
    latest_raw_output.write_text(serialized_raw, encoding="utf-8")

    print("\n\n========== LOAD TEST RESULTS ==========")
    print(serialized)
    print(f"\nSaved summary: {versioned_output}")
    print(f"Saved raw requests: {versioned_raw_output}")
    print(f"Updated latest files in: {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
