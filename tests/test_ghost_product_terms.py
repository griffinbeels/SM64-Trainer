"""Backend messages and native labels use Ghost; legacy protocol keys remain."""
import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "src/sm64_events"
OLD_TERM = re.compile(r"\brivals?\b", re.IGNORECASE)


def product_terms(source):
    tree = ast.parse(source)
    # Standalone string expressions include module/member documentation and
    # historical user quotations; none is sent to a product surface.
    documentation = {id(node.value) for node in ast.walk(tree)
                     if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in documentation
            and node.value != "rivals" and not node.value.startswith("/") and OLD_TERM.search(node.value)]


def test_backend_ghost_messages_and_native_fallback_labels_do_not_reintroduce_old_product_term():
    paths = [*sorted((ROOT / "ghost").glob("*.py")), *sorted((ROOT / "server").glob("ghost*.py"))]
    violations = [(str(path.relative_to(ROOT)), line, message) for path in paths
                  for line, message in product_terms(path.read_text(encoding="utf-8"))]
    assert not violations, violations


def test_product_inventory_catches_old_messages_and_native_labels_but_preserves_protocol_and_history():
    assert product_terms('raise ValueError("Choose a rival.")') == [(1, "Choose a rival.")]
    assert product_terms('label = f"RIVAL {number}"') == [(1, "RIVAL ")]
    assert not product_terms('"""Historical rivals vocabulary."""\nkey = "rivals"\nurl = "/ghost/rivals/search"')
