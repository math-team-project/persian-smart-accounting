# `math_to_latex.py` — Documentation

This module converts JS/Python-like evaluation strings — the kind used in
data-validation rules, e.g.

```
Math.abs(sources_expense + sources_dedicated - sources_total_provided) == 0
```

— into clean, renderable LaTeX, e.g.

```
$$\left| \text{sources expense} + \text{sources dedicated} - \text{sources total provided} \right| = 0$$
```

The single public function is `math_to_latex(condition)`. Everything else in
the file exists to support it.

## Why parse the expression instead of using regex/string replacement?

An earlier version of this converter used chained `str.replace()` calls and
regex substitutions. That approach breaks down as soon as expressions are
**nested or combined** — e.g. `round(x / 1000000)`, or
`abs(a + b) == round(sum(c) + sum(d))` — because regex has no real concept of
"this closing parenthesis belongs to that opening one, three functions deep."
Fixing one case tends to re-break another.

Instead, this module uses Python's built-in `ast` (Abstract Syntax Tree)
module. Once an expression is translated into valid Python syntax, `ast.parse`
turns it into a tree that correctly captures the actual structure of the
expression — which operator applies to what, and in what order — regardless
of how deeply it's nested. Walking that tree and emitting LaTeX for each node
type is then mechanical and reliable, and it scales to new combinations for
free.

## Section-by-section walkthrough

### 1. `TOKEN_REPLACEMENTS`

```python
TOKEN_REPLACEMENTS = [
    ("===", "=="), ("!==", "!="),
    ("&&", " and "), ("||", " or "),
    ...
]
```

**What it does:** Before anything is parsed, JavaScript-flavored syntax
(`&&`, `Math.abs`, `===`, `.toUpperCase()`, ...) is textually translated into
valid Python syntax (`and`, `abs`, `==`, `.upper()`, ...), because `ast.parse`
only understands Python.

**Why a list of tuples, and why that order:** Order matters because some
tokens are substrings of others. `&&` must be replaced *before* the
single-`&` rule runs, otherwise `&&` would first become `& &` and then
`and and`. Keeping the pairs in an explicit, ordered list (rather than a
`dict`, which has no guaranteed *meaning* of order to a reader) makes the
dependency between rules visible and easy to reorder correctly if a new
token is added.

**Why this step is a simple `str.replace` and not part of the AST walk:**
These are lexical (surface-syntax) substitutions with no structural
ambiguity — `Math.abs` always means `abs`, everywhere. Doing it with plain
string replacement before parsing is simpler and cheaper than teaching the
AST walker to recognize `Math.*` as a special case of attribute access.

### 2. `COMPARE_SYMBOLS`, `BINOP_SYMBOLS`, `FUNCTION_TEMPLATES`

```python
COMPARE_SYMBOLS  = {ast.Eq: "=", ast.NotEq: r"\neq", ...}
BINOP_SYMBOLS    = {ast.Add: "+", ast.Sub: "-", ast.Mult: r"\times", ...}
FUNCTION_TEMPLATES = {
    "abs":   r"\left| {0} \right|",
    "round": r"\operatorname{{round}}\left({0}\right)",
    ...
}
```

**What they do:** These three tables are the *only* place that decides what
the LaTeX output actually looks like. `COMPARE_SYMBOLS`/`BINOP_SYMBOLS` map a
Python AST operator class (e.g. `ast.Eq`) to its LaTeX symbol.
`FUNCTION_TEMPLATES` maps a function name to a Python format-string template,
where `{0}`, `{1}`, ... are the function's arguments (already converted to
LaTeX).

**Why they're separated from the recursive logic below:** This is the main
answer to "make it easy to change." If you want `round(...)` to render as
`\text{round}(...)` instead of `\operatorname{round}(...)`, or you want a new
function like `log` supported, you edit **one line in a table** — you never
need to touch, or even fully understand, the recursive walker in section 4.
The walker's job is only "find the right template and fill it in"; it has no
opinion about what any specific symbol should look like.

**Why `FUNCTION_TEMPLATES` uses `str.format` with `{0}`/`{1}` placeholders:**
This keeps the template itself readable as plain LaTeX (you can read
`r"\left| {0} \right|"` and immediately picture the output), instead of
building strings with `%` operators or f-string logic scattered through the
walker.

### 3. `PRECEDENCE`

```python
PRECEDENCE = {"or": 1, "and": 2, "compare": 3, "add_sub": 4, "mul_div": 5, "atom": 6}
```

**What it does:** Assigns a numeric precedence level to each kind of
operator, mirroring standard math/Python precedence (`or` binds loosest,
then `and`, then comparisons, then `+`/`-`, then `*`/`/`, with plain
values/function-calls binding tightest).

**Why it's needed at all:** Consider `a * (b - c)` vs. `a * b - c`. Both
parse into a `BinOp`, but only the first one requires parentheses in the
output to preserve its meaning — dropping the parens would silently change
what the formula says. `PRECEDENCE`, together with the `_wrap()` helper
below, is what decides, per sub-expression, "does this need
`\left( ... \right)` around it, or is it already unambiguous here?" Without
this, the converter would either always add parentheses (visually noisy,
e.g. `(a) + (b) - (c)`) or never add them (silently wrong for expressions
like `a * (b - c)`).

### 4. `_var(name)` and `_num(value)`

```python
def _var(name):
    return r"\text{%s}" % name.replace("_", " ")

def _num(value):
    if isinstance(value, bool):
        return r"\text{True}" if value else r"\text{False}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
```

**What they do:**
- `_var` renders an identifier (variable name). Underscores are replaced
  with spaces (`tafrigh_expense_diff` → `\text{tafrigh expense diff}`)
  because in a variable name, `_` means "word separator," not "subscript" —
  rendering it as a literal LaTeX subscript (`tafrigh_{expense}...`) would be
  misleading.
- `_num` renders a numeric constant, and specifically drops a redundant
  `.0` from integer-valued floats (`19191012833.0` → `19191012833`), because
  values coming from validation rules are frequently floats internally
  (`114257639634.0`) but reading `.0` in a formula that's about whole
  amounts of money/units adds visual noise without adding information.
  `bool` is checked *before* the generic numeric branch because `bool` is a
  subtype of `int` in Python — without that check, `True`/`False` would
  silently be rendered as `1`/`0`.

**Why these are their own tiny functions instead of inline code:** Both are
single, self-contained "policy" decisions (how do we spell a variable? how
do we spell a number?) that are easy to want to change independently later
— e.g. switching `_var` to keep underscores (`\text{tafrigh\_expense\_diff}`)
instead of spaces is then a one-line edit in one place, guaranteed to apply
everywhere a name is rendered.

### 5. `_wrap(latex, node_precedence, needed_precedence)`

```python
def _wrap(latex, node_precedence, needed_precedence):
    if node_precedence < needed_precedence:
        return r"\left(%s\right)" % latex
    return latex
```

**What it does:** Given a piece of already-rendered LaTeX and two
precedence numbers, adds parentheses around it only if its own precedence is
lower than what's required in its current position.

**Why it's a separate helper instead of inlined at each call site:** The
same "should I parenthesize this?" decision is needed in several places
(both sides of a `BinOp`, each operand of a `BoolOp`, the operand of a
`UnaryOp`). Pulling it into one function means the rule is defined and
tested in exactly one place, so all call sites stay consistent by
construction.

### 6. `_to_latex(node)` — the recursive core

This is the only nontrivial function in the module. It takes one AST node
and returns a tuple: `(latex_string, precedence_of_that_expression)`.
Returning the precedence alongside the string (rather than just the string)
is what lets the *parent* node decide whether to wrap its child in
parentheses, without either node needing to know anything about the other's
internals.

It's organized as one `if isinstance(node, ast.X): ...` branch per kind of
Python syntax the converter supports:

| AST node | Meaning | LaTeX handling |
|---|---|---|
| `ast.Expression` | the parsed tree's root | unwrap and recurse into `.body` |
| `ast.List` (1 item) | e.g. `'[x == y]'` | unwrap — see note below |
| `ast.Constant` | a literal number/string/bool | `_num()` / plain text |
| `ast.Name` | a variable | `_var()` |
| `ast.Subscript` | `x[0]` | rendered as a LaTeX subscript, `x_{0}` |
| `ast.UnaryOp` | `-x`, `not x` | `-`, `\neg`, wrapping the operand if needed |
| `ast.BinOp` | `+ - * / % **` | `\frac{}{}` for `/`, `{}^{}` for `**`, symbol lookup + `_wrap()` otherwise |
| `ast.Compare` | `== != < <= > >=` | symbol lookup via `COMPARE_SYMBOLS` |
| `ast.BoolOp` | `and` / `or` | `\land` / `\lor`, wrapping each operand if needed |
| `ast.Call` | `abs(...)`, `round(...)`, ... | template lookup in `FUNCTION_TEMPLATES`, with a generic `\operatorname{name}(...)` fallback for anything not in the table |
| `ast.List` / `ast.Tuple` (general) | literal list/tuple | `\left[ a, b, c \right]` |

A few specific design choices worth calling out:

- **Why `'[x == y]'` is unwrapped instead of rendered as a list:**
  Some inputs arrive pre-wrapped in an extra pair of brackets (this was
  observed directly in the validation data this module processes). Written
  in Python, `[x == y]` is syntactically valid — a one-item list containing
  a comparison. If left alone, that would render as
  `\left[ x = y \right]`, which visually implies "this is a set/array
  containing one boolean," which is not the intended meaning — the intent
  is just the equation `x = y`. So a one-item list is treated as "extra
  bracket noise" and unwrapped; a list with more than one item is still
  rendered literally as a list, since that *is* a real list in that case.

- **Why division always becomes `\frac{}{}` with no extra parentheses:**
  A LaTeX fraction is already visually self-contained — the fraction bar
  itself makes the grouping unambiguous, so numerator and denominator never
  need `\left( ... \right)` around them the way, say, a multiplication's
  operands might.

- **Why subtraction needs one more precedence level on its right side:**
  ```python
  right_needed = precedence + 1 if isinstance(node.op, ast.Sub) else precedence
  ```
  `+` and `*` are commutative and associative, so `a - (b - c)` and
  `a - b - c` are genuinely different values, but `a + (b + c)` and
  `a + b + c` are not. Because of that, the right-hand side of a subtraction
  must be parenthesized whenever it is itself an addition/subtraction (its
  precedence must be *strictly greater*, not just equal), otherwise
  `a - (b - c)` would silently render as `a - b - c` and change the meaning
  of the formula. Addition doesn't have this problem, so it doesn't need the
  extra `+ 1`.

- **Why unknown function names still work:** the `Call` branch falls back to
  `\operatorname{name}(...)` for any function not listed in
  `FUNCTION_TEMPLATES`, instead of raising an error. Validation rules can
  reference many different helper functions; the fallback means the
  converter degrades gracefully (still produces readable, if generic, LaTeX)
  instead of failing on the first unrecognized name.

### 7. `math_to_latex(condition)` — the public entry point

```python
def math_to_latex(condition):
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
        return f"$${text}$$"

    return f"$${latex_body}$$"
```

**What it does, step by step:**

1. **Collection handling.** If given a `list`/`tuple`/`pandas.Series`/
   `numpy.ndarray`, it converts every item individually and returns a list of
   results. This is a `isinstance` check at the very top, so all downstream
   logic only ever has to deal with one string at a time — the recursive
   converter never needs to know that batches exist.
2. **Empty/missing input handling.** `None`, `""`, and `NaN` (via
   `pandas.isna`, since numeric-missing values commonly show up as float
   `NaN` in data pulled from spreadsheets/DataFrames) all return `""`
   immediately instead of being sent into the parser, since there is no
   meaningful equation to render.
3. **Token translation.** Runs the `TOKEN_REPLACEMENTS` table (section 1)
   to make the text valid Python before parsing.
4. **Parse + convert.** `ast.parse(text, mode="eval")` builds the tree;
   `_to_latex(tree.body)` walks it. `mode="eval"` is used (rather than the
   default `"exec"`) because the input is always a single expression, never
   a statement — this also means assignment, loops, etc. are correctly
   rejected as invalid input rather than silently accepted.
5. **Fail-safe fallback.** If parsing fails (`SyntaxError`) or the tree
   contains a construct the converter doesn't know how to render
   (`ValueError`, raised at the bottom of `_to_latex`), the function does
   **not** crash the caller. It returns the token-translated text wrapped in
   `$$...$$` as-is. This matters because this function is meant to run over
   potentially large batches of validation rules — one unusual or malformed
   rule shouldn't stop the rest of the batch from being converted; the
   caller gets a best-effort, still-inspectable string instead of an
   exception.
6. **Consistent wrapping.** Every successful result is wrapped in
   `$$...$$`, so the caller always gets a ready-to-render math block, never
   a bare, ambiguous string.

## A note on a real issue you ran into: doubled backslashes

If you `print()` a **list** of these results, e.g. `print(result)`, Python
shows you the *repr* of each string, which displays every `\` as `\\`. That
doubled text is just how Python's console output escapes backslashes for
display — it is **not** the actual content of the string. If that
printed/escaped text is copied into a LaTeX renderer, `\\left` is
interpreted as "line break" (`\\`) followed by the literal word `left`,
which is exactly the broken `left|...right|` rendering seen earlier.

**The fix is on the calling side, not in this module:** print or use the
individual strings (`result[0]`, or `for r in result: print(r)`), not the
raw list itself. `math_to_latex` was verified to already return correctly
single-escaped LaTeX (`\left| ... \right|`, one backslash each) — see the
included `__main__` block, which prints each item individually for exactly
this reason.
