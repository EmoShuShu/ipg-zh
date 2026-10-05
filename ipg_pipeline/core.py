from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
SAFE_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return yaml.load(stream, Loader=SAFE_YAML_LOADER)


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        width=100,
        line_break="\n",
    )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


@lru_cache(maxsize=32)
def _schema_validator(schema_name: str, files: tuple[tuple[str, bytes], ...]) -> Draft202012Validator:
    schemas = {name: json.loads(content) for name, content in files}
    registry = Registry()
    for schema in schemas.values():
        if "$id" in schema:
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return Draft202012Validator(schemas[schema_name], registry=registry)


def validate_schema(instance: Any, schema_path: Path) -> list[str]:
    # Read all dependencies afresh: even same-size/same-mtime edits invalidate the cache.
    files = tuple((path.name, path.read_bytes()) for path in sorted(schema_path.parent.glob("*.schema.json")))
    validator = _schema_validator(schema_path.name, files)
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    ]


def walk_nodes(documents: Iterable[dict[str, Any]]) -> Iterable[tuple[str, dict[str, Any]]]:
    for document in documents:
        for section in document.get("sections", []):
            yield "section", section
            for component in section.get("components", []):
                yield "component", component
                for group in component.get("groups", []):
                    yield "group", group
                    for block in group.get("blocks", []):
                        yield "block", block


def anchor_position_is_legal(anchor_type: str, position: str) -> bool:
    if anchor_type == "block":
        return position in {"before", "after"}
    return anchor_type in {"section", "component", "group"} and position in {
        "before",
        "after",
        "inside-start",
        "inside-end",
    }


def validate_registry(registry: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    seen: set[str] = set()
    canonical: set[str] = set()
    for entry in registry.get("entries", []):
        entry_id = entry.get("id", "")
        if entry_id in seen:
            findings.append(f"duplicate registry id: {entry_id}")
        seen.add(entry_id)
        key = entry.get("canonicalKey", "")
        if key in canonical:
            findings.append(f"duplicate canonical key: {key}")
        canonical.add(key)
        if entry.get("status") == "retired" and not any(
            event.get("event") == "retired" for event in entry.get("history", [])
        ):
            findings.append(f"retired id has no tombstone event: {entry_id}")
    return findings


def allocate_registry_id(
    registry: dict[str, Any], *, entry_id: str, kind: str, canonical_key: str, version: str, label: str
) -> None:
    all_ids = {entry["id"] for entry in registry.get("entries", [])}
    all_keys = {entry["canonicalKey"] for entry in registry.get("entries", [])}
    if entry_id in all_ids:
        raise ValueError(f"id has already been allocated and may not be reused: {entry_id}")
    if canonical_key in all_keys:
        raise ValueError(f"canonical key already exists: {canonical_key}")
    registry.setdefault("entries", []).append(
        {
            "id": entry_id,
            "kind": kind,
            "status": "active",
            "canonicalKey": canonical_key,
            "history": [{"event": "allocated", "atVersion": version, "label": label}],
        }
    )


def update_registry_identity(
    registry: dict[str, Any], *, entry_id: str, event: str, version: str, **details: str
) -> None:
    if event not in {"renamed", "renumbered", "retired", "split", "merged"}:
        raise ValueError(f"unsupported registry event: {event}")
    entry = next((item for item in registry.get("entries", []) if item["id"] == entry_id), None)
    if entry is None:
        raise KeyError(entry_id)
    history = {"event": event, "atVersion": version, **details}
    entry.setdefault("history", []).append(history)
    if event == "retired":
        entry["status"] = "retired"

