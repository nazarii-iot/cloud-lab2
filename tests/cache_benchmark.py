import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.request
from pathlib import Path

BASE_URL = "http://localhost:8080"
REQUEST_COUNT = 300
WORKERS = 20


def request(path):
    start = time.perf_counter()
    req = urllib.request.Request(BASE_URL + path)

    with urllib.request.urlopen(req, timeout=30) as response:
        response.read()
        status = response.status

    return (time.perf_counter() - start) * 1000, status


def percentile(values, percent):
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((percent / 100) * len(ordered) + 0.5) - 1))
    return round(ordered[index], 2)


def post(path):
    req = urllib.request.Request(BASE_URL + path, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["off", "on"], required=True)
    args = parser.parse_args()

    request("/items")
    post("/cache/reset")

    latencies = []
    errors = 0
    start = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(request, "/items") for _ in range(REQUEST_COUNT)]

        for future in concurrent.futures.as_completed(futures):
            try:
                latency, status = future.result()
                latencies.append(latency)
                if status != 200:
                    errors += 1
            except Exception:
                errors += 1

    duration = time.perf_counter() - start
    stats = json.loads(urllib.request.urlopen(BASE_URL + "/cache/stats").read().decode())

    result = {
        "mode": args.mode,
        "requests": REQUEST_COUNT,
        "workers": WORKERS,
        "successful_requests": len(latencies),
        "errors": errors,
        "duration_seconds": round(duration, 2),
        "rps": round(len(latencies) / duration, 2),
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "cache_hits": stats["hits"],
        "cache_misses": stats["misses"],
        "hit_ratio": stats["hit_ratio"],
        "redis_enabled": stats["redis_enabled"]
    }

    output = Path("monitoring") / f"cache_{args.mode}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    print(f"\nSaved: {output}")


if __name__ == "__main__":
    main()
