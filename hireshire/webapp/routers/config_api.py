"""Config view/edit endpoints.

GET returns the current values of the whitelisted fields for a phase plus a
field-doc map. PUT validates the patch against the pipeline's own pydantic
models and writes it back with ruamel.yaml so the extensive inline comments in
config/*.yaml survive untouched. Any key outside the whitelist is rejected.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from ruamel.yaml import YAML

from hireshire.webapp.config_spec import PHASE_SPECS, field_docs
from hireshire.webapp.models import ConfigPatch, ConfigResponse

router = APIRouter(prefix="/api/config", tags=["config"])

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096  # don't wrap long comment lines
# Match the config/*.yaml style: list items indented 4 with the dash at 2, so a
# one-key edit doesn't reflow every sequence line.
_yaml.indent(mapping=2, sequence=4, offset=2)


def _load_doc(path: Path):
    return _yaml.load(path.read_text(encoding="utf-8"))


def _dump_doc(doc, path: Path) -> None:
    buf = io.StringIO()
    _yaml.dump(doc, buf)
    text = buf.getvalue()
    # ruamel always emits LF; preserve the file's original newline style (the
    # config/*.yaml files are CRLF on Windows) so a one-key edit produces a
    # one-line diff instead of rewriting every line.
    if b"\r\n" in path.read_bytes():
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))


def _get_path(doc, path: tuple[str, ...]) -> Any:
    node = doc
    for key in path:
        if node is None or key not in node:
            return None
        node = node[key]
    return node


def _set_path(doc, path: tuple[str, ...], value: Any) -> None:
    node = doc
    for key in path[:-1]:
        if key not in node or node[key] is None:
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


def _plain(value: Any) -> Any:
    """Coerce ruamel scalar/collection wrappers into JSON-native types."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if value is None:
        return None
    return str(value)


@router.get("/{phase}", response_model=ConfigResponse)
def get_config(phase: str) -> ConfigResponse:
    if phase not in PHASE_SPECS:
        raise HTTPException(404, f"Unknown config phase '{phase}'.")
    spec = PHASE_SPECS[phase]
    doc = _load_doc(Path(spec.file))
    values = {name: _plain(_get_path(doc, fs.path)) for name, fs in spec.fields.items()}
    types = {name: fs.type for name, fs in spec.fields.items()}
    options = {name: fs.options for name, fs in spec.fields.items() if fs.options}
    return ConfigResponse(
        phase=phase, values=values, docs=field_docs(phase),
        types=types, options=options,
    )


@router.put("/{phase}")
def put_config(phase: str, patch: ConfigPatch) -> dict:
    if phase not in PHASE_SPECS:
        raise HTTPException(404, f"Unknown config phase '{phase}'.")
    spec = PHASE_SPECS[phase]

    unknown = [k for k in patch.values if k not in spec.fields]
    if unknown:
        raise HTTPException(400, f"Fields not editable from the dashboard: {unknown}")

    path = Path(spec.file)
    doc = _load_doc(path)

    # Apply the patch to an in-memory copy, then validate the whole file's model.
    for key, value in patch.values.items():
        _set_path(doc, spec.fields[key].path, value)

    plain = _plain(doc)
    try:
        spec.validate(plain)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors())

    _dump_doc(doc, path)
    values = {name: _plain(_get_path(doc, fs.path)) for name, fs in spec.fields.items()}
    return {"phase": phase, "saved": list(patch.values), "values": values}
