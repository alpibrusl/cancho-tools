#!/usr/bin/env python3
"""Write schemas/<tool>.v1.json, one JSON Schema (Draft 2020-12) per tool.

The schemas are generated so that the parts every tool shares -- the
envelope, the error object, the repair kinds, `text_or_bytes` -- are written
once, here, and each tool's file is self-contained (it is embedded in the
tool's binary by scripts/manifest.py and printed by `introspect`).

Every object is `additionalProperties: false` except an error's `detail`,
which is rule-specific data (cancho docs/agent-toolbox.md D3, D5).

    python3 scripts/schemas.py           # write
    python3 scripts/schemas.py --check   # exit 1 if a committed file differs
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIALECT = "https://json-schema.org/draft/2020-12/schema"

NAT = {"type": "integer", "minimum": 0}
HEX64 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
CODES = ["GENERAL_ERROR", "INVALID_ARGS", "NOT_FOUND", "PERMISSION_DENIED",
         "CONFLICT", "PRECONDITION_FAILED"]


def obj(props, required=None):
    return {
        "type": "object",
        "properties": props,
        "required": list(props) if required is None else required,
        "additionalProperties": False,
    }


def common_defs():
    return {
        "text_or_bytes": {
            "description": "UTF-8 text as a string; other bytes as base64 (RFC 4648 section 4, padded).",
            "oneOf": [
                {"type": "string"},
                obj({"b64": {"type": "string", "pattern": "^[A-Za-z0-9+/]*={0,2}$"}}),
            ],
        },
        "repair": {
            "oneOf": [
                {"type": "null"},
                obj({"kind": {"const": "retry"},
                     "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}}),
                obj({"kind": {"const": "none"}, "reason": {"type": "string"}}),
                obj({"kind": {"const": "choose"},
                     "options": {"type": "array", "items": obj(
                         {"argv": {"type": "array", "items": {"type": "string"}}})}}),
            ]
        },
        "error": obj({
            "code": {"enum": CODES},
            "rule": {"type": "string", "pattern": "^[a-z]+\\.[a-z0-9-]+$"},
            "message": {"type": "string"},
            "hint": {"type": ["string", "null"]},
            "repair": {"$ref": "#/$defs/repair"},
            "detail": {"type": "object"},
        }),
        "meta": obj({"version": {"type": "string"}}),
    }


def document(tool, data, extra=None):
    """An envelope: `data` and/or `error`, never `meta.duration_ms`."""
    props = {
        "ok": {"type": "boolean"},
        "command": {"const": tool},
        "schema": {"const": tool + ".v1"},
        "data": {"$ref": "#/$defs/data"},
    }
    props.update(extra or {})
    props.update({
        "error": {"$ref": "#/$defs/error"},
        "errors": {"type": "array", "items": {"$ref": "#/$defs/error"}, "minItems": 1},
        "meta": {"$ref": "#/$defs/meta"},
    })
    defs = common_defs()
    defs["data"] = data
    return {
        "$schema": DIALECT,
        "$id": "https://github.com/alpibrusl/cancho-tools/schemas/%s.v1.json" % tool,
        "title": "%s.v1" % tool,
        "description": "One JSON object on one line. ok is false exactly when errors is present; error is its first element.",
        "type": "object",
        "properties": props,
        "required": ["ok", "command", "schema", "meta"],
        "additionalProperties": False,
        "allOf": [
            {"if": {"properties": {"ok": {"const": True}}},
             "then": {"not": {"anyOf": [{"required": ["error"]}, {"required": ["errors"]}]}},
             "else": {"required": ["error", "errors"]}},
        ],
        "$defs": defs,
    }


def stream(tool, records, end_counts):
    """NDJSON: each line one of `records`, an error record, or the end record."""
    defs = common_defs()
    end = {
        "type": {"const": "end"},
        "ok": {"type": "boolean"},
        "command": {"const": tool},
        "schema": {"const": tool + ".v1"},
        "complete": {"type": "boolean"},
    }
    end.update(end_counts)
    defs["end"] = obj(end)
    defs["error_record"] = obj({"type": {"const": "error"}, "error": {"$ref": "#/$defs/error"}})
    one_of = [{"$ref": "#/$defs/" + name} for name in records] + [
        {"$ref": "#/$defs/error_record"}, {"$ref": "#/$defs/end"}]
    for name, schema in records.items():
        defs[name] = schema
    return {
        "$schema": DIALECT,
        "$id": "https://github.com/alpibrusl/cancho-tools/schemas/%s.v1.json" % tool,
        "title": "%s.v1" % tool,
        "description": "One line of an NDJSON stream. The last line of a stream that finished is the end record; a stream without one was cut short.",
        "oneOf": one_of,
        "$defs": defs,
    }


TB = {"$ref": "#/$defs/text_or_bytes"}

# `toolbox.diff`: at most one hunk, the lines between the common leading and
# trailing lines. `write` answers null when the old file was past --max-bytes.
HUNK = obj({
    "old_start": {"type": "integer", "minimum": 1}, "new_start": {"type": "integer", "minimum": 1},
    "removed_count": NAT, "added_count": NAT,
    "removed": {"type": "array", "items": TB}, "added": {"type": "array", "items": TB},
})
DIFF = {"type": "array", "items": HUNK, "maxItems": 1}
DIFFS = {"diff": DIFF, "diff_truncated": {"type": "boolean"}}
WRITE_DIFFS = {"diff": {"oneOf": [{"type": "null"}, DIFF]}, "diff_truncated": {"type": "boolean"}}

SCHEMAS = {
    "seek": stream("seek", {
        "match": obj({"type": {"const": "match"}, "path": TB, "line": NAT, "offset": NAT, "text": TB}),
        "file": obj({"type": {"const": "file"}, "path": TB, "matches": NAT, "bytes": NAT,
                     "binary": {"type": "boolean"}, "complete": {"type": "boolean"}}),
    }, {
        "files": NAT, "matches": NAT, "errors": NAT, "truncated": {"type": "boolean"},
        "next": {"oneOf": [{"type": "null"}, obj({"skip": NAT})]},
    }),
    "hash": stream("hash", {
        "hash": obj({"type": {"const": "hash"}, "path": TB, "algo": {"enum": ["sha256", "sha512"]},
                     "hex": {"type": "string", "pattern": "^([0-9a-f]{64}|[0-9a-f]{128})$"},
                     "bytes": NAT}),
    }, {"files": NAT, "errors": NAT}),
    "list": stream("list", {
        "entry": obj({"type": {"const": "entry"}, "path": TB,
                      "kind": {"enum": ["file", "directory", "link", "other", "unknown"]},
                      "depth": {"type": "integer", "minimum": 1},
                      "size": NAT, "mtime": {"type": "integer"}},
                     required=["type", "path", "kind", "depth"]),
    }, {
        "entries": NAT, "errors": NAT, "truncated": {"type": "boolean"},
        "next": {"oneOf": [{"type": "null"}, obj({"skip": NAT})]},
    }),
    "write": document("write", obj({
        "path": TB, "changed": {"type": "boolean"}, "created": {"type": "boolean"}, "bytes": NAT,
        "before_sha256": {"oneOf": [{"type": "null"}, HEX64]}, "after_sha256": HEX64,
        **WRITE_DIFFS,
    }), {
        "dry_run": {"const": True},
        "planned_actions": {"type": "array", "items": obj({
            "op": {"enum": ["create", "replace"]}, "path": TB,
            "before_sha256": {"oneOf": [{"type": "null"}, HEX64]}, "after_sha256": HEX64, "bytes": NAT,
            **WRITE_DIFFS,
        })},
    }),
    "move": document("move", obj({
        "path": TB, "to": TB, "changed": {"type": "boolean"},
        "kind": {"enum": ["file", "directory", "link", "other"]},
        "sha256": {"oneOf": [{"type": "null"}, HEX64]},
    }), {
        "dry_run": {"const": True},
        "planned_actions": {"type": "array", "items": obj({
            "op": {"enum": ["move", "remove"]}, "path": TB, "to": TB,
            "kind": {"enum": ["file", "directory", "link", "other"]},
            "sha256": {"oneOf": [{"type": "null"}, HEX64]},
        })},
    }),
    "replace": document("replace", obj({
        "path": TB, "changed": {"type": "boolean"}, "replacements": NAT, "bytes": NAT,
        "before_sha256": HEX64, "after_sha256": HEX64,
        **DIFFS,
    }), {
        "dry_run": {"const": True},
        "planned_actions": {"type": "array", "items": obj({
            "op": {"const": "replace"}, "path": TB, "replacements": NAT,
            "before_sha256": HEX64, "after_sha256": HEX64, "bytes": NAT,
            **DIFFS,
        })},
    }),
    "peek": document("peek", {
        "oneOf": [
            obj({
                "path": TB, "size": NAT, "binary": {"type": "boolean"}, "mode": {"const": "lines"},
                "from": NAT, "to": NAT,
                "lines": {"type": "array", "items": {"oneOf": [
                    obj({"n": NAT, "offset": NAT, "text": TB}),
                    obj({"n": NAT, "offset": NAT, "text": {"type": "null"}, "length": NAT}),
                ]}},
                "eof": {"type": "boolean"}, "truncated": {"type": "boolean"},
                "next": {"oneOf": [{"type": "null"}, obj({"line": NAT})]},
                "total_lines": {"oneOf": [{"type": "null"}, NAT]},
            }),
            obj({
                "path": TB, "size": NAT, "binary": {"type": "boolean"}, "mode": {"const": "bytes"},
                "from": NAT, "to": NAT, "text": TB,
                "eof": {"type": "boolean"}, "truncated": {"type": "boolean"},
                "next": {"oneOf": [{"type": "null"}, obj({"byte": NAT})]},
                "total_lines": {"type": "null"},
            }),
        ]
    }),
    "jsonq": document("jsonq", {
        "oneOf": [
            obj({"pointer": TB, "exists": {"type": "boolean"}}),
            obj({"pointer": TB, "kind": {"enum": ["null", "boolean", "integer", "float", "string", "array", "object"]}}),
            obj({"pointer": TB, "kind": {"enum": ["null", "boolean", "integer", "float", "string", "array", "object"]},
                 "value": {"description": "the value as the document wrote it"}}),
            obj({"pointer": TB, "kind": {"const": "object"}, "keys": {"type": "array", "items": {"type": "string"}}}),
            obj({"pointer": TB, "kind": {"enum": ["string", "array", "object"]}, "length": NAT}),
        ]
    }),
    "tally": document("tally", obj({
        "total": NAT, "distinct": NAT, "skipped": NAT,
        "top": {"type": "array", "items": obj({"key": TB, "count": {"type": "integer", "minimum": 1}})},
        "truncated": {"type": "boolean"},
    })),
}


def render(schema):
    return json.dumps(schema, indent=2, sort_keys=False) + "\n"


def main():
    check = "--check" in sys.argv[1:]
    out = ROOT / "schemas"
    out.mkdir(exist_ok=True)
    stale = []
    for tool, schema in SCHEMAS.items():
        path = out / ("%s.v1.json" % tool)
        text = render(schema)
        if check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(text)
    if stale:
        print("stale schemas (run scripts/schemas.py): " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
