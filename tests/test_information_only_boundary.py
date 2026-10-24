"""The research app stays information only. Checked on the syntax tree, so docstrings may name the methods
(market_data/ibkr_tws.py does), over stock_machine/, scripts/, migrations/ and tests/:
- no call, definition, imported name or raw-message name of an IBKR socket-API order method;
- no import of ibapi's order types, or of the owner's private execution desk (stock-machine-desk), including through
  import_module or __import__ with a literal name;
- outside tests/ (whose tests assert these routes are refused), no string that names an order method or an IBKR
  Client Portal order path, the REST route by which an order would be placed, modified, cancelled or confirmed;
- no dependency on the desk or on third-party IBKR order libraries in pyproject.toml.
Dynamic tricks (a computed attribute name, a string assembled at run time) are left to review."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("stock_machine", "scripts", "migrations", "tests")
ORDER_METHODS = {"placeOrder", "cancelOrder", "reqGlobalCancel", "exerciseOptions",
                 "PLACE_ORDER", "CANCEL_ORDER", "REQ_GLOBAL_CANCEL", "EXERCISE_OPTIONS"}
FORBIDDEN_IMPORTS = ("ibapi.order", "ibapi.order_state", "ibapi.order_cancel", "ibapi.order_condition",
                     "desk", "stock_machine_desk", "ib_insync", "ib_async")
ORDER_PATH = re.compile(r"/iserver/(account/\S*orders?\b|reply\b)|/orders?(/|$|\?)")
# files that must be in the scan, so a moved folder cannot leave the check passing on nothing
MUST_SCAN = ("stock_machine/market_data/ibkr_tws.py", "stock_machine/market_data/ibkr_client_portal.py",
             "stock_machine/market_data/remote_bridge.py", "stock_machine/ibkr_bridge.py",
             "stock_machine/mcp_server/server.py", "stock_machine/ingestion/prices_tws.py")


def _forbidden(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in FORBIDDEN_IMPORTS)


def violations(path: Path, root: Path) -> list[str]:
    tree = ast.parse(path.read_bytes(), filename=str(path))
    rel = path.relative_to(root).as_posix()
    strings = not rel.startswith("tests/")
    found = []
    for node in ast.walk(tree):
        names, modules = [], []
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
        elif isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            names += [a.name for a in node.names]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if func in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                modules.append(node.args[0].value)
        elif strings and isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in ORDER_METHODS:
                found.append(f"{rel}:{node.lineno}: the string {node.value!r} names an order method")
            if ORDER_PATH.search(node.value):
                found.append(f"{rel}:{node.lineno}: the string {node.value!r} is an IBKR order path")
        found += [f"{rel}:{node.lineno}: order method {n}" for n in names if n in ORDER_METHODS]
        found += [f"{rel}:{node.lineno}: imports {m}" for m in modules if _forbidden(m)]
    return found


def python_files(root: Path):
    for folder in FOLDERS:
        yield from sorted(p for p in (root / folder).rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_can_place_an_order_or_reach_the_execution_desk():
    files = list(python_files(ROOT))
    scanned = {p.relative_to(ROOT).as_posix() for p in files}
    assert set(MUST_SCAN) <= scanned and len(scanned) > 200          # coverage cannot quietly drop to nothing
    found = [v for path in files for v in violations(path, ROOT)]
    assert found == []


def test_no_dependency_on_the_desk_or_an_ibkr_order_library():
    text = (ROOT / "pyproject.toml").read_text().lower()
    assert not any(name in text for name in ("stock-machine-desk", "stock_machine_desk", "ib_insync", "ib-insync",
                                             "ib_async", "ib-async"))


PLANTED = {
    "call": ("stock_machine/a.py", "def go(app, c, o):\n    app.placeOrder(1, c, o)\n", "order method placeOrder"),
    "definition": ("stock_machine/a.py", "def cancelOrder(self, i):\n    pass\n", "order method cancelOrder"),
    "async definition": ("stock_machine/a.py", "async def reqGlobalCancel(self):\n    pass\n",
                         "order method reqGlobalCancel"),
    "imported name": ("stock_machine/a.py", "from somewhere import exerciseOptions\n", "order method exerciseOptions"),
    "raw message": ("stock_machine/a.py", "from x import OUT\nOUT.PLACE_ORDER\n", "order method PLACE_ORDER"),
    "order module": ("stock_machine/a.py", "from ibapi.order import Order\n", "imports ibapi.order"),
    "order module, from form": ("stock_machine/a.py", "from ibapi import order\n", "imports ibapi.order"),
    "order condition": ("stock_machine/a.py", "import ibapi.order_condition\n", "imports ibapi.order_condition"),
    "desk": ("scripts/a.py", "import desk.engine\n", "imports desk.engine"),
    "desk, dynamic": ("scripts/a.py", "from importlib import import_module\nimport_module('desk.engine')\n",
                      "imports desk.engine"),
    "desk, __import__": ("scripts/a.py", "__import__('desk')\n", "imports desk"),
    "ib_insync": ("stock_machine/a.py", "from ib_insync import IB\n", "imports ib_insync"),
    "string name": ("stock_machine/a.py", "def go(app):\n    getattr(app, 'placeOrder')\n", "names an order method"),
    "REST place": ("stock_machine/a.py", "def go(c, a):\n    c._json('POST', f'/iserver/account/{a}/orders')\n",
                   "is an IBKR order path"),
    "REST cancel": ("stock_machine/a.py", "def go(c, a, o):\n    c._json('DELETE', f'/iserver/account/{a}/order/{o}')\n",
                    "is an IBKR order path"),
    "REST confirm": ("stock_machine/a.py", "def go(c, r):\n    c._json('POST', f'/iserver/reply/{r}')\n",
                     "is an IBKR order path"),
    "in migrations": ("migrations/a.py", "from ibapi.order import Order\n", "migrations/a.py:1: imports ibapi.order"),
    "in tests": ("tests/a.py", "def go(app, c, o):\n    app.placeOrder(1, c, o)\n", "tests/a.py:2: order method"),
}


def test_each_rule_catches_its_own_planted_violation(tmp_path):
    for case, (rel, code, expected) in PLANTED.items():
        root = tmp_path / case.replace(" ", "_").replace(",", "")
        path = root / rel
        path.parent.mkdir(parents=True)
        path.write_text(code)
        found = [v for p in python_files(root) for v in violations(p, root)]
        assert any(expected in f for f in found), (case, found)


def test_words_in_docstrings_read_only_paths_and_test_assertions_pass(tmp_path):
    ok = {"stock_machine/ok.py": '"""Never imports or calls placeOrder."""\nPATHS = ["/iserver/accounts", '
                                 '"/iserver/marketdata/snapshot", "/iserver/secdef/search"]\n',
          "tests/test_ok.py": "def test_refused(client):\n    assert client.post('/v1/orders').status_code == 404\n"
                              "    assert not hasattr(client, 'placeOrder')\n"}
    for rel, code in ok.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(code)
    assert [v for p in python_files(tmp_path) for v in violations(p, tmp_path)] == []
