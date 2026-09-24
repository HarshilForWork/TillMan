"""Test access to the vendored UCP spec: its official JSON Schemas and its annotated examples.

The spec docs annotate every JSON example with `<!-- ucp:example schema=... op=... direction=...
extract=... -->`. UCP's own validator (scripts/validate_examples.py) also merges elided examples
into scaffolds; this harness only uses the examples that are complete as written, which is the
strict subset: an example with a `...` elision is skipped, never repaired.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from tillhand.core.constants import UCP_VERSION

SPEC_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "ucp" / f"v{UCP_VERSION}"
SCHEMA_ROOT = SPEC_ROOT / "source" / "schemas"
CATALOG_DOCS = SPEC_ROOT / "docs" / "specification" / "shopping" / "catalog"
OVERVIEW = SPEC_ROOT / "docs" / "specification" / "overview" / "index.md"
SCAFFOLDS = SPEC_ROOT / "scripts" / "scaffolds"
SCHEMA_BASE_URL = "https://ucp.dev/schemas/"

_ANNOTATION = re.compile(r"<!--\s*ucp:example\s+(?P<attrs>.*?)\s*-->")
_ATTR = re.compile(r'(\w+)=("[^"]*"|\S+)')


@cache
def registry() -> Registry[Any]:
    resources: list[tuple[str, Resource[Any]]] = []
    for path in SCHEMA_ROOT.rglob("*.json"):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


def validator(schema: str, definition: str | None = None) -> Draft202012Validator:
    """A validator for `schema` (a path like "shopping/catalog_search"), or one of its `$defs`."""
    ref = f"{SCHEMA_BASE_URL}{schema}.json"
    if definition is not None:
        ref += f"#/$defs/{definition}"
    # format_checker makes `format: uri` (and friends) real checks rather than annotations.
    return Draft202012Validator({"$ref": ref}, registry=registry(), format_checker=FormatChecker())


def schema_errors(payload: Any, schema: str, definition: str | None = None) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
        for e in validator(schema, definition).iter_errors(payload)
    ]


@dataclass(frozen=True)
class SpecExample:
    source: str
    schema: str
    op: str
    direction: str
    payload: Any

    @property
    def label(self) -> str:
        return f"{self.source} {self.schema} {self.op}/{self.direction}"


_HTTP_START = re.compile(r"^\s*(?:(?:GET|POST|PUT|PATCH|DELETE) \S+ HTTP/|HTTP/\d)")


def _unwrap_http(text: str) -> str:
    """REST examples show a request or status line and headers; the JSON body follows a blank line."""
    if not _HTTP_START.match(text):
        return text
    parts = re.split(r"\n[ \t]*\n", text, maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _canonical(block: str) -> str | None:
    """Reduce a doc JSON block to strict JSON, or None when it elides content."""
    text = _unwrap_http(block.replace("{{ ucp_version }}", UCP_VERSION))
    lines = [re.sub(r'^((?:[^"/]|"(?:\\.|[^"\\])*")*)//.*$', r"\1", line) for line in text.splitlines()]
    text = "\n".join(lines)
    if re.search(r'(?<!")\.\.\.(?!")|"\.\.\."', text):
        return None
    return text


def _extract(payload: Any, path: str | None) -> Any:
    if not path:
        return payload
    node = payload
    for key in path.removeprefix("$.").split("."):
        node = node[key]
    return node


def catalog_doc_examples() -> Iterator[SpecExample]:
    """Every complete, annotated JSON example in the catalog spec pages."""
    for doc in sorted(CATALOG_DOCS.glob("*.md")):
        pending: dict[str, str] | None = None
        in_json = False
        block: list[str] = []
        for line in doc.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if in_json:
                if stripped.startswith("```"):
                    in_json = False
                    if pending is not None and "skip" not in pending:
                        text = _canonical("\n".join(block))
                        if text is not None:
                            yield SpecExample(
                                source=doc.name,
                                schema=pending["schema"],
                                op=pending.get("op", "read"),
                                direction=pending.get("direction", "response"),
                                payload=_extract(json.loads(text), pending.get("extract")),
                            )
                    pending = None
                else:
                    block.append(line)
                continue
            if match := _ANNOTATION.search(stripped):
                pending = {k: v.strip('"') for k, v in _ATTR.findall(match["attrs"])}
                if match["attrs"].strip().startswith("skip"):
                    pending["skip"] = "1"
            elif stripped.startswith("```json"):
                in_json, block = True, []
            elif stripped:
                pending = None


def scaffold(name: str) -> Any:
    text = (SCAFFOLDS / f"{name}.json").read_text(encoding="utf-8")
    return json.loads(text.replace("2026-01-01", UCP_VERSION))


def authority_table() -> list[tuple[str, str, bool]]:
    """The examples table in the overview's "Derivation algorithm": (name, schema host, accepted)."""
    rows: list[tuple[str, str, bool]] = []
    in_table = False
    for line in OVERVIEW.read_text(encoding="utf-8").splitlines():
        if line.startswith("| Entity name") and "`schema` host" in line:
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
            if set(cells[0]) <= {"-", " "}:
                continue
            name, host, _authority, result = cells
            rows.append((name, host, result.startswith("**accept")))
    return rows
