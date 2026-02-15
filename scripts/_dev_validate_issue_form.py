from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ALLOWED_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}


def _err(errors: list[str], msg: str) -> None:
    errors.append(msg)


def validate_issue_form(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")

    # GitHub can be picky about stray control chars.
    for i, ch in enumerate(raw):
        o = ord(ch)
        if o < 0x09 or (0x0E <= o < 0x20):
            line = raw.count("\n", 0, i) + 1
            col = i - raw.rfind("\n", 0, i)
            return [f"{path}: control character U+{o:04X} at line {line}, col {col}"]

    try:
        doc = yaml.safe_load(raw)
    except Exception as e:
        return [f"{path}: YAML parse error: {e}"]

    errors: list[str] = []

    if not isinstance(doc, dict):
        return [f"{path}: top-level must be a mapping/object"]

    for key in ["name", "description", "body"]:
        if key not in doc:
            _err(errors, f"{path}: missing top-level key '{key}'")

    for key in ["name", "description"]:
        val = doc.get(key)
        if not isinstance(val, str) or not val.strip():
            _err(errors, f"{path}: top-level '{key}' must be a non-empty string")

    title = doc.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        _err(errors, f"{path}: top-level 'title' must be a non-empty string when present")

    labels = doc.get("labels")
    if labels is not None and not isinstance(labels, (list, str)):
        _err(errors, f"{path}: 'labels' must be a list or a string")

    body = doc.get("body")
    if not isinstance(body, list):
        _err(errors, f"{path}: 'body' must be a list")
        return errors

    ids_seen: set[str] = set()

    for idx, item in enumerate(body):
        loc = f"{path}: body[{idx}]"
        if not isinstance(item, dict):
            _err(errors, f"{loc}: must be an object")
            continue

        t = item.get("type")
        if not isinstance(t, str) or not t:
            _err(errors, f"{loc}: missing/invalid 'type'")
            continue

        if t not in ALLOWED_TYPES:
            _err(errors, f"{loc}: invalid type '{t}' (allowed: {sorted(ALLOWED_TYPES)})")

        if t != "markdown":
            issue_id = item.get("id")
            if not isinstance(issue_id, str) or not issue_id:
                _err(errors, f"{loc}: missing/invalid 'id' for type '{t}'")
            else:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", issue_id):
                    _err(errors, f"{loc}: id '{issue_id}' contains invalid characters")
                if issue_id in ids_seen:
                    _err(errors, f"{loc}: duplicate id '{issue_id}'")
                ids_seen.add(issue_id)

        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            _err(errors, f"{loc}: missing/invalid 'attributes'")
            continue

        if t == "markdown":
            v = attrs.get("value")
            if not isinstance(v, str):
                _err(errors, f"{loc}: markdown requires attributes.value (string)")
            if "label" in attrs:
                _err(errors, f"{loc}: markdown must not include attributes.label")
        else:
            if not isinstance(attrs.get("label"), str) or not attrs.get("label"):
                _err(errors, f"{loc}: type '{t}' requires attributes.label")

            desc = attrs.get("description")
            if desc is not None and not isinstance(desc, str):
                _err(errors, f"{loc}: attributes.description must be a string when present")

            placeholder = attrs.get("placeholder")
            if placeholder is not None and not isinstance(placeholder, str):
                _err(errors, f"{loc}: attributes.placeholder must be a string when present")

        if t == "dropdown":
            opts = attrs.get("options")
            if not isinstance(opts, list) or not opts:
                _err(errors, f"{loc}: dropdown requires attributes.options (non-empty list)")
            elif not all(isinstance(o, str) and o.strip() for o in opts):
                _err(errors, f"{loc}: dropdown options must be non-empty strings")
        if t == "checkboxes":
            opts = attrs.get("options")
            if not isinstance(opts, list) or not opts:
                _err(errors, f"{loc}: checkboxes requires attributes.options (non-empty list)")
            else:
                for oi, opt in enumerate(opts):
                    if not isinstance(opt, dict) or not isinstance(opt.get("label"), str) or not opt.get("label"):
                        _err(errors, f"{loc}: checkboxes option[{oi}] requires label")
                        continue
                    if "required" in opt and not isinstance(opt["required"], bool):
                        _err(errors, f"{loc}: checkboxes option[{oi}].required must be boolean")

        if "validations" in item:
            val = item.get("validations")
            if not isinstance(val, dict):
                _err(errors, f"{loc}: validations must be an object")
            else:
                if "required" in val and not isinstance(val["required"], bool):
                    _err(errors, f"{loc}: validations.required must be boolean")

        # Extra strictness: markdown items should NOT have validations.
        if t == "markdown" and "validations" in item:
            _err(errors, f"{loc}: markdown must not include validations")

    # Title placeholders should reference existing ids when present.
    if isinstance(title, str):
        for placeholder in re.findall(r"\{([A-Za-z0-9_\-]+)\}", title):
            if placeholder not in ids_seen:
                _err(errors, f"{path}: title placeholder '{{{placeholder}}}' does not match any field id")

    return errors


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print("usage: python scripts/_dev_validate_issue_form.py <issue_form.yml> [more...]")
        return 2

    all_errors: list[str] = []
    for p in paths:
        all_errors.extend(validate_issue_form(p))

    if all_errors:
        print("ISSUE_FORM_VALIDATION_ERRORS")
        for e in all_errors:
            print("-", e)
        return 1

    print("OK: issue form(s) look structurally valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
