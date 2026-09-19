"""
Evaluates a checklist question's `evaluation_condition` string against
a dict of resolved variable values.

Two evaluators are provided:

* `ConditionEvaluator` - the original AST-walking implementation.
  Safe, but supports only a small whitelist of operations and does
  not translate JavaScript-flavoured operators (&&, Math.abs, ...).

* `MathConditionEvaluator` - the production evaluator. Delegates to
  `evaluate_logic`, which accepts JS-style operators, handles missing
  values as FAILED sub-conditions, and returns the rich breakdown the
  operational JSON expects. It uses eval() with an empty builtins
  dict, so the expression cannot reach the filesystem/network/imports;
  the evaluator is meant to run only on checklist expressions that
  come from a trusted source file.

Both expose the same "evaluate one expression" contract so callers can
swap them without changing the surrounding code.
"""

import ast
import logging
import math
import operator
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Legacy AST-walking evaluator (kept for backward compatibility)
# ---------------------------------------------------------------------------

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_COMPARES = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_COMPARE_SYMBOLS = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=",
}
_ALLOWED_FUNCTIONS = {"round": round, "abs": abs, "min": min, "max": max}


class ConditionEvaluationError(Exception):
    """Raised when a condition can't be safely evaluated (e.g. missing variable)."""


class ConditionEvaluator:
    """Original AST-based evaluator. Kept for tests and backward compat."""

    def evaluate(self, expression: str, variables: Dict[str, Optional[float]]) -> bool:
        tree = ast.parse(expression, mode="eval")
        return bool(self._eval_node(tree.body, variables))

    def describe_and_evaluate(
        self, expression: str, variables: Dict[str, Optional[float]]
    ) -> Tuple[Optional[bool], str, Optional[str]]:
        tree = ast.parse(expression, mode="eval").body
        if isinstance(tree, ast.Compare) and len(tree.ops) == 1 and type(tree.ops[0]) in _ALLOWED_COMPARES:
            left, left_error = self._safe_eval(tree.left, variables)
            right, right_error = self._safe_eval(tree.comparators[0], variables)
            symbol = _COMPARE_SYMBOLS[type(tree.ops[0])]
            breakdown = f"[{left!r} {symbol} {right!r}]"
            if left_error or right_error:
                return None, breakdown, left_error or right_error
            passed = _ALLOWED_COMPARES[type(tree.ops[0])](left, right)
            return passed, breakdown, None
        try:
            return self.evaluate(expression, variables), f"[{expression}]", None
        except ConditionEvaluationError as e:
            return None, f"[{expression}]", str(e)

    def _safe_eval(self, node: ast.AST, variables: Dict[str, Optional[float]]):
        try:
            return self._eval_node(node, variables), None
        except ConditionEvaluationError as e:
            return None, str(e)

    def _eval_node(self, node: ast.AST, variables: Dict[str, Optional[float]]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ConditionEvaluationError(f"Unknown variable in condition: '{node.id}'")
            value = variables[node.id]
            if value is None:
                raise ConditionEvaluationError(f"Variable '{node.id}' has no value")
            return value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](
                self._eval_node(node.left, variables),
                self._eval_node(node.right, variables),
            )
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._eval_node(node.operand, variables)
        if isinstance(node, ast.BoolOp):
            values = [self._eval_node(v, variables) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _ALLOWED_COMPARES:
            left = self._eval_node(node.left, variables)
            right = self._eval_node(node.comparators[0], variables)
            return _ALLOWED_COMPARES[type(node.ops[0])](left, right)
        if isinstance(node, ast.Call):
            func_name = getattr(node.func, "id", None)
            if func_name not in _ALLOWED_FUNCTIONS:
                raise ConditionEvaluationError(f"Function not allowed: '{func_name}'")
            args = [self._eval_node(a, variables) for a in node.args]
            return _ALLOWED_FUNCTIONS[func_name](*args)
        raise ConditionEvaluationError(f"Unsupported expression element: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Production evaluator - delegates to evaluate_logic
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def math_to_latex(formatted_conditions: List[str]) -> List[str]:
    """Minimal LaTeX wrapper. Only used for audit-trail reporting."""
    return [f"${c.replace('==', '=').replace('!=', r'\\neq ')}$"
            for c in formatted_conditions]


# Translating JavaScript-flavoured operators to Python ones. Kept at
# module level so tests can reference the same list.
_OPERATOR_REPLACEMENTS = [
    # logical operators
    ("&&", " and "),
    ("||", " or "),
    ("&", " and "),
    ("|", " or "),
    # math shorthands
    ("Math.abs", "abs"),
    ("Math.round", "round"),
    ("Math.floor", "floor"),
    ("Math.ceil", "ceil"),
    ("Math.min", "min"),
    ("Math.max", "max"),
    ("Math.pow", "pow"),
    # string case helpers
    (".toUpperCase()", ".upper()"),
    (".toLowerCase()", ".lower()"),
    (".trim()", ".strip()"),
    # strict equality
    ("===", "=="),
    ("!==", "!="),
]


def evaluate_logic(
    condition_str: str,
    variables: Dict[str, Any],
    on_true: str = "",
    on_false: str = "",
) -> Dict[str, Any]:
    """Evaluate a checklist condition string and return a full breakdown.

    Returns a dict with:
        status                 "TRUE" | "FALSE" | "ERROR"
        is_true                bool
        message                on_true / on_false / a generic error
        breakdown              list of True/False/"FAILED" per sub-condition
        formatted_conditions   list of human-readable strings, one per
                               sub-condition, with variable values inlined
    """
    if not condition_str:
        return {
            "is_true": False,
            "status": "ERROR",
            "message": "No condition provided.",
            "breakdown": [],
            "formatted_conditions": [],
            "formatted_conditions_latex": [],
        }

    py_condition = condition_str
    for old, new in _OPERATOR_REPLACEMENTS:
        py_condition = py_condition.replace(old, new)

    # NOTE: only `" and "` is used as a sub-condition delimiter. If you
    # ever need per-clause reporting on `or`-chained conditions, extend
    # this split. For the current checklist this is intentional.
    sub_conditions = py_condition.split(" and ")

    eval_context: Dict[str, Any] = {
        "abs": abs,
        "round": round,
        "sum": sum,
        "min": min,
        "max": max,
        "pow": pow,
        "None": None,
        "NaN": float("nan"),
        "floor": math.floor,
        "ceil": math.ceil,
    }
    eval_context.update(variables)

    # Round floats to integers before comparing, so float artifacts
    # (e.g. 54519819997.04 vs 54519819997) do not cause spurious fails.
    eval_context = {
        k: (round(v, 0) if isinstance(v, (int, float)) and not math.isnan(v) else v)
        for k, v in eval_context.items()
    }

    formatted_conditions: List[str] = []
    evaluated_results: List[Any] = []
    all_true = True

    for sub_cond in sub_conditions:
        sub_cond_clean = sub_cond.strip()

        eval_logged_str = sub_cond_clean
        for v_name, v_val in variables.items():
            if v_name in eval_logged_str:
                eval_logged_str = re.sub(
                    rf"\b{re.escape(v_name)}\b", str(v_val), eval_logged_str
                )
        formatted_conditions.append(f"[{eval_logged_str}]")

        # Any variable in this clause that is None/NaN -> FAILED for the
        # whole clause (treated as an extraction failure, not a False).
        missing_vars: List[str] = []
        for var_name, val in variables.items():
            if var_name in sub_cond_clean:
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    missing_vars.append(var_name)

        if missing_vars:
            logger.error(
                "Condition [%s]: FAILED due to missing/NaN variables: %s",
                sub_cond_clean, missing_vars,
            )
            evaluated_results.append("FAILED")
            all_true = False
            continue

        try:
            sub_result = bool(eval(sub_cond_clean, {"__builtins__": None}, eval_context))
            logger.info("Condition [(%s)]: %s", eval_logged_str, sub_result)
            evaluated_results.append(sub_result)
            if not sub_result:
                all_true = False
        except Exception as exc:
            logger.error("Condition [%s]: FAILED TO EVALUATE (%s)", sub_cond_clean, exc)
            evaluated_results.append("FAILED")
            all_true = False

    has_evaluation_error = any(res == "FAILED" for res in evaluated_results)

    if has_evaluation_error:
        final_status = "ERROR"
        final_message = "Evaluation failed due to missing data or execution error."
    elif all_true:
        final_status = "TRUE"
        final_message = on_true
    else:
        final_status = "FALSE"
        final_message = on_false

    return {
        "is_true": final_status == "TRUE",
        "status": final_status,
        "message": final_message,
        "breakdown": evaluated_results,
        "formatted_conditions": formatted_conditions,
        "formatted_conditions_latex": math_to_latex(formatted_conditions),
    }


class MathConditionEvaluator:
    """Production evaluator used by QuestionOrchestrator.

    Exposes the same `describe_and_evaluate`-style contract as the
    legacy evaluator, but backed by `evaluate_logic` and returning the
    rich breakdown the operational JSON expects.
    """

    def evaluate(
        self,
        expression: str,
        variables: Dict[str, Optional[float]],
        on_true: str = "",
        on_false: str = "",
    ) -> Dict[str, Any]:
        return evaluate_logic(expression, variables, on_true, on_false)

    def describe_and_evaluate(
        self,
        expression: str,
        variables: Dict[str, Optional[float]],
        on_true: str = "",
        on_false: str = "",
    ) -> Tuple[Optional[bool], List[Dict[str, Any]], str]:
        """Compatibility method.

        Returns:
            passed: True / False / None (None means ERROR)
            breakdown: list of {"condition": str, "result": value}
                       where value is True, False, or "FAILED"
            message: on_true / on_false / error text
        """
        result = evaluate_logic(expression, variables, on_true, on_false)

        status = result["status"]
        if status == "ERROR":
            passed: Optional[bool] = None
        else:
            passed = status == "TRUE"

        breakdown = [
            {"condition": cond, "result": value}
            for cond, value in zip(result["formatted_conditions"], result["breakdown"])
        ]

        return passed, breakdown, result["message"]