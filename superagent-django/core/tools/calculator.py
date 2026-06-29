"""
Calculator tool — evaluates arithmetic expressions safely.

Zone: GREEN — runs automatically, no human approval required.

Supports standard arithmetic operators (+, -, *, /, **, %).
Deliberately blocks any expression that tries to call builtins or
access attributes to prevent code injection.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from .base_tool import BaseTool, ToolZone


# Allowed AST node types and binary operators
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,  # covers all numeric literals (Python 3.8+)
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
)

_BIN_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type, Any] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """Recursively evaluate a parsed AST node.

    Args:
        node: An AST node from ``ast.parse(..., mode='eval').body``.

    Returns:
        The numeric result of the expression.

    Raises:
        ValueError: For unsupported operations or division by zero.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ValueError("Division by zero is not allowed.")
        return _BIN_OPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _safe_eval(node.operand)
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        return _UNARY_OPS[op_type](operand)
    raise ValueError(f"Unsupported AST node type: {type(node).__name__}")


class CalculatorTool(BaseTool):
    """Evaluate a simple arithmetic expression and return the result.

    Input format:
        Any arithmetic expression using +, -, *, /, //, %, ** and
        numeric literals.  Examples:
            "1200 + 800 + 3400"
            "2 ** 10"
            "100 / 4 * 3"

    Returns:
        The result formatted as a clean numeric string, e.g. ``"5400.0"``.
    """

    name: str = "calculator"
    description: str = (
        "Evaluates an arithmetic expression and returns the numeric result. "
        "Supports +, -, *, /, //, %, ** and numeric literals. "
        "Input: an expression string like '1200 + 800 + 3400'."
    )
    zone: ToolZone = ToolZone.GREEN

    def run(self, input_str: str) -> str:
        """Evaluate the arithmetic expression in ``input_str``.

        Args:
            input_str: A string containing a valid arithmetic expression.

        Returns:
            String representation of the calculated result.

        Raises:
            ValueError: For empty input, invalid syntax, unsupported
                        operations, or division by zero.
        """
        if not input_str or not input_str.strip():
            raise ValueError("CalculatorTool received an empty expression.")

        expression = input_str.strip()

        # Validate only allowed characters before parsing
        allowed_chars = set("0123456789+-*/.()%^ \t")
        # Allow ** by including ^ then replace later; check for bad chars
        for ch in expression.replace("**", ""):
            if ch not in allowed_chars:
                raise ValueError(
                    f"Invalid character '{ch}' in expression: {expression!r}"
                )

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError(
                f"Invalid expression syntax: {expression!r}"
            ) from exc

        # Ensure all nodes are safe
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise ValueError(
                    f"Unsafe expression — contains disallowed node: "
                    f"{type(node).__name__}"
                )

        result = _safe_eval(tree.body)

        # Return int-like results without .0 for cleaner output
        if result == int(result):
            return str(int(result))
        return str(result)
