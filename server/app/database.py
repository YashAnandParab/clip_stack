"""MongoDB persistence for articles and per-user taxonomy."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "clipstack")

client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
database = client[MONGO_DATABASE]
articles = database["articles"]
categories = database["categories"]

DEFAULT_TAXONOMY = {
    "Investor Finance": [
        "Behavioral Finance",
        "Personal Finance",
        "Nano Learning",
        "Investing",
        "Markets",
        "Money",
    ],
    "Work": ["AI", "Database"],
    "News": ["Political", "Sports"],
    "Entertainment": ["Movies", "Songs"],
}


def init_database(attempts: int = 15, delay_seconds: float = 2) -> None:
    last_error: PyMongoError | None = None
    for attempt in range(attempts):
        try:
            client.admin.command("ping")
            articles.create_index(
                [("owner_id", ASCENDING), ("path", ASCENDING)], unique=True
            )
            articles.create_index(
                [("owner_id", ASCENDING), ("clipped_at", DESCENDING)]
            )
            categories.create_index(
                [("owner_id", ASCENDING), ("name_key", ASCENDING)],
                unique=True,
                partialFilterExpression={"kind": "category"},
            )
            return
        except PyMongoError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


def database_available() -> bool:
    try:
        client.admin.command("ping")
        return True
    except PyMongoError:
        return False


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _article(document: dict[str, Any], *, include_markdown: bool = False) -> dict[str, Any]:
    result = {key: value for key, value in document.items() if key != "owner_id"}
    result["id"] = str(result.pop("_id"))
    if not include_markdown:
        result.pop("markdown", None)
    return result


def record_article(
    owner_id: str,
    values: dict[str, Any],
    path: str,
    filename: str,
    markdown: str,
    source_client: str,
) -> str:
    document = {
        "owner_id": owner_id,
        "url": values["url"],
        "title": values["title"],
        "site": values["site"] or "",
        "author": values["author"] or "",
        "published": values["published"] or "",
        "word_count": values["word_count"] or 0,
        "strategy": values["strategy"] or "",
        "category": values["category"],
        "subcategory": values["subcategory"],
        "path": path,
        "filename": filename,
        "markdown": markdown,
        "source_client": source_client,
        "clipped_at": datetime.now(timezone.utc),
    }
    saved = articles.find_one_and_update(
        {"owner_id": owner_id, "path": path},
        {"$set": document},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    assert saved is not None
    return str(saved["_id"])


def list_articles(owner_id: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    query = {"owner_id": owner_id}
    total = articles.count_documents(query)
    cursor = (
        articles.find(query, {"markdown": 0, "owner_id": 0})
        .sort("clipped_at", DESCENDING)
        .skip(offset)
        .limit(limit)
    )
    return [_article(document) for document in cursor], total


def get_article(owner_id: str, article_id: str, *, include_markdown: bool = True) -> dict[str, Any] | None:
    object_id = _object_id(article_id)
    if object_id is None:
        return None
    document = articles.find_one({"_id": object_id, "owner_id": owner_id})
    return _article(document, include_markdown=include_markdown) if document else None


def delete_article_record(owner_id: str, article_id: str) -> bool:
    object_id = _object_id(article_id)
    return bool(
        object_id
        and articles.delete_one({"_id": object_id, "owner_id": owner_id}).deleted_count
    )


def ensure_default_categories(owner_id: str) -> None:
    marker = categories.update_one(
        {"owner_id": owner_id, "kind": "seed"},
        {"$setOnInsert": {"owner_id": owner_id, "kind": "seed"}},
        upsert=True,
    )
    if marker.upserted_id is not None:
        categories.insert_many(
            {
                "owner_id": owner_id,
                "kind": "category",
                "name": name,
                "name_key": name.casefold(),
                "subcategories": subcategories,
            }
            for name, subcategories in DEFAULT_TAXONOMY.items()
        )


def list_categories(owner_id: str) -> list[dict[str, Any]]:
    ensure_default_categories(owner_id)
    return [
        {
            "id": str(document["_id"]),
            "name": document["name"],
            "subcategories": document.get("subcategories", []),
        }
        for document in categories.find(
            {"owner_id": owner_id, "kind": "category"}
        ).sort("name_key", ASCENDING)
    ]


def create_category(owner_id: str, name: str) -> dict[str, Any] | None:
    ensure_default_categories(owner_id)
    name = " ".join(name.split())
    try:
        result = categories.insert_one(
            {
                "owner_id": owner_id,
                "kind": "category",
                "name": name,
                "name_key": name.casefold(),
                "subcategories": [],
            }
        )
    except DuplicateKeyError:
        return None
    return {"id": str(result.inserted_id), "name": name, "subcategories": []}


def add_subcategory(owner_id: str, category_id: str, name: str) -> str:
    object_id = _object_id(category_id)
    document = object_id and categories.find_one(
        {"_id": object_id, "owner_id": owner_id, "kind": "category"}
    )
    if not document:
        return "not_found"
    name = " ".join(name.split())
    if any(value.casefold() == name.casefold() for value in document["subcategories"]):
        return "duplicate"
    categories.update_one({"_id": object_id}, {"$push": {"subcategories": name}})
    return "created"


def delete_subcategory(owner_id: str, category_id: str, name: str) -> bool:
    object_id = _object_id(category_id)
    document = object_id and categories.find_one(
        {"_id": object_id, "owner_id": owner_id, "kind": "category"}
    )
    if not document:
        return False
    stored = next(
        (value for value in document["subcategories"] if value.casefold() == name.casefold()),
        None,
    )
    if stored is None:
        return False
    categories.update_one({"_id": object_id}, {"$pull": {"subcategories": stored}})
    return True


def delete_category(owner_id: str, category_id: str) -> str:
    object_id = _object_id(category_id)
    document = object_id and categories.find_one(
        {"_id": object_id, "owner_id": owner_id, "kind": "category"}
    )
    if not document:
        return "not_found"
    if document.get("subcategories"):
        return "not_empty"
    categories.delete_one({"_id": object_id})
    return "deleted"


def valid_taxonomy(owner_id: str, category: str, subcategory: str) -> tuple[str, str]:
    if not category:
        return "", ""
    ensure_default_categories(owner_id)
    document = categories.find_one(
        {
            "owner_id": owner_id,
            "kind": "category",
            "name_key": category.casefold(),
        }
    )
    if not document:
        return "", ""
    canonical_category = document["name"]
    canonical_subcategory = next(
        (
            value
            for value in document.get("subcategories", [])
            if value.casefold() == subcategory.casefold()
        ),
        "",
    )
    return canonical_category, canonical_subcategory
