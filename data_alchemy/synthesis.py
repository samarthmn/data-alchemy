"""Programmatic dataset synthesis from a model-designed spec.

The model returns a spec (tables -> columns + per-table row counts); we generate the
actual rows here so any volume is produced reliably, with referential integrity AND
cross-field coherence:
    * each row picks one locale, so name/address/city/country/phone agree;
    * email/username are derived from the row's generated name;
    * "formula" columns are computed from sibling columns (e.g. quantity * unit_price);
    * "after" date columns are generated relative to an earlier date in the same row.
"""

import ast
import datetime as dt
import operator
import random
import re
import unicodedata

from faker import Faker

from data_alchemy.config import DEFAULT_LOCALES, MAX_ROWS_PER_TABLE

# Faker field types whose values come straight from a localized Faker instance.
ENTITY_TYPES = {
    "name",
    "first_name",
    "last_name",
    "email",
    "username",
    "phone",
    "company",
    "job",
    "address",
    "street_address",
    "city",
    "state",
    "postcode",
    "country",
    "country_code",
    "sentence",
    "text",
    "uuid",
}

# Operators and functions allowed inside a "formula" column's expression.
_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_SAFE_FUNCS = {
    "round": round,
    "min": min,
    "max": max,
    "abs": abs,
    "int": int,
    "float": float,
}

_faker_cache: dict[str, Faker] = {}


def _safe_name(name, fallback="table"):
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", str(name)).strip("_") or fallback


def _ascii(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", str(text))
        if not unicodedata.combining(c)
    )


def _localized_faker(locale):
    instance = _faker_cache.get(locale)
    if instance is None:
        try:
            instance = Faker(locale)
        except Exception:
            instance = Faker()
        _faker_cache[locale] = instance
    return instance


def _row_context(table):
    """Per-row context: one locale (so geography agrees) and a lazy person profile."""
    locales = table.get("locales") or list(DEFAULT_LOCALES)
    if isinstance(locales, str):
        locales = [locales]
    locale = random.choice(locales) if locales else random.choice(DEFAULT_LOCALES)
    return {"locale": locale, "faker": _localized_faker(locale)}


def _person(ctx):
    """Generate (once) the row's person so name/email/username stay consistent."""
    if "name" not in ctx:
        faker = ctx["faker"]
        first, last = faker.first_name(), faker.last_name()
        ctx["first_name"] = first
        ctx["last_name"] = last
        ctx["name"] = f"{first} {last}"
    return ctx


def _random_date(col):
    try:
        start = dt.date.fromisoformat(str(col.get("start") or "2020-01-01"))
        end = dt.date.fromisoformat(str(col.get("end") or "2024-12-31"))
    except ValueError:
        start, end = dt.date(2020, 1, 1), dt.date(2024, 12, 31)
    if end < start:
        start, end = end, start
    return start + dt.timedelta(days=random.randint(0, max((end - start).days, 0)))


def _entity_value(type_name, ctx):
    faker = ctx["faker"]
    if type_name in {"name", "first_name", "last_name"}:
        _person(ctx)
        return ctx["name"] if type_name == "name" else ctx[type_name]
    if type_name == "username":
        _person(ctx)
        return (
            _ascii(f"{ctx['first_name']}.{ctx['last_name']}").lower().replace(" ", "")
        )
    if type_name == "email":
        _person(ctx)
        try:
            domain = faker.free_email_domain()
        except Exception:
            domain = "example.com"
        local = (
            _ascii(f"{ctx['first_name']}.{ctx['last_name']}").lower().replace(" ", "")
        )
        return f"{local}@{domain}"

    simple = {
        "phone": faker.phone_number,
        "company": faker.company,
        "job": getattr(faker, "job", faker.word),
        "address": lambda: faker.address().replace("\n", ", "),
        "street_address": getattr(faker, "street_address", faker.address),
        "city": faker.city,
        "state": getattr(faker, "state", lambda: ""),
        "postcode": getattr(faker, "postcode", lambda: ""),
        "country": getattr(faker, "current_country", faker.country),
        "country_code": getattr(faker, "current_country_code", faker.country_code),
        "sentence": lambda: faker.sentence(nb_words=8),
        "text": lambda: faker.text(max_nb_chars=120),
        "uuid": faker.uuid4,
    }
    provider = simple.get(type_name)
    try:
        return provider() if provider else faker.word()
    except Exception:
        return faker.word()


def _make_value(col, fk_pools, ctx):
    type_name = str(col.get("type") or "text").lower()

    if type_name == "int":
        lo, hi = int(col.get("min", 0)), int(col.get("max", 100))
        lo, hi = min(lo, hi), max(lo, hi)
        return random.randint(lo, hi)
    if type_name == "float":
        lo, hi = float(col.get("min", 0)), float(col.get("max", 1))
        lo, hi = min(lo, hi), max(lo, hi)
        return round(random.uniform(lo, hi), int(col.get("decimals", 2)))
    if type_name == "bool":
        return random.choice([True, False])
    if type_name in {"date", "datetime"}:
        day = _random_date(col)
        if type_name == "date":
            return day.isoformat()
        clock = dt.time(
            random.randint(0, 23), random.randint(0, 59), random.randint(0, 59)
        )
        return dt.datetime.combine(day, clock).isoformat(sep=" ")
    if type_name == "category":
        values = col.get("values") or ["A", "B", "C"]
        weights = col.get("weights")
        if weights and len(weights) == len(values):
            return random.choices(values, weights=weights, k=1)[0]
        return random.choice(values)
    if type_name == "foreign_key":
        ref = str(col.get("references") or "")
        pool = fk_pools.get(ref) or fk_pools.get(ref.split(".")[0])
        return random.choice(pool) if pool else None
    if type_name in ENTITY_TYPES:
        return _entity_value(type_name, ctx)
    return _entity_value("text", ctx)


def _date_after(base_value, col, type_name):
    try:
        base_date = dt.date.fromisoformat(str(base_value)[:10])
    except (ValueError, TypeError):
        base_date = dt.date.today()
    lo, hi = int(col.get("min_days", 1)), int(col.get("max_days", 30))
    lo, hi = min(lo, hi), max(lo, hi)
    new_date = base_date + dt.timedelta(days=random.randint(lo, hi))
    if type_name == "datetime":
        clock = dt.time(
            random.randint(0, 23), random.randint(0, 59), random.randint(0, 59)
        )
        return dt.datetime.combine(new_date, clock).isoformat(sep=" ")
    return new_date.isoformat()


def _safe_eval(expr, variables):
    """Evaluate an arithmetic formula over row values; unknown names resolve to 0."""

    def evaluate(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            value = variables.get(node.id, 0)
            return value if isinstance(value, (int, float)) else 0
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINOPS:
            return _SAFE_BINOPS[type(node.op)](
                evaluate(node.left), evaluate(node.right)
            )
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _SAFE_FUNCS
            and not node.keywords
        ):
            return _SAFE_FUNCS[node.func.id](*[evaluate(arg) for arg in node.args])
        raise ValueError("unsupported expression")

    return evaluate(ast.parse(expr, mode="eval").body)


def _resolve_deferred(row, deferred):
    """Resolve formula/`after` columns once their dependencies exist (multi-pass)."""
    pending = list(deferred)
    for _ in range(len(pending) + 1):
        if not pending:
            break
        still, progressed = [], False
        for col in pending:
            name = col.get("name")
            type_name = str(col.get("type")).lower()
            if type_name in {"date", "datetime"}:
                base = row.get(col.get("after"))
                if base is None:
                    still.append(col)
                    continue
                row[name] = _date_after(base, col, type_name)
                progressed = True
            else:  # formula
                expr = str(col.get("expr") or "0")
                refs = [
                    n for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr) if n in row
                ]
                if any(row.get(n) is None for n in refs):
                    still.append(col)
                    continue
                try:
                    value = _safe_eval(expr, row)
                    if "decimals" in col and isinstance(value, (int, float)):
                        value = round(value, int(col["decimals"]))
                    row[name] = value
                except Exception:
                    row[name] = None
                progressed = True
        pending = still
        if not progressed:
            break


def _table_order(tables):
    """Order tables so a table is generated after any parent it references."""
    by_name = {t.get("name"): t for t in tables}
    ordered, visited = [], set()

    def visit(table, stack):
        name = table.get("name")
        if name in visited or name in stack:
            return
        stack.add(name)
        for col in table.get("columns", []):
            if str(col.get("type")).lower() == "foreign_key":
                parent = str(col.get("references") or "").split(".")[0]
                if parent in by_name:
                    visit(by_name[parent], stack)
        stack.discard(name)
        visited.add(name)
        ordered.append(table)

    for table in tables:
        visit(table, set())
    return ordered


def synthesize_tables(tables):
    """Turn a spec into {table_name: {"columns": [...], "rows": [ {..}, ... ]}}."""
    fk_pools = {}
    rendered = {}
    for table in _table_order(tables):
        name = _safe_name(table.get("name"))
        columns = table.get("columns") or []
        col_names = [c.get("name") or f"col_{i}" for i, c in enumerate(columns)]
        count = max(1, min(int(table.get("rows", 10) or 10), MAX_ROWS_PER_TABLE))

        rows = []
        for index in range(1, count + 1):
            ctx = _row_context(table)
            row = {}
            deferred = []
            for col in columns:
                col_name = col.get("name")
                type_name = str(col.get("type") or "text").lower()
                if type_name == "id":
                    prefix = col.get("prefix")
                    row[col_name] = f"{prefix}-{index:06d}" if prefix else index
                elif type_name == "formula" or (
                    type_name in {"date", "datetime"} and col.get("after")
                ):
                    row[col_name] = None
                    deferred.append(col)
                else:
                    row[col_name] = _make_value(col, fk_pools, ctx)
            _resolve_deferred(row, deferred)
            rows.append(row)

        for col in columns:
            if str(col.get("type")).lower() == "id":
                key = f"{table.get('name')}.{col.get('name')}"
                fk_pools[key] = [row[col.get("name")] for row in rows]

        rendered[name] = {"columns": col_names, "rows": rows}
    return rendered
