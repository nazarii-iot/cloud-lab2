import json
import os
from contextlib import asynccontextmanager

import psycopg
import redis
from fastapi import FastAPI, HTTPException, Query
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://clouduser:cloudpass@localhost:5432/clouddb",
)
REDIS_URL = os.getenv("REDIS_URL", "").strip()
BUILD_ID = os.getenv("BUILD_ID", "local")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "45"))
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "10"))

redis_client = None
db_pool = None
local_cache_stats = {"hits": 0, "misses": 0}
CACHE_HITS_KEY = "cache:stats:hits"
CACHE_MISSES_KEY = "cache:stats:misses"
CACHE_READ_SCRIPT = """
local value = redis.call("GET", KEYS[1])
if value then
    redis.call("INCR", KEYS[2])
else
    redis.call("INCR", KEYS[3])
end
return value
"""


def initialize_database():
    with db_pool.connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client

    db_pool = ConnectionPool(
        conninfo=DATABASE_URL,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE,
        timeout=10,
        kwargs={"connect_timeout": 5},
    )
    db_pool.wait(timeout=15)
    initialize_database()

    if REDIS_URL:
        redis_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
            max_connections=100,
        )

    yield

    if redis_client:
        redis_client.close()
    if db_pool:
        db_pool.close()


app = FastAPI(
    title="Cloud Lab 2 REST API",
    description="REST API with PostgreSQL and Redis cache-aside caching",
    version="3.0.0",
    lifespan=lifespan,
)


class ItemCreate(BaseModel):
    name: str


def record_cache_stat(name):
    if redis_client:
        try:
            key = CACHE_HITS_KEY if name == "hits" else CACHE_MISSES_KEY
            redis_client.incr(key)
            return
        except redis.RedisError:
            pass

    local_cache_stats[name] += 1


def get_cached(key):
    if not redis_client:
        record_cache_stat("misses")
        return None

    try:
        value = redis_client.eval(
            CACHE_READ_SCRIPT,
            3,
            key,
            CACHE_HITS_KEY,
            CACHE_MISSES_KEY,
        )
        return json.loads(value) if value is not None else None
    except (redis.RedisError, ValueError):
        record_cache_stat("misses")
        return None


def set_cached(key, value):
    if not redis_client:
        return

    try:
        redis_client.setex(key, CACHE_TTL, json.dumps(value, separators=(",", ":")))
    except redis.RedisError:
        pass


def invalidate_cache():
    if not redis_client:
        return

    try:
        batch = []
        for key in redis_client.scan_iter(match="items:list:*", count=500):
            batch.append(key)
            if len(batch) == 500:
                redis_client.delete(*batch)
                batch.clear()
        if batch:
            redis_client.delete(*batch)
    except redis.RedisError:
        pass


def get_cache_counters():
    if redis_client:
        try:
            hits, misses = redis_client.mget(CACHE_HITS_KEY, CACHE_MISSES_KEY)
            return int(hits or 0), int(misses or 0), True
        except (redis.RedisError, TypeError, ValueError):
            pass

    return local_cache_stats["hits"], local_cache_stats["misses"], False


@app.get("/")
def root():
    return {"message": "Cloud Lab 2 API is running", "version": app.version, "build_id": BUILD_ID}


@app.get("/health")
def health():
    try:
        with db_pool.connection() as connection:
            connection.execute("SELECT 1")
    except (psycopg.Error, AttributeError):
        raise HTTPException(status_code=503, detail="Database unavailable")

    redis_status = "disabled"
    if redis_client:
        try:
            redis_client.ping()
            redis_status = "connected"
        except redis.RedisError:
            redis_status = "unavailable"

    return {"status": "ok", "database": "connected", "redis": redis_status}


@app.get("/health/redis")
def redis_health():
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis is not configured")

    try:
        redis_client.ping()
        return {"status": "ok", "redis": "connected"}
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Redis unavailable")


@app.get("/author")
def get_author():
    return {
        "name": os.getenv("AUTHOR_NAME", "Not specified"),
        "group": os.getenv("AUTHOR_GROUP", "Not specified"),
    }


@app.get("/items")
def get_items(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    key = f"items:list:v3:{limit}:{offset}"
    cached = get_cached(key)
    if cached is not None:
        return cached

    try:
        with db_pool.connection() as connection:
            rows = connection.execute(
                "SELECT id, name FROM items ORDER BY id LIMIT %s OFFSET %s",
                (limit, offset),
            ).fetchall()

        result = [{"id": row[0], "name": row[1]} for row in rows]
        set_cached(key, result)
        return result
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.get("/items/{item_id}")
def get_item(item_id: int):
    key = f"items:{item_id}"
    cached = get_cached(key)
    if cached is not None:
        return cached

    try:
        with db_pool.connection() as connection:
            row = connection.execute(
                "SELECT id, name FROM items WHERE id = %s",
                (item_id,),
            ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Item not found")

        result = {"id": row[0], "name": row[1]}
        set_cached(key, result)
        return result
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/items", status_code=201)
def create_item(item: ItemCreate):
    try:
        with db_pool.connection() as connection:
            row = connection.execute(
                "INSERT INTO items (name) VALUES (%s) RETURNING id, name",
                (item.name,),
            ).fetchone()

        invalidate_cache()
        return {"id": row[0], "name": row[1]}
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.get("/cache/stats")
def get_cache_stats():
    hits, misses, redis_connected = get_cache_counters()
    total = hits + misses
    hit_ratio = hits / total if total else 0

    return {
        "hits": hits,
        "misses": misses,
        "hit_ratio": round(hit_ratio, 4),
        "ttl_seconds": CACHE_TTL,
        "redis_enabled": redis_client is not None,
        "redis_connected": redis_connected,
    }


@app.post("/cache/reset")
def reset_cache_stats():
    local_cache_stats["hits"] = 0
    local_cache_stats["misses"] = 0

    if redis_client:
        try:
            redis_client.delete(CACHE_HITS_KEY, CACHE_MISSES_KEY)
        except redis.RedisError:
            raise HTTPException(status_code=503, detail="Redis unavailable")

    return {"status": "reset"}
