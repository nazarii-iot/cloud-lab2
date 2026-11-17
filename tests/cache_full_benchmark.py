import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
BASE_URL = "http://localhost:8080"

MONITORING.mkdir(exist_ok=True)

def run(command):
    subprocess.run(command, cwd=ROOT, check=True)

def request_json(path, method="GET"):
    req = urllib.request.Request(BASE_URL + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode())

def wait_for_api():
    for _ in range(60):
        try:
            request_json("/health")
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("API did not become healthy")

def postgres_stats():
    sql = (
        "SELECT xact_commit, xact_rollback, blks_read, blks_hit, "
        "tup_returned, tup_fetched "
        "FROM pg_stat_database WHERE datname = current_database();"
    )
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "postgres",
            "psql", "-U", "clouduser", "-d", "clouddb",
            "-At", "-F", ",", "-c", sql
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True
    )
    values = result.stdout.strip().split(",")
    if len(values) != 6:
        raise RuntimeError("Unexpected PostgreSQL statistics: " + result.stdout)
    names = [
        "xact_commit",
        "xact_rollback",
        "blks_read",
        "blks_hit",
        "tup_returned",
        "tup_fetched"
    ]
    return {name: int(value) for name, value in zip(names, values)}

def compose(mode):
    command = ["docker", "compose", "-f", "docker-compose.yml"]
    if mode == "off":
        command += ["-f", "docker-compose.cache-off.yml"]
    return command

def run_scenario(mode):
    print(f"\n========== CACHE {mode.upper()} ==========", flush=True)

    run(compose(mode) + ["up", "-d", "--force-recreate", "api"])
    wait_for_api()

    if mode == "on":
        run([
            "docker", "compose", "exec", "-T",
            "redis", "redis-cli", "FLUSHDB"
        ])

    request_json("/cache/reset", "POST")
    db_before = postgres_stats()

    run([
        sys.executable,
        str(ROOT / "loadtest" / "load_test.py"),
        "--url", BASE_URL,
        "--users", "30",
        "--ramp-up", "180",
        "--steady", "300",
        "--ramp-down", "120"
    ])

    load_test_path = MONITORING / "load_test_latest.json"
    if not load_test_path.exists():
        raise RuntimeError("Load test result file was not created")

    load_result = json.loads(load_test_path.read_text(encoding="utf-8"))
    cache_result = request_json("/cache/stats")
    db_after = postgres_stats()

    db_delta = {
        key: db_after[key] - db_before[key]
        for key in db_before
    }

    result = {
        "mode": mode,
        "load_test": load_result,
        "cache": cache_result,
        "postgres_before": db_before,
        "postgres_after": db_after,
        "postgres_delta": db_delta
    }

    output = MONITORING / f"cache_scenario_{mode}.json"
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    print(f"Saved: {output}", flush=True)

def main():
    try:
        run(["docker", "compose", "up", "-d"])
        wait_for_api()
        run_scenario("off")
        run_scenario("on")
    finally:
        print("\n========== RESTORE NORMAL COMPOSE ==========", flush=True)
        try:
            run([
                "docker", "compose",
                "-f", "docker-compose.yml",
                "up", "-d", "--force-recreate", "api"
            ])
            wait_for_api()
            print("API restored with normal Redis configuration.", flush=True)
        except Exception as error:
            print(f"Could not restore API automatically: {error}", flush=True)

    comparison = {
        "cache_off": json.loads(
            (MONITORING / "cache_scenario_off.json").read_text(encoding="utf-8")
        ),
        "cache_on": json.loads(
            (MONITORING / "cache_scenario_on.json").read_text(encoding="utf-8")
        )
    }
    output = MONITORING / "cache_scenario_comparison.json"
    output.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\nComparison saved: {output}", flush=True)

if __name__ == "__main__":
    main()
