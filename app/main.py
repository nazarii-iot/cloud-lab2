import json
import os
from contextlib import asynccontextmanager

import psycopg
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://clouduser:cloudpass@localhost:5432/clouddb",
)
REDIS_URL = os.getenv("REDIS_URL")
CACHE_TTL = 45

redis_client = None
cache_stats = {"hits": 0, "misses": 0}


def initialize_database():
    with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client

    if os.getenv("DATABASE_URL"):
        initialize_database()

    if REDIS_URL:
        redis_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    yield

    if redis_client:
        redis_client.close()


app = FastAPI(
    title="Cloud Lab 2 REST API",
    description="REST API with PostgreSQL and Redis caching",
    version="2.0.0",
    lifespan=lifespan,
)


class ItemCreate(BaseModel):
    name: str


def get_cached(key):
    if not redis_client:
        cache_stats["misses"] += 1
        return None

    try:
        value = redis_client.get(key)
        if value is not None:
            cache_stats["hits"] += 1
            return json.loads(value)
    except redis.RedisError:
        pass

    cache_stats["misses"] += 1
    return None


def set_cached(key, value):
    if not redis_client:
        return

    try:
        redis_client.setex(key, CACHE_TTL, json.dumps(value))
    except redis.RedisError:
        pass


def invalidate_cache():
    if not redis_client:
        return

    try:
        redis_client.delete("items:list")
        for key in redis_client.scan_iter("items:*"):
            redis_client.delete(key)
    except redis.RedisError:
        pass


@app.get("/")
def root():
    return {"message": "Cloud Lab 2 API is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/author")
def get_author():
    return {
        "name": os.getenv("AUTHOR_NAME", "Not specified"),
        "group": os.getenv("AUTHOR_GROUP", "Not specified"),
    }


@app.get("/items")
def get_items():
    cached = get_cached("items:list")
    if cached is not None:
        return cached

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
            rows = connection.execute(
                "SELECT id, name FROM items ORDER BY id"
            ).fetchall()

        result = [{"id": row[0], "name": row[1]} for row in rows]
        set_cached("items:list", result)
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
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
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
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
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
    total = cache_stats["hits"] + cache_stats["misses"]
    hit_ratio = cache_stats["hits"] / total if total else 0

    return {
        "hits": cache_stats["hits"],
        "misses": cache_stats["misses"],
        "hit_ratio": round(hit_ratio, 4),
        "ttl_seconds": CACHE_TTL,
        "redis_enabled": redis_client is not None,
    }


@app.post("/cache/reset")
def reset_cache_stats():
    cache_stats["hits"] = 0
    cache_stats["misses"] = 0
    return {"status": "reset"}
