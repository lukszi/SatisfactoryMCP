"""The web routers: one module per concern, each mounted once, each saying what it sends.

The reasons are in docs/DEVELOPING.md, "Architecture rules".
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from tests.support.import_graph import (
    REPO,
    WEB,
    WEB_ROUTERS,
    covers,
    import_nodes,
    module_name,
    package_of,
    parse,
    root_of,
    sources,
    targets,
)

WEB_APP_PY = WEB / "app.py"

#: What a router module may import beyond the standard library; ``typing_extensions`` for the
#: ``TypedDict`` pydantic accepts on every supported Python.
ROUTER_ALLOWED_ROOTS = frozenset({"fastapi", "starlette", "typing_extensions"})

#: The two packages a relative import traverses, allowed exactly and never as a prefix, so
#: ``from .. import app`` still fails on its second edge.
ROUTER_ALLOWED_EXACT = frozenset({"satisfactory_mcp", "satisfactory_mcp.interfaces.web"})

ROUTER_ALLOWED_PREFIXES: tuple[str, ...] = (
    "satisfactory_mcp.config",
    "satisfactory_mcp.core",
    "satisfactory_mcp.domain",
    "satisfactory_mcp.interfaces.web.serial",
    "satisfactory_mcp.interfaces.web.terrain",
)

#: The one measured exception: the event stream imports the watcher's event names.
ROUTER_EXTRA_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "satisfactory_mcp.interfaces.web.routers.bridge.events",
            "satisfactory_mcp.interfaces.web.watch.events",
        )
    }
)

#: Annotations FastAPI treats as "this handler answers for itself", matched by terminal name.
RESPONSE_CLASSES = frozenset(
    {
        "Response",
        "JSONResponse",
        "StreamingResponse",
        "FileResponse",
        "HTMLResponse",
        "PlainTextResponse",
        "RedirectResponse",
        "ORJSONResponse",
        "UJSONResponse",
    }
)

#: GET handlers allowed to publish no response schema, by function name, with the reason.
RESPONSE_MODEL_EXEMPT: dict[str, str] = {}

WRITE_VERBS = frozenset({"post", "put", "patch", "delete"})


def _router_sources() -> list[Path]:
    """Every router module; ``__init__.py`` is the mount list, not a router."""
    return [path for path in sources(WEB_ROUTERS) if path.name != "__init__.py"]


def _all_routers_declared() -> list[str]:
    """The module names in ``ALL_ROUTERS``, in order, read off the AST (no FastAPI needed)."""
    for node in ast.walk(parse(WEB_ROUTERS / "__init__.py")):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not (isinstance(node.target, ast.Name) and node.target.id == "ALL_ROUTERS"):
            continue
        assert isinstance(node.value, ast.Tuple), "ALL_ROUTERS is no longer a literal tuple"
        return [
            element.value.id
            for element in node.value.elts
            if isinstance(element, ast.Attribute)
            and element.attr == "router"
            and isinstance(element.value, ast.Name)
        ]
    raise AssertionError("ALL_ROUTERS is gone from routers/__init__.py")


def _annotation_name(node: ast.expr | None) -> str | None:
    """The terminal name of a return annotation, spelled as a name, an attribute or a string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rsplit(".", 1)[-1]
    return None


def _answers_get(deco: ast.Call) -> bool:
    """Whether a ``router.<verb>(...)`` decorator registers a read or a write this rule walks."""
    verb = deco.func.attr  # type: ignore[attr-defined]
    if verb == "api_route":
        methods = next((k.value for k in deco.keywords if k.arg == "methods"), None)
        names = (
            {e.value for e in methods.elts if isinstance(e, ast.Constant)}
            if isinstance(methods, (ast.List, ast.Tuple))
            else set()
        )
        return "GET" in names
    return verb in WRITE_VERBS | {"get"}


def _routes() -> list[tuple[str, str, int, ast.Call, ast.expr | None]]:
    """``(module, function, lineno, decorator, return annotation)`` for every routed handler."""
    found: list[tuple[str, str, int, ast.Call, ast.expr | None]] = []
    for path in _router_sources():
        module = path.relative_to(WEB_ROUTERS).with_suffix("").as_posix()
        for node in ast.walk(parse(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
                    continue
                if not (isinstance(deco.func.value, ast.Name) and deco.func.value.id == "router"):
                    continue
                if _answers_get(deco):
                    found.append((module, node.name, node.lineno, deco, node.returns))
    return found


def _declares_model(deco: ast.Call) -> bool:
    return any(k.arg == "response_model" for k in deco.keywords)


def test_a_router_sees_the_domain_and_its_own_two_helpers_and_nothing_else():
    stray = []
    for path in _router_sources():
        name = module_name(path)
        for node, _in_function in import_nodes(parse(path)):
            for target in targets(node, package_of(path)):
                allowed = (
                    root_of(target) in sys.stdlib_module_names
                    or root_of(target) in ROUTER_ALLOWED_ROOTS
                    or target in ROUTER_ALLOWED_EXACT
                    or covers(target, ROUTER_ALLOWED_PREFIXES)
                    or (name, target) in ROUTER_EXTRA_EDGES
                )
                if not allowed:
                    stray.append(f"  {name}:{node.lineno} imports {target}")
    assert not stray, (
        "a router may import the standard library, fastapi/starlette, typing_extensions, "
        "config/core/domain "
        "and the web package's own serial and terrain -- never another router, never app, "
        "never a presenter. Whatever is shared belongs in the serial package:\n"
        + "\n".join(sorted(stray))
    )


def test_every_router_is_mounted_exactly_once_and_app_mounts_only_the_tuple():
    declared = _all_routers_declared()
    on_disk = sorted(path.stem for path in _router_sources())

    duplicated = sorted({name for name in declared if declared.count(name) > 1})
    assert not duplicated, (
        "these routers are in ALL_ROUTERS more than once -- every path they carry is "
        f"registered twice: {duplicated}"
    )
    assert sorted(declared) == on_disk, (
        "ALL_ROUTERS and routers/ have drifted; an unmounted module is a 404 that nothing "
        "reports:\n"
        f"  never mounted: {sorted(set(on_disk) - set(declared))}\n"
        f"  no such file:  {sorted(set(declared) - set(on_disk))}"
    )

    tree = parse(WEB_APP_PY)
    includes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
    ]
    assert len(includes) == 1, (
        "app.py includes routers somewhere other than the loop over ALL_ROUTERS -- the "
        f"mount order is the tuple's order or it is nobody's ({len(includes)} calls found)"
    )
    loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "ALL_ROUTERS"
    ]
    assert len(loops) == 1, "app.py no longer mounts the API by iterating ALL_ROUTERS"
    assert includes[0] in ast.walk(loops[0]), (
        "the one include_router in app.py is outside the ALL_ROUTERS loop"
    )


def test_the_one_file_api_stays_deleted():
    for candidate in (WEB / "api.py", WEB / "api"):
        assert not candidate.exists(), (
            "interfaces/web/api.py is back -- it was split into routers/ (one module per "
            "concern, mounted through ALL_ROUTERS) and serial/ (the shared vocabulary), "
            "and a handler that fits neither belongs in a router of its own"
        )
    assert not (REPO / "tests" / "web" / "test_api.py").exists(), (
        "tests/web/test_api.py is back -- the endpoint tests live in tests/web/<router "
        "package>/, one file per router, plus test_static.py for the mount at /"
    )


def test_every_get_says_what_it_sends():
    """Write verbs too: a write owes the page its reply type as much as a read does."""
    missing = [
        f"  routers/{module}.py:{lineno}  {name}()"
        for module, name, lineno, deco, returns in _routes()
        if not _declares_model(deco)
        and _annotation_name(returns) not in RESPONSE_CLASSES
        and name not in RESPONSE_MODEL_EXEMPT
    ]
    assert not missing, (
        "these GET handlers publish no response schema, so the page cannot be typed from the "
        "server and will type itself from observed payloads instead -- which is the file this "
        "ratchet exists to keep deleted. Declare a TypedDict in EMISSION order and pass it as "
        "response_model (routers/layers/floors.py writes the two rules out), or, if the handler "
        "returns a Response subclass, say so in its return annotation:\n" + "\n".join(missing)
    )


def test_no_stale_response_model_exemption():
    routes = _routes()
    named = {name for _module, name, _line, _deco, _returns in routes}
    unknown = sorted(set(RESPONSE_MODEL_EXEMPT) - named)
    assert not unknown, (
        "RESPONSE_MODEL_EXEMPT names GET handlers that no longer exist in routers/ -- a "
        f"renamed or deleted endpoint leaves its exemption behind: {unknown}"
    )
    declared = {name for _module, name, _line, deco, _returns in routes if _declares_model(deco)}
    fixed = sorted(declared & set(RESPONSE_MODEL_EXEMPT))
    assert not fixed, (
        "these handlers now declare a response_model and no longer need their exemption; "
        f"delete the entry from RESPONSE_MODEL_EXEMPT: {fixed}"
    )
