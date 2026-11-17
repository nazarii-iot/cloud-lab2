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

def request(base_url, method, path, payload=None):
    start = time.perf_counter()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
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
    args = parser.parse_args()

    if args.users < 1 or min(args.ramp_up, args.steady, args.ramp_down) < 1:
        raise SystemExit("Users and phase durations must be positive")

    base_url = args.url.rstrip("/")
    health_status, _, health_body = await asyncio.to_thread(
        request, base_url, "GET", "/health"
    )

    if health_status != 200:
        raise SystemExit(f"API health check failed: {health_status} {health_body}")

    author_status, _, author_body = await asyncio.to_thread(
        request, base_url, "GET", "/author"
    )

    if author_status != 200:
        raise SystemExit(f"Author endpoint check failed: {author_status} {author_body}")

    metrics = {
        "all": [],
        "phases": {
            "ramp_up": [],
            "steady": [],
            "ramp_down": []
        },
        "endpoints": {},
        "errors": []
    }

    state = {"phase": "ramp_up"}

    async def send(method, path, payload=None):
        status, latency, body = await asyncio.to_thread(
            request, base_url, method, path, payload
        )

        phase = state["phase"]
        endpoint = f"{method} {path.split('?')[0]}"
        result = {
            "status": status,
            "latency_ms": latency,
            "endpoint": endpoint
        }

        metrics["all"].append(result)
        metrics["phases"][phase].append(result)

        if endpoint not in metrics["endpoints"]:
            metrics["endpoints"][endpoint] = {"requests": 0, "errors": 0}

        metrics["endpoints"][endpoint]["requests"] += 1

        if status < 200 or status >= 300:
            metrics["endpoints"][endpoint]["errors"] += 1
            if len(metrics["errors"]) < 20:
                metrics["errors"].append({
                    "endpoint": endpoint,
                    "status": status,
                    "details": body[:300]
                })

        return status, body

    async def virtual_user(user_number, stop_event):
        while not stop_event.is_set():
            status, body = await send(
                "POST",
                "/items",
                {"name": f"Load test item user-{user_number}-{time.time_ns()}"}
            )

            item_id = None
            if 200 <= status < 300:
                try:
                    item_id = json.loads(body).get("id")
                except (ValueError, AttributeError):
                    pass

            read_count = random.randint(8, 10)
            reads = ["/items"] * 3

            if item_id is not None:
                reads.extend([f"/items/{item_id}"] * 3)
            else:
                reads.extend(["/items"] * 3)

            reads.extend(["/author"] * (read_count - 6))
            random.shuffle(reads)

            for path in reads:
                await send("GET", path)

    stop_events = []
    tasks = []
    test_start = time.perf_counter()

    print(f"Target: {base_url}")
    print(f"Virtual users: {args.users}")
    print(f"Ramp-up: {args.ramp_up}s | Steady: {args.steady}s | Ramp-down: {args.ramp_down}s")

    interval = args.ramp_up / args.users
    ramp_start = time.perf_counter()

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
    print("\nSteady load started")

    await asyncio.sleep(args.steady)

    state["phase"] = "ramp_down"
    print("Ramp-down started")

    interval = args.ramp_down / args.users

    for user_number, stop_event in enumerate(stop_events):
        stop_event.set()
        print(f"\rRamp-down: {user_number + 1}/{args.users} users stopped", end="", flush=True)

        if user_number < args.users - 1:
            await asyncio.sleep(interval)

    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)
    except asyncio.TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    duration = time.perf_counter() - test_start

    def summarize(entries, phase_duration):
        latencies = [entry["latency_ms"] for entry in entries]
        errors = sum(
            1 for entry in entries
            if entry["status"] < 200 or entry["status"] >= 300
        )
        return {
            "requests": len(entries),
            "errors": errors,
            "rps": round(len(entries) / phase_duration, 2) if phase_duration else 0,
            "p50_ms": percentile(latencies, 50),
            "p95_ms": percentile(latencies, 95),
            "p99_ms": percentile(latencies, 99)
        }

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "base_url": base_url,
        "virtual_users": args.users,
        "ramp_up_seconds": args.ramp_up,
        "steady_seconds": args.steady,
        "ramp_down_seconds": args.ramp_down,
        "duration_seconds": round(duration, 2),
        "total": summarize(metrics["all"], duration),
        "phases": {
            name: summarize(entries, {"ramp_up": args.ramp_up, "steady": args.steady, "ramp_down": args.ramp_down}[name])
            for name, entries in metrics["phases"].items()
        },
        "endpoints": metrics["endpoints"],
        "errors_sample": metrics["errors"]
    }

    output = Path("monitoring") / "load_test_latest.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("\n\n========== LOAD TEST RESULTS ==========")
    print(json.dumps(result, indent=2))
    print(f"\nSaved: {output}")

if __name__ == "__main__":
    asyncio.run(main())

