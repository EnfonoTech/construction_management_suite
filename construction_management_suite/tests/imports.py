"""Check every frappe and erpnext symbol this app uses exists on THIS bench.

A controller that imports a private frappe helper works until the bench it runs
on is a different patch release. `frappe.model.delete_doc.get_linked_docs` is
absent in frappe 15.92 and present in 15.114, so `Rate Analysis` opened fine in
development and raised ImportError in the browser on the server — the kind of
break that reaches a user before it reaches a test.

Run it on any bench before trusting a deploy:

    bench --site <site> execute construction_management_suite.tests.imports.run

It parses rather than executes, so a module with a side effect is not run; and
it reports every failure instead of stopping at the first.

Two kinds of use are checked, because both have broken here before:

* `from frappe.x import y` — an import fails loudly, at the moment the module
  is first loaded, which may be the moment a user opens a form;
* `frappe.db.some_method(...)` — an attribute fails only when that line runs,
  which can be months later and on somebody else's screen.

This app is meant to run on frappe 15 generally, not only on the newest one.
That means preferring documented, long-standing API over whatever the local
bench happens to have, and running this before trusting a deploy.
"""

import ast
import importlib
import io
import os

import frappe


def _app_root():
    return frappe.get_app_path("construction_management_suite")


def _dotted(node):
    """`frappe.db.sql_ddl` out of the attribute chain, or None if it is not one."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


_SEEN = {}


def _resolve(dotted):
    """Walk the chain on the live modules. Returns the problem, or None."""
    if dotted in _SEEN:
        return _SEEN[dotted]

    root, *rest = dotted.split(".")
    try:
        obj = importlib.import_module(root)
    except Exception as e:
        _SEEN[dotted] = f"no module {root} ({type(e).__name__})"
        return _SEEN[dotted]

    walked = root
    for part in rest:
        if not hasattr(obj, part):
            # A submodule is not an attribute until something imports it.
            try:
                obj = importlib.import_module(f"{walked}.{part}")
            except Exception:
                _SEEN[dotted] = f"{walked} has no {part}   (from {dotted})"
                return _SEEN[dotted]
        else:
            obj = getattr(obj, part)
        walked = f"{walked}.{part}"

    _SEEN[dotted] = None
    return None


def _assigned_names(trees):
    """Dotted names the app sets on frappe itself.

    `frappe.local` and `frappe.flags` are request-scoped scratch space that any
    app may hang its own attributes on — this one caches a lookup at
    `frappe.local._cms_stand_in`. Those are not frappe API and must not be
    reported as missing from it; what marks them out is that the app assigns
    them, so that is what is looked for rather than a list of namespaces to
    trust blindly.
    """
    assigned = set()
    for tree in trees:
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    dotted = _dotted(target)
                    if dotted:
                        assigned.add(dotted)
    return assigned


def run():
    root = _app_root()
    checked = 0
    missing = []

    parsed = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                parsed.append((path, ast.parse(io.open(path, encoding="utf-8").read())))
            except SyntaxError as e:
                missing.append(f"{os.path.relpath(path, root)}: will not parse — {e}")

    ours = _assigned_names(t for _, t in parsed)

    for path, tree in parsed:
        rel = os.path.relpath(path, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    node.module.split(".")[0] in ("frappe", "erpnext"):
                checked += 1
                try:
                    module = importlib.import_module(node.module)
                except Exception as e:
                    missing.append(f"{rel}: no module {node.module} ({type(e).__name__})")
                    continue
                for alias in node.names:
                    if alias.name != "*" and not hasattr(module, alias.name):
                        missing.append(f"{rel}: {node.module} has no {alias.name}")

            elif isinstance(node, ast.Attribute):
                dotted = _dotted(node)
                if not dotted or dotted.split(".")[0] not in ("frappe", "erpnext"):
                    continue
                if dotted in ours:
                    continue
                checked += 1
                problem = _resolve(dotted)
                if problem:
                    missing.append(f"{rel}: {problem}")

    version = frappe.get_attr("frappe.__version__")
    erp = frappe.get_attr("erpnext.__version__")
    print(f"frappe {version}, erpnext {erp}")
    print(f"{checked} frappe/erpnext imports and attribute calls checked")
    if missing:
        print(f"\n{len(missing)} WILL BREAK ON THIS BENCH:")
        for line in missing:
            print("   " + line)
    else:
        print("every symbol resolves")
    return len(missing)
