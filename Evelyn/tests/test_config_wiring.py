# test_config_wiring.py
# date created: 2026-09-06 18:46:08
# date modified: 2026-09-06 18:46:08
# tags:

"""
Deterministic AST verification test.
Asserts that every uppercase configuration constant in evelyn_config.py
is actively referenced in at least one engine or server module.
Fails immediately upon detecting unwired configuration drift.
"""

import ast
from pathlib import Path

import pytest

CONFIG_PATH = Path("evelyn_config.py")
SOURCE_DIRS = [Path("Evelyn"), Path("scripts")]
ENTRYPOINT_FILES = [Path("evelyn_server.py"), Path("evelyn_setup.py")]

# Whitelist of constants intended strictly for environment templates, future roadmap waves, or external inspection
CONFIG_WHITELIST = {
    # Private or internal definitions
    "_CATEGORY_LABELS",
    "CATEGORY_NAMES",

    # Environment templates or external bindings
    "ENV_FILE_PATH",
    "VERSION_INFO",
    "SERVER_HOST",
    "SQLITE_PRAGMAS",
    "CONTEXT_DIR",
    "VAULT_WRITE_IGNORE",
    "DEBUG_TOOL_FULL",
    "SHOW_TOOL_LOOP_THINKING",

    # Task queue & terminal options
    "TASK_QUEUE_STATE_FILE",
    "TERMINAL_DEFAULT_TIMEOUT",
    "TERMINAL_ENABLED",

    # Summarization parameters
    "SUMMARY_MAX_WORDS",
    "SUMMARY_OVERLAP",
    "SUMMARY_WINDOW_SIZE",

    # Deep Research parameters (Wave 3 / future scopes)
    "RESEARCH_CONFIDENCE_THRESHOLD",
    "RESEARCH_MAX_SUB_QUESTIONS",
    "RESEARCH_MODEL",

    # Tag Librarian (Wave 4 Roadmap)
    "TAG_LIBRARIAN_BATCH_SIZE",
    "TAG_LIBRARIAN_ENABLED",
    "TAG_LIBRARIAN_FORMAT_RULES",
    "TAG_LIBRARIAN_IDLE_THRESHOLD",
}


def extract_config_constants(config_path: Path) -> set[str]:
    """Parse evelyn_config.py and extract all top-level uppercase constant names."""
    if not config_path.exists():
        pytest.skip(f"{config_path} not found")

    with open(config_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(config_path))

    constants = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.isupper():
            constants.add(node.target.id)
    return constants - CONFIG_WHITELIST


class SymbolUsageVisitor(ast.NodeVisitor):
    """AST Visitor that captures Name, Attribute, and string Constant identifiers in a file."""

    def __init__(self):
        self.used_symbols = set()

    def visit_Name(self, node: ast.Name):
        self.used_symbols.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        self.used_symbols.add(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        # Captures getattr(cfg, "CONSTANT_NAME", default) patterns
        if isinstance(node.value, str):
            self.used_symbols.add(node.value)
        self.generic_visit(node)


def collect_used_symbols() -> set[str]:
    """Scan all Python files across the codebase and extract referenced symbols."""
    py_files = list(ENTRYPOINT_FILES)
    for s_dir in SOURCE_DIRS:
        if s_dir.exists():
            py_files.extend(s_dir.rglob("*.py"))

    all_used = set()
    for p in py_files:
        if not p.exists() or p == CONFIG_PATH or "tests" in p.parts:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(p))
            visitor = SymbolUsageVisitor()
            visitor.visit(tree)
            all_used.update(visitor.used_symbols)
        except SyntaxError:
            continue
    return all_used


def test_all_config_constants_are_wired():
    """Assert that every declared configuration constant has at least one consumer."""
    declared = extract_config_constants(CONFIG_PATH)
    used = collect_used_symbols()

    unwired = declared - used
    assert not unwired, (
        f"CRITICAL DRIFT: Configuration constants declared in evelyn_config.py "
        f"have ZERO consumers across the engine: {sorted(unwired)}. "
        f"Wire them into the runtime or add to CONFIG_WHITELIST."
    )


def test_critical_context_delivery_constants_wired():
    """Explicitly verify that critical context delivery constants are wired and consumed."""
    used = collect_used_symbols()
    assert "RAG_EXCLUDED_SUBDIRS" in used, "RAG_EXCLUDED_SUBDIRS must be actively consumed"
    assert "MAX_HISTORY_MESSAGES" in used, "MAX_HISTORY_MESSAGES must be actively consumed"
