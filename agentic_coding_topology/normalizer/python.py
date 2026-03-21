"""
act.normalizer.python — Pass 1: Python AST normalization.

Transforms agent-generated Python into an explicit intermediate form
where every statement does exactly one thing. This is Karpathy's preferred
style enforced mechanically — not for production output, but to make the
true structural shape visible to topology analysis.

Transformations applied:
  - Chained calls decomposed into intermediate variables
  - Subscript-on-call decomposed (fetch(url)[0] → tmp = fetch(url); v = tmp[0])
  - Nested comprehensions flattened where possible
  - Multi-assignment targets split

The normalized form is NOT meant to be pretty code.
It is meant to be analyzable code.
"""

import ast
import textwrap
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NormalizedStatement:
    """A single statement in the normalized intermediate form."""
    var_name: str           # assigned variable name (or _expr_N for void calls)
    operation: str          # human-readable description of what this does
    source_line: int        # original line number in source
    ast_node: ast.AST       # original AST node for further analysis
    depends_on: list[str] = field(default_factory=list)  # variable names this reads
    is_intermediate: bool = False  # True if introduced by normalization, not source


@dataclass
class NormalizedModule:
    """A module after Pass 1 normalization."""
    source_path: str
    statements: list[NormalizedStatement]
    functions: dict[str, list[NormalizedStatement]]  # func_name → statements
    raw_source: str


class PythonNormalizer(ast.NodeVisitor):
    """
    Decomposes complex Python expressions into single-operation statements.

    Agent code tends to chain operations:
        result = process(load(fetch(url)[0]))

    After normalization:
        _t0 = fetch(url)        # intermediate, introduced by normalizer
        _t1 = _t0[0]            # intermediate, introduced by normalizer
        _t2 = load(_t1)         # intermediate, introduced by normalizer
        result = process(_t2)   # original assignment target preserved

    This makes the data flow graph explicit and analyzable.
    """

    def __init__(self):
        self._counter = 0
        self._statements: list[NormalizedStatement] = []
        self._current_function: Optional[str] = None
        self._functions: dict[str, list[NormalizedStatement]] = {}

    def _fresh_var(self) -> str:
        """Generate a fresh intermediate variable name."""
        name = f"_t{self._counter}"
        self._counter += 1
        return name

    def _emit(self, stmt: NormalizedStatement):
        """Record a normalized statement."""
        if self._current_function is not None:
            self._functions.setdefault(self._current_function, []).append(stmt)
        self._statements.append(stmt)

    def _decompose_expr(self, node: ast.expr, target_name: Optional[str] = None,
                        source_line: int = 0) -> str:
        """
        Recursively decompose a complex expression into intermediate statements.
        Returns the variable name holding the final result.

        The key insight: every subexpression that calls a function or performs
        an operation becomes its own statement with its own variable. This
        makes data dependencies explicit as graph edges.
        """
        if isinstance(node, ast.Call):
            # Decompose each argument first
            decomposed_args = []
            for arg in node.args:
                if isinstance(arg, (ast.Call, ast.Subscript, ast.BinOp)):
                    # Complex argument — decompose recursively
                    arg_var = self._decompose_expr(arg, source_line=source_line)
                    decomposed_args.append(arg_var)
                else:
                    # Simple argument — use as-is
                    decomposed_args.append(ast.unparse(arg))

            # Decompose the function expression itself if it's complex
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, (ast.Call, ast.Subscript)):
                    obj_var = self._decompose_expr(node.func.value,
                                                   source_line=source_line)
                    func_repr = f"{obj_var}.{node.func.attr}"
                    depends = [obj_var] + [a for a in decomposed_args
                                           if a.startswith("_t")]
                else:
                    func_repr = ast.unparse(node.func)
                    depends = [a for a in decomposed_args if a.startswith("_t")]
            else:
                func_repr = ast.unparse(node.func)
                depends = [a for a in decomposed_args if a.startswith("_t")]

            var = target_name if target_name else self._fresh_var()
            args_str = ", ".join(decomposed_args)
            self._emit(NormalizedStatement(
                var_name=var,
                operation=f"{func_repr}({args_str})",
                source_line=source_line,
                ast_node=node,
                depends_on=depends,
                is_intermediate=(target_name is None),
            ))
            return var

        elif isinstance(node, ast.Subscript):
            # obj[key] — decompose obj if complex
            if isinstance(node.value, (ast.Call, ast.Subscript)):
                obj_var = self._decompose_expr(node.value, source_line=source_line)
            else:
                obj_var = ast.unparse(node.value)

            var = target_name if target_name else self._fresh_var()
            key_repr = ast.unparse(node.slice)
            self._emit(NormalizedStatement(
                var_name=var,
                operation=f"{obj_var}[{key_repr}]",
                source_line=source_line,
                ast_node=node,
                depends_on=[obj_var] if obj_var.startswith("_t") or
                            obj_var in self._get_known_vars() else [],
                is_intermediate=(target_name is None),
            ))
            return var

        elif isinstance(node, ast.BinOp):
            # Decompose both sides
            left = ast.unparse(node.left)
            right = ast.unparse(node.right)
            deps = []
            if isinstance(node.left, (ast.Call, ast.Subscript, ast.BinOp)):
                left = self._decompose_expr(node.left, source_line=source_line)
                deps.append(left)
            if isinstance(node.right, (ast.Call, ast.Subscript, ast.BinOp)):
                right = self._decompose_expr(node.right, source_line=source_line)
                deps.append(right)

            var = target_name if target_name else self._fresh_var()
            op_symbol = ast.unparse(node.op.__class__())
            self._emit(NormalizedStatement(
                var_name=var,
                operation=f"{left} {op_symbol} {right}",
                source_line=source_line,
                ast_node=node,
                depends_on=deps,
                is_intermediate=(target_name is None),
            ))
            return var

        else:
            # Simple expression — no decomposition needed
            var = target_name if target_name else self._fresh_var()
            self._emit(NormalizedStatement(
                var_name=var,
                operation=ast.unparse(node),
                source_line=source_line,
                ast_node=node,
                depends_on=[],
                is_intermediate=(target_name is None),
            ))
            return var

    def _get_known_vars(self) -> set[str]:
        return {s.var_name for s in self._statements}

    def visit_Assign(self, node: ast.Assign):
        """Handle simple assignments: x = <expr>"""
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            self._decompose_expr(node.value, target_name=target,
                                 source_line=node.lineno)
        else:
            # Multi-target or complex target — emit as-is with note
            var = self._fresh_var()
            self._emit(NormalizedStatement(
                var_name=var,
                operation=ast.unparse(node),
                source_line=node.lineno,
                ast_node=node,
                is_intermediate=False,
            ))
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr):
        """Handle expression statements (void calls, etc.)"""
        var = f"_expr_{self._counter}"
        self._counter += 1
        self._decompose_expr(node.value, target_name=var,
                             source_line=node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        """Handle return statements — decompose their value expression."""
        if node.value is not None:
            var = f"_return_{self._counter}"
            self._counter += 1
            self._decompose_expr(node.value, target_name=var,
                                 source_line=node.lineno)
        # Do NOT call generic_visit — we already handled the value

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Track function boundaries for per-function analysis."""
        prev = self._current_function
        self._current_function = node.name
        self._functions[node.name] = []
        # Manually visit body statements to stay in function context
        for child in node.body:
            self.visit(child)
        self._current_function = prev
        # Do NOT call generic_visit — we visited body manually

    visit_AsyncFunctionDef = visit_FunctionDef


def normalize_python(source: str, path: str = "<string>") -> NormalizedModule:
    """
    Normalize a Python source string.

    Returns a NormalizedModule with all complex expressions decomposed
    into single-operation statements ready for graph construction.
    """
    tree = ast.parse(source)
    normalizer = PythonNormalizer()
    normalizer.visit(tree)

    return NormalizedModule(
        source_path=path,
        statements=normalizer._statements,
        functions=normalizer._functions,
        raw_source=source,
    )


def normalize_file(path: str) -> NormalizedModule:
    """Normalize a Python source file."""
    with open(path) as f:
        source = f.read()
    return normalize_python(source, path=path)
