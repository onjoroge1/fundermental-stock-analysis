"""The research app stays information only: no module here can call or define an IBKR order method, import ibapi's
order types, or import the owner's private execution desk (stock-machine-desk). Checked on the syntax tree, since
docstrings may name the methods (market_data/ibkr_tws.py does)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER_METHODS = {"placeOrder", "cancelOrder", "reqGlobalCancel", "exerciseOptions"}
FORBIDDEN_IMPORTS = ("ibapi.order", "ibapi.order_state", "desk", "stock_machine_desk")


def violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    rel = path.relative_to(ROOT).as_posix()
    found = []
    for node in ast.walk(tree):
        name = (node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name)
                else node.name if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else None)
        if name in ORDER_METHODS:
            found.append(f"{rel}:{node.lineno}: {name}")
        modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                   else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
        for module in modules:
            if any(module == m or module.startswith(m + ".") for m in FORBIDDEN_IMPORTS):
                found.append(f"{rel}:{node.lineno}: imports {module}")
    return found


def python_files():
    for folder in ("stock_machine", "scripts"):
        yield from sorted(p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_can_place_an_order_or_reach_the_execution_desk():
    found = [v for path in python_files() for v in violations(path)]
    assert found == []


def test_the_check_catches_a_planted_order_call(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    cases = {"a.py": "def go(app, c, o):\n    app.placeOrder(1, c, o)\n", "b.py": "from ibapi.order import Order\n",
             "c.py": "import desk.engine\n", "d.py": "def cancelOrder(self, i):\n    pass\n"}
    for name, code in cases.items():
        (tmp_path / name).write_text(code)
        assert violations(tmp_path / name), name
    (tmp_path / "ok.py").write_text('"""Never imports or calls placeOrder."""\n')
    assert violations(tmp_path / "ok.py") == []
