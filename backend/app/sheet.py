"""The Desk's spreadsheet grid: rows of raw cells (text, numbers, or formulas starting with "="), and a small
formula evaluator so the window and the assistant see the same computed values.

Formulas are stored the way an .xlsx file stores them (English function names, "," between arguments), so they
export unchanged. Italian names and ";" separators are accepted when typed and rewritten on save.
"""
import re

MAX_ROWS, MAX_COLS = 2000, 100
ERRORS = ("#DIV/0!", "#VALUE!", "#NAME?", "#REF!", "#CYCLE!", "#ERROR!")
ITALIAN = {"SOMMA": "SUM", "MEDIA": "AVERAGE", "CONTA.NUMERI": "COUNT", "CONTA.VALORI": "COUNTA", "ARROTONDA": "ROUND",
           "ASS": "ABS", "SE": "IF", "E": "AND", "O": "OR", "NON": "NOT"}
_A1 = r"(\$?)([A-Za-z]{1,2})(\$?)(\d+)"
_REF = re.compile(rf"(?<![A-Za-z0-9_.$]){_A1}(?::{_A1})?(?![A-Za-z0-9_.(])")   # a cell or a range, in formula text
_TOKEN = re.compile(r"""\s*(?:(?P<num>\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+)|"(?P<str>(?:[^"]|"")*)"
    |(?P<ref>\$?[A-Za-z]{1,2}\$?\d+)(?![A-Za-z0-9_.(])|(?P<name>[A-Za-z_][A-Za-z0-9_.]*)|(?P<op><=|>=|<>|[-+*/^&=<>(),:%]))""", re.X)
_STRINGS = re.compile(r'("(?:[^"]|"")*")')


class Error(Exception):
    """A cell error such as #DIV/0!; it spreads to every formula that reads the cell."""


def col_name(i: int) -> str:
    return col_name(i // 26 - 1) + chr(65 + i % 26) if i >= 26 else chr(65 + i)


def cell_at(ref: str) -> tuple[int, int]:
    """'B3' -> (2, 1), zero-based row and column."""
    m = re.fullmatch(r"\$?([A-Za-z]{1,2})\$?(\d+)", ref.strip())
    if not m or m[2] == "0":
        raise ValueError(f"{ref} is not a cell like B3")
    col = 0
    for ch in m[1].upper():
        col = col * 26 + ord(ch) - 64
    return int(m[2]) - 1, col - 1


def clean(rows: list) -> list[list]:
    """Rows as stored: numbers, text or None; formulas normalized; no trailing empty cells or rows."""
    out = []
    for row in rows:
        cells = [_cell(v) for v in (row if isinstance(row, list) else [])]
        while cells and cells[-1] is None:
            cells.pop()
        out.append(cells)
    while out and not out[-1]:
        out.pop()
    if len(out) > MAX_ROWS or any(len(row) > MAX_COLS for row in out):
        raise ValueError(f"a sheet on the Desk can have at most {MAX_ROWS} rows and {MAX_COLS} columns")
    return out


def number(text: str):
    """'120' and '80.5' as numbers; anything else (codes with leading zeros, dates, words) stays text."""
    if re.fullmatch(r"-?(0|[1-9]\d*)(\.\d+)?", text.strip()):
        return float(text) if "." in text else int(text)
    return text


def _cell(v):
    if v is None or isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    text = str(v)
    if text.startswith("=") and len(text) > 1:   # outside quoted text: Italian names and ";" become what .xlsx stores
        parts = _STRINGS.split(text)
        for i in range(0, len(parts), 2):
            part = re.sub(r"[A-Za-z_][A-Za-z0-9_.]*(?=\()", lambda m: ITALIAN.get(m[0].upper(), m[0].upper()), parts[i])
            parts[i] = _REF.sub(lambda m: m[0].upper(), part.replace(";", ","))
        return "".join(parts)
    return text if text.strip() else None


# --- formulas ---

def _parse(src: str):
    """Formula text (without "=") to a tree of tuples."""
    tokens, pos = [], 0
    while pos < len(src.rstrip()):
        m = _TOKEN.match(src, pos)
        if not m:
            raise Error("#REF!" if "#REF!" in src else "#ERROR!")
        tokens.append((m.lastgroup, m[m.lastgroup]))
        pos = m.end()
    tokens.append(("end", ""))
    i = 0

    def take(op=None):
        nonlocal i
        kind, text = tokens[i]
        if op is not None and (kind != "op" or text != op):
            raise Error("#ERROR!")
        i += 1
        return kind, text

    def peek(*ops):
        return tokens[i][0] == "op" and tokens[i][1] in ops

    def binary(ops, operand):
        def level():
            node = operand()
            while peek(*ops):
                node = ("op", take()[1], node, operand())
            return node
        return level

    def unary():
        if peek("-", "+"):
            return ("neg", unary()) if take()[1] == "-" else unary()
        node = primary()
        while peek("%"):
            take()
            node = ("op", "/", node, ("val", 100))
        return node

    def primary():
        kind, text = take()
        if kind == "num":
            return ("val", float(text) if any(c in text for c in ".eE") else int(text))
        if kind == "str":
            return ("val", text.replace('""', '"'))
        if kind == "ref":
            if peek(":"):
                take()
                kind2, text2 = take()
                if kind2 != "ref":
                    raise Error("#ERROR!")
                return ("range", cell_at(text), cell_at(text2))
            return ("ref", cell_at(text))
        if kind == "name":
            if text.upper() in ("TRUE", "FALSE") and not peek("("):
                return ("val", text.upper() == "TRUE")
            if not peek("("):
                raise Error("#NAME?")
            take("(")
            args = []
            while not peek(")"):
                args.append(compare())
                if not peek(")"):
                    take(",")
            take(")")
            return ("call", text.upper(), args)
        if (kind, text) == ("op", "("):
            node = compare()
            take(")")
            return node
        raise Error("#ERROR!")

    compare = binary(("=", "<>", "<", ">", "<=", ">="), binary(("&",), binary(("+", "-"), binary(("*", "/"), binary(("^",), unary)))))
    tree = compare()
    if tokens[i][0] != "end":
        raise Error("#ERROR!")
    return tree


def _num(v) -> float:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        raise Error("#VALUE!")


def _text(v) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return "" if v is None else str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def _numbers(args) -> list[float]:
    """The numbers in the arguments; inside a range, text and empty cells are skipped as Excel does."""
    return [v for a in args for v in (a if isinstance(a, list) else [_num(a)])
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _average(args):
    nums = _numbers(args)
    if not nums:
        raise Error("#DIV/0!")
    return sum(nums) / len(nums)


def _round(args):
    digits = int(_num(args[1])) if len(args) > 1 else 0
    return int(_num(args[0]) * 10 ** digits + (0.5 if _num(args[0]) >= 0 else -0.5)) / 10 ** digits   # halves away from zero, like Excel


def _flat(args):
    return [v for a in args for v in (a if isinstance(a, list) else [a])]


FUNCTIONS = {
    "SUM": lambda a: sum(_numbers(a)), "AVERAGE": _average, "ROUND": _round, "ABS": lambda a: abs(_num(a[0])),
    "MIN": lambda a: min(_numbers(a), default=0), "MAX": lambda a: max(_numbers(a), default=0),
    "COUNT": lambda a: len(_numbers(a)), "COUNTA": lambda a: sum(v is not None for v in _flat(a)),
    "AND": lambda a: all(bool(v) for v in _flat(a)), "OR": lambda a: any(bool(v) for v in _flat(a)),
    "NOT": lambda a: not a[0],
}


class _Grid:
    def __init__(self, rows):
        self.rows, self.done, self.open = rows, {}, set()

    def cell(self, at):
        """The value of a cell; raises Error when the cell holds one."""
        if at not in self.done:
            r, c = at
            raw = self.rows[r][c] if r < len(self.rows) and c < len(self.rows[r]) else None
            if isinstance(raw, str) and raw.startswith("=") and len(raw) > 1:
                if at in self.open:
                    raise Error("#CYCLE!")
                self.open.add(at)
                try:
                    raw = self.eval(_parse(raw[1:]))
                    if isinstance(raw, list):
                        raise Error("#VALUE!")
                except Error as e:
                    raw = e
                except RecursionError:
                    raw = Error("#ERROR!")
                except (ArithmeticError, IndexError, TypeError, ValueError):
                    raw = Error("#VALUE!")
                self.open.discard(at)
            self.done[at] = raw
        if isinstance(self.done[at], Error):
            raise self.done[at]
        return self.done[at]

    def eval(self, node):
        kind = node[0]
        if kind == "val":
            return node[1]
        if kind == "ref":
            return self.cell(node[1])
        if kind == "range":
            (r1, c1), (r2, c2) = node[1], node[2]
            return [self.cell((r, c)) for r in range(min(r1, r2), min(max(r1, r2), len(self.rows) - 1) + 1)
                    for c in range(min(c1, c2), max(c1, c2) + 1)]
        if kind == "neg":
            return -_num(self.eval(node[1]))
        if kind == "call":
            name, args = node[1], node[2]
            if name == "IF":                 # lazy, so =IF(B1=0, 0, A1/B1) never divides by zero
                if len(args) < 2:
                    raise Error("#ERROR!")
                return self.eval(args[1]) if self.eval(args[0]) else self.eval(args[2]) if len(args) > 2 else False
            if name not in FUNCTIONS:
                raise Error("#NAME?")
            return FUNCTIONS[name]([self.eval(a) for a in args])
        op, a, b = node[1], self.eval(node[2]), self.eval(node[3])
        if isinstance(a, list) or isinstance(b, list):
            raise Error("#VALUE!")
        if op == "&":
            return _text(a) + _text(b)
        if op in ("=", "<>"):
            same = a.casefold() == b.casefold() if isinstance(a, str) and isinstance(b, str) else (a or 0) == (b or 0)
            return same == (op == "=")
        if op in ("<", ">", "<=", ">="):
            a, b = (a.casefold(), b.casefold()) if isinstance(a, str) and isinstance(b, str) else (_num(a), _num(b))
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        a, b = _num(a), _num(b)
        if op == "/":
            if b == 0:
                raise Error("#DIV/0!")
            return a / b
        return a + b if op == "+" else a - b if op == "-" else a * b if op == "*" else a ** b


def values(rows: list[list]) -> list[list]:
    """What each cell shows: formulas computed, errors as their code (#DIV/0!, #NAME?, ...)."""
    grid = _Grid(rows)
    out = []
    for r, row in enumerate(rows):
        line = []
        for c in range(len(row)):
            try:
                v = grid.cell((r, c))
            except Error as e:
                v = str(e)
            line.append(round(v, 10) if isinstance(v, float) else v)
        out.append(line)
    return out


def shift(rows: list[list], axis: str, index: int, delta: int) -> list[list]:
    """Inserts (delta > 0) or deletes (delta < 0) rows or columns at a zero-based index, and moves the cell
    references in every formula with them. References to deleted cells become #REF!."""
    gone = range(index, index - delta) if delta < 0 else range(0)

    def moved(m):
        ends = [m.group(1, 2, 3, 4)] + ([m.group(5, 6, 7, 8)] if m[6] else [])
        out, at = [], []
        for i, (abs_col, letters, abs_row, digits) in enumerate(ends):
            if digits == "0":
                return m[0]
            row, col = cell_at(letters + digits)
            pos = row if axis == "row" else col
            if pos in gone:
                if len(ends) == 1:
                    return "#REF!"
                pos = index - i          # a range shrinks to the cells that are left
            elif pos >= index:
                pos += delta
            at.append(pos)
            row, col = (pos, col) if axis == "row" else (row, pos)
            out.append(f"{abs_col}{col_name(col)}{abs_row}{row + 1}")
        return "#REF!" if len(at) == 2 and at[0] > at[1] else ":".join(out)

    def fixed(v):
        if not (isinstance(v, str) and v.startswith("=")):
            return v
        parts = _STRINGS.split(v)
        return "".join(p if i % 2 else _REF.sub(moved, p) for i, p in enumerate(parts))

    rows = [[fixed(v) for v in row] for row in rows]
    if axis == "row":
        rows[index:index - delta if delta < 0 else index] = [[] for _ in range(max(delta, 0))]
    else:
        for row in rows:
            if index < len(row):
                row[index:index - delta if delta < 0 else index] = [None] * max(delta, 0)
    return clean(rows)


def as_lines(rows: list[list], first: int = 0, last: int | None = None) -> list[str]:
    """The grid for the assistant: one line per row that has content, each cell with its address; formulas show
    their result."""
    shown = values(rows)
    lines = []
    for r in range(max(first, 0), len(rows) if last is None else min(last + 1, len(rows))):
        cells = [f"{col_name(c)}{r + 1}: {v}" + (f" (shows {_text(shown[r][c])})" if isinstance(v, str) and v.startswith("=") else "")
                 for c, v in enumerate(rows[r]) if v is not None]
        if cells:
            lines.append(" | ".join(cells))
    return lines
