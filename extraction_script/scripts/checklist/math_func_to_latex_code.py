import ast
import pandas as pd
import numpy as np

# --------------------------------------------------------------------------- #
# EVERYTHING YOU'D WANT TO TWEAK LIVES IN THESE TABLES.
# --------------------------------------------------------------------------- #

# JS-like tokens -> Python tokens. Order matters (longer tokens first,
# e.g. "===" before "==", "&&" before "&").
TOKEN_REPLACEMENTS = [
    ("===", "=="), ("!==", "!="),
    ("&&", " and "), ("||", " or "),
    ("&", " and "), ("|", " or "),
    ("Math.abs", "abs"), ("Math.round", "round"), ("Math.floor", "floor"),
    ("Math.ceil", "ceil"), ("Math.min", "min"), ("Math.max", "max"), ("Math.pow", "pow"),
    (".toUpperCase()", ".upper()"), (".toLowerCase()", ".lower()"), (".trim()", ".strip()"),
]

# Comparison / arithmetic operator symbols in LaTeX.
COMPARE_SYMBOLS = {ast.Eq: "=", ast.NotEq: r"\neq", ast.Lt: "<", ast.LtE: r"\leq", ast.Gt: ">", ast.GtE: r"\geq"}
BINOP_SYMBOLS = {ast.Add: "+", ast.Sub: "-", ast.Mult: r"\times", ast.Mod: r"\bmod"}

# Function name -> LaTeX template. "{0}", "{1}", ... are the function's
# arguments already converted to LaTeX. Add/edit an entry to change how a
# function is rendered (or to support a new one).
FUNCTION_TEMPLATES = {
    "abs":   r"\left| {0} \right|",
    "round": r"\operatorname{{round}}\left({0}\right)",
    "sum":   r"\sum\left({0}\right)",
    "min":   r"\min\left({0}\right)",
    "max":   r"\max\left({0}\right)",
    "pow":   r"{{{0}}}^{{{1}}}",
    "floor": r"\left\lfloor {0} \right\rfloor",
    "ceil":  r"\left\lceil {0} \right\rceil",
    "sqrt":  r"\sqrt{{{0}}}",
}

# Precedence levels, only used to decide when parentheses are required
# (e.g. "a * (b - c)" must keep its parens, "a - b + c" must not gain any).
PRECEDENCE = {"or": 1, "and": 2, "compare": 3, "add_sub": 4, "mul_div": 5, "atom": 6}


# --------------------------------------------------------------------------- #
# Small rendering helpers.
# --------------------------------------------------------------------------- #

def _var(name):
    """How a bare identifier (variable name) is rendered in LaTeX."""
    return r"\text{%s}" % name.replace("_", " ")


def _num(value):
    """How a numeric literal is rendered (drops a redundant trailing '.0')."""
    if isinstance(value, bool):
        return r"\text{True}" if value else r"\text{False}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _wrap(latex, node_precedence, needed_precedence):
    """Add \\left( ... \\right) around `latex` only if it's actually needed."""
    if node_precedence < needed_precedence:
        return r"\left(%s\right)" % latex
    return latex


# --------------------------------------------------------------------------- #
# The recursive converter: one AST node in, (latex, precedence) out.
# To support a new kind of expression, just add another `elif` branch.
# --------------------------------------------------------------------------- #

def _to_latex(node):
    if isinstance(node, ast.Expression):
        return _to_latex(node.body)

    # An extra pair of brackets around the whole expression, e.g. '[x == y]',
    # parses as a one-item list -> unwrap it instead of rendering as a list.
    if isinstance(node, ast.List) and len(node.elts) == 1:
        return _to_latex(node.elts[0])

    if isinstance(node, ast.Constant):
        text = _num(node.value) if isinstance(node.value, (int, float, bool)) else str(node.value)
        return text, PRECEDENCE["atom"]

    if isinstance(node, ast.Name):
        return _var(node.id), PRECEDENCE["atom"]

    if isinstance(node, ast.Subscript):
        base, _ = _to_latex(node.value)
        index_node = node.slice.value if isinstance(node.slice, ast.Index) else node.slice  # py < 3.9
        index, _ = _to_latex(index_node)
        return "%s_{%s}" % (base, index), PRECEDENCE["atom"]

    if isinstance(node, ast.UnaryOp):
        value, value_prec = _to_latex(node.operand)
        value = _wrap(value, value_prec, PRECEDENCE["atom"])
        if isinstance(node.op, ast.USub):
            return "-%s" % value, PRECEDENCE["atom"]
        if isinstance(node.op, ast.Not):
            return r"\neg %s" % value, PRECEDENCE["atom"]
        return value, PRECEDENCE["atom"]

    if isinstance(node, ast.BinOp):
        left, left_prec = _to_latex(node.left)
        right, right_prec = _to_latex(node.right)

        if isinstance(node.op, ast.Div):
            return r"\frac{%s}{%s}" % (left, right), PRECEDENCE["atom"]
        if isinstance(node.op, ast.Pow):
            return "{%s}^{%s}" % (left, right), PRECEDENCE["atom"]

        is_add_sub = isinstance(node.op, (ast.Add, ast.Sub))
        precedence = PRECEDENCE["add_sub"] if is_add_sub else PRECEDENCE["mul_div"]
        left = _wrap(left, left_prec, precedence)
        # subtraction is not commutative, so "a - (b - c)" must keep its parens
        right_needed = precedence + 1 if isinstance(node.op, ast.Sub) else precedence
        right = _wrap(right, right_prec, right_needed)
        return "%s %s %s" % (left, BINOP_SYMBOLS[type(node.op)], right), precedence

    if isinstance(node, ast.Compare):
        parts = [_to_latex(node.left)[0]]
        for op, comparator in zip(node.ops, node.comparators):
            parts += [COMPARE_SYMBOLS[type(op)], _to_latex(comparator)[0]]
        return " ".join(parts), PRECEDENCE["compare"]

    if isinstance(node, ast.BoolOp):
        is_and = isinstance(node.op, ast.And)
        symbol = r" \land " if is_and else r" \lor "
        precedence = PRECEDENCE["and"] if is_and else PRECEDENCE["or"]
        parts = []
        for value in node.values:
            latex, prec = _to_latex(value)
            parts.append(_wrap(latex, prec, precedence))
        return symbol.join(parts), precedence

    if isinstance(node, ast.Call):
        func_name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "f")
        args = [_to_latex(a)[0] for a in node.args]
        template = FUNCTION_TEMPLATES.get(func_name)
        if template:
            return template.format(*args), PRECEDENCE["atom"]
        # Unknown function -> generic fallback, still easy to read.
        return r"\operatorname{%s}\left(%s\right)" % (func_name, ", ".join(args)), PRECEDENCE["atom"]

    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_to_latex(e)[0] for e in node.elts]
        return r"\left[%s\right]" % ", ".join(items), PRECEDENCE["atom"]

    raise ValueError(f"Unsupported expression element: {type(node).__name__}")


# --------------------------------------------------------------------------- #
# Public entry point.

def math_to_latex(condition):
    """
    Convert a JS/Python-like evaluation string (e.g. 'a == Math.abs(b) - c')
    into LaTeX, wrapped in $$...$$.
    Also accepts a list/tuple/pandas.Series/numpy.ndarray of such strings,
    in which case each item is converted and a list is returned.
    """
    if isinstance(condition, (list, tuple, pd.Series, np.ndarray)):
        return [math_to_latex(item) for item in condition]

    if condition is None or condition == "" or (isinstance(condition, float) and pd.isna(condition)):
        return ""

    text = str(condition).strip()
    for old, new in TOKEN_REPLACEMENTS:
        text = text.replace(old, new)

    try:
        tree = ast.parse(text, mode="eval")
        latex_body, _ = _to_latex(tree.body)
    except (SyntaxError, ValueError):
        return f"$${text}$$"  # fall back to the raw text instead of crashing

    return f"$${latex_body}$$"


## --------------------------------------------------------------------------- #
## test case
 
# if __name__ == "__main__":
#     samples = [
#         "note_4_1_ending_balance_previous == Math.abs(tafrigh_expense_unrealized_diff) - "
#         "Math.abs(tafrigh_unallocated_sources_diff)",
#         ['[19191012833.0 == abs(114257639634.0) - abs(-29279000000.0)]'],
#         "traz_cash == fs_cash && traz_ar_non_exchange == fs_ar_non_exchange",
#         "round(financial_cost_prev_balances[0]/1000000) == budget_cost_data[0] && "
#         "financial_cost_current_appr/1000000 == budget_cost_data[1]",
#         "round(allocated_credit_930) == round(sum(unused_funds_sarmayeh_current_year) + "
#         "sum(unused_funds_hazine_current_year))",
#         "round(internal_eblaghi_expense_approved/1000000) == external_confirmation_amount",
#         "sum(note_4_last_year_total) == transferred_from_last_year",
#         # new examples
#         "Math.abs(tafrigh_unallocated_diff) == (mosavab_total_approved - mosavab_takhsis)",
#         "Math.abs(sources_expense + sources_dedicated + sources_capital - sources_total_provided)  == 0",
#         "round(approved_credit_920_balance) == round(abs(non_allocation_comparison) + sum(unused_special_funds))",
#         "sum(note_4_last_year_total) == transferred_from_last_year"
#     ]
#     for s in samples:
#         result = math_to_latex(s)
#         print("IN :", s)
#         # Print each string directly (NOT the raw list repr) so backslashes show correctly.
#         if isinstance(result, list):
#             for r in result:
#                 print("OUT:", r)
#         else:
#             print("OUT:", result)
#         print("-" * 60)