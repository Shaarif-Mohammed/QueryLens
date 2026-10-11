"""
QueryLens — AI-Powered Marketplace Analytics Copilot
SQL Validator

Every generated query passes through here before it can reach the database. The
validator is an allow-list, not a deny-list: a query runs only if it is a single
read-only SELECT that touches nothing but the known relations in the `warehouse`
schema. Anything it cannot positively confirm as safe is rejected.

The permitted relations are read from the metadata files, so the validator and the
model's instructions always describe the same set of views and tables.

This is one layer of defence among several: the database role is read-only, the
connection is opened read-only, and the executor applies its own row cap and timeout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import sqlparse
import yaml

MAX_ROWS = 500


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "metadata").is_dir():
            return parent
    raise FileNotFoundError("Could not find the project's metadata/ folder.")


METADATA_DIR = _find_project_root() / "metadata"

# Statements and clauses that change data, schema, permissions or session state.
FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "COPY", "CALL", "DO", "EXECUTE", "PREPARE",
    "DEALLOCATE", "VACUUM", "ANALYZE", "REINDEX", "CLUSTER", "LOCK", "LISTEN",
    "NOTIFY", "UNLISTEN", "SET", "RESET", "COMMENT", "SECURITY", "REFRESH",
    "IMPORT", "DISCARD", "LOAD", "INTO", "RETURNING", "CHECKPOINT", "BEGIN",
    "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
}
# Row-locking clauses (FOR UPDATE is caught by UPDATE above).
ROW_LOCK = re.compile(r"\bFOR\s+(NO\s+KEY\s+UPDATE|KEY\s+SHARE|SHARE)\b", re.IGNORECASE)

# Functions with side effects or access to the server, files, network or settings.
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "dblink", "lo_")
FORBIDDEN_FUNCTIONS = {
    "set_config", "current_setting", "query_to_xml", "query_to_xml_and_xmlschema",
    "table_to_xml", "table_to_xml_and_xmlschema", "cursor_to_xml", "schema_to_xml",
    "database_to_xml", "txid_current", "nextval", "setval", "currval",
}
# Schemas and catalogs that must never be referenced, qualified or not.
FORBIDDEN_SCHEMA_PATTERN = re.compile(
    r"\b(staging|public|information_schema|pg_catalog|pg_toast|pg_temp\w*)\b", re.IGNORECASE
)
# Table functions allowed in FROM (harmless, useful for calendar grids).
ALLOWED_TABLE_FUNCTIONS = {"generate_series", "unnest"}
# Functions whose syntax contains FROM without naming a relation.
FROM_INSIDE_FUNCTIONS = {"EXTRACT", "TRIM", "SUBSTRING", "OVERLAY", "POSITION"}
# Words that end a FROM item (so they are not mistaken for an alias).
CLAUSE_WORDS = {
    "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "FETCH", "UNION",
    "INTERSECT", "EXCEPT", "WINDOW", "JOIN", "INNER", "LEFT", "RIGHT", "FULL",
    "CROSS", "NATURAL", "ON", "USING", "LATERAL", "FOR", "AND", "OR", "WITH",
    "SELECT", "FROM", "AS", "TABLESAMPLE", "OUTER",
}
# Non-deterministic "today" functions: the warehouse's reference date is 2018-09-01.
NOW_FUNCTIONS = re.compile(
    r"\b(CURRENT_DATE|CURRENT_TIMESTAMP|CURRENT_TIME|LOCALTIMESTAMP|LOCALTIME|NOW\s*\(|"
    r"CLOCK_TIMESTAMP\s*\(|STATEMENT_TIMESTAMP\s*\(|TRANSACTION_TIMESTAMP\s*\()",
    re.IGNORECASE,
)

TOKEN = re.compile(
    r'"(?:[^"]|"")*"'          # quoted identifier
    r"|''"                     # masked string literal
    r"|[A-Za-z_][A-Za-z0-9_$]*"  # word
    r"|\d+(?:\.\d+)?"          # number
    r"|::|[(),.;*]"            # punctuation we care about
    r"|\S",                    # anything else, one char at a time
)


@dataclass
class ValidationResult:
    ok: bool
    sql: str  # the query to execute (row limit applied); empty if rejected
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)  # warehouse relations read


@lru_cache(maxsize=1)
def allowed_relations() -> tuple[str, frozenset[str]]:
    """(schema name, permitted relation names), read from the metadata files."""
    with (METADATA_DIR / "views_reference.yaml").open(encoding="utf-8") as f:
        views = yaml.safe_load(f)
    with (METADATA_DIR / "schema_dictionary.yaml").open(encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    names = set(views["views"]) | set(schema["tables"]) | set(schema["ml_tables"]["tables"])
    return views["warehouse"]["schema"], frozenset(names)


def validate_sql(sql: str) -> ValidationResult:
    """Validate one generated query. Returns the SQL to run, or the reasons it was refused."""
    errors: list[str] = []
    warnings: list[str] = []

    if not sql or not sql.strip():
        return ValidationResult(False, "", ["No SQL to validate."])

    # Comments can hide text from a reader; remove them and validate what remains.
    cleaned = sqlparse.format(sql, strip_comments=True).strip()
    cleaned = re.sub(r";\s*$", "", cleaned).strip()
    if not cleaned:
        return ValidationResult(False, "", ["The query is empty after removing comments."])

    if re.search(r"\$[A-Za-z_0-9]*\$", cleaned):
        return ValidationResult(False, "", ["Dollar-quoted strings are not allowed."])

    statements = [s for s in sqlparse.split(cleaned) if s.strip().rstrip(";").strip()]
    if len(statements) != 1:
        return ValidationResult(False, "", [f"Exactly one statement is allowed; found {len(statements)}."])

    masked = _mask_strings(cleaned)
    if masked is None:
        return ValidationResult(False, "", ["Unterminated string literal."])
    tokens = TOKEN.findall(masked)
    words = [t.upper() for t in tokens if re.match(r"[A-Za-z_]", t)]

    # 1. Must be a query.
    if not words or words[0] not in {"SELECT", "WITH"}:
        errors.append("Only SELECT queries (optionally starting with WITH) are allowed.")

    # 2. No statements or clauses that write, change settings or lock rows.
    bad = sorted({w for w in words if w in FORBIDDEN_KEYWORDS})
    if bad:
        errors.append(f"Forbidden keyword(s): {', '.join(bad)}.")
    if ROW_LOCK.search(masked):
        errors.append("Row-locking clauses (FOR SHARE / FOR UPDATE) are not allowed.")

    # 3. No other schemas or system catalogs, anywhere.
    for match in sorted({m.group(1).lower() for m in FORBIDDEN_SCHEMA_PATTERN.finditer(masked)}):
        errors.append(f"Reference to '{match}' is not allowed; only the warehouse schema can be queried.")

    # 4. No dangerous functions.
    for i, tok in enumerate(tokens[:-1]):
        if tokens[i + 1] == "(" and re.match(r"[A-Za-z_]", tok):
            name = tok.lower()
            if name.startswith(FORBIDDEN_FUNCTION_PREFIXES) or name in FORBIDDEN_FUNCTIONS:
                errors.append(f"Function '{name}' is not allowed.")

    # 5. Every relation read must be on the allow-list.
    schema, allowed = allowed_relations()
    relations, relation_errors = _check_relations(tokens, schema, allowed, _cte_names(masked))
    errors.extend(relation_errors)

    if errors:
        return ValidationResult(False, "", _dedupe(errors), warnings, relations)

    # 6. Warnings: the query is allowed but should be reviewed.
    if NOW_FUNCTIONS.search(masked):
        warnings.append(
            "Uses the current date/time; QueryLens's reference date is 2018-09-01, "
            "so results may be empty or misleading."
        )

    # 7. Row limit.
    final_sql, limit_note = _apply_row_limit(cleaned, tokens)
    if limit_note:
        warnings.append(limit_note)

    return ValidationResult(True, final_sql, [], warnings, relations)


# --------------------------------------------------------------------------- helpers

def _mask_strings(sql: str) -> str | None:
    """Replace every '...' string literal with '' so keywords inside strings are ignored."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while True:
                if j >= n:
                    return None
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append("''")
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _unquote(identifier: str) -> str:
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier.lower()


def _cte_names(masked: str) -> set[str]:
    pattern = re.compile(
        r"(?:\bWITH\s+(?:RECURSIVE\s+)?|\)\s*,\s*)"
        r"([A-Za-z_][A-Za-z0-9_]*|\"(?:[^\"]|\"\")*\")\s*(?:\([^()]*\)\s*)?"
        r"AS\s+(?:NOT\s+)?(?:MATERIALIZED\s+)?\(",
        re.IGNORECASE,
    )
    return {_unquote(m.group(1)) for m in pattern.finditer(masked)}


def _is_word(tok: str) -> bool:
    return bool(re.match(r'[A-Za-z_"]', tok)) and tok != "''"


def _check_relations(tokens, schema, allowed, ctes):
    """Find every relation after FROM / JOIN and check it against the allow-list."""
    relations, errors = [], []
    stack: list[str | None] = []  # function name that opened each parenthesis
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        up = tok.upper()
        if tok == "(":
            prev = tokens[i - 1].upper() if i > 0 and _is_word(tokens[i - 1]) else None
            stack.append(prev)
        elif tok == ")":
            if stack:
                stack.pop()
        elif up in {"FROM", "JOIN"}:
            inside_function = bool(stack) and stack[-1] in FROM_INSIDE_FUNCTIONS
            distinct_from = up == "FROM" and i >= 1 and tokens[i - 1].upper() == "DISTINCT"
            if not inside_function and not distinct_from:
                # Read each FROM item. The outer walk (index i) still visits every
                # token, so FROMs inside subqueries and function arguments are
                # checked too; index j only reads this item list.
                j = i + 1
                while j < n:
                    while j < n and tokens[j].upper() in {"LATERAL", "ONLY"}:
                        j += 1
                    if j >= n:
                        break
                    if tokens[j] == "(":
                        j = _skip_parens(tokens, j)  # subquery or VALUES list
                    else:
                        parts = [tokens[j]]
                        j += 1
                        while j + 1 < n and tokens[j] == "." and _is_word(tokens[j + 1]):
                            parts.append(tokens[j + 1])
                            j += 2
                        if not _is_word(parts[0]):
                            errors.append(f"Could not read the relation after {up}.")
                            break
                        if j < n and tokens[j] == "(":
                            fname = _unquote(parts[-1])
                            if len(parts) > 1 or fname not in ALLOWED_TABLE_FUNCTIONS:
                                errors.append(f"Table function '{'.'.join(parts)}' is not allowed.")
                            j = _skip_parens(tokens, j)
                        else:
                            errors.extend(_check_name(parts, schema, allowed, ctes, relations))
                    # Optional alias, with an optional column list.
                    if j < n and tokens[j].upper() == "AS":
                        j += 1
                    if j < n and _is_word(tokens[j]) and tokens[j].upper() not in CLAUSE_WORDS:
                        j += 1
                        if j < n and tokens[j] == "(":
                            j = _skip_parens(tokens, j)
                    if up == "FROM" and j < n and tokens[j] == ",":
                        j += 1
                        continue  # next item in a comma-separated FROM list
                    break
        i += 1
    return relations, errors


def _skip_parens(tokens: list[str], j: int) -> int:
    """Given tokens[j] == '(', return the index just after its matching ')'."""
    depth = 0
    while j < len(tokens):
        if tokens[j] == "(":
            depth += 1
        elif tokens[j] == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return j


def _check_name(parts, schema, allowed, ctes, relations):
    names = [_unquote(p) for p in parts]
    if len(names) == 1:
        if names[0] in ctes:
            return []
        if names[0] in allowed:
            return [f"Relation '{names[0]}' must be schema-qualified as {schema}.{names[0]}."]
        return [f"Unknown relation '{names[0]}'."]
    if len(names) > 2:
        return [f"Relation '{'.'.join(names)}' has too many name parts."]
    rel_schema, rel = names
    if rel_schema != schema:
        return [f"Schema '{rel_schema}' is not allowed; only {schema} can be queried."]
    if rel not in allowed:
        return [f"Relation '{schema}.{rel}' is not a known QueryLens view or table."]
    if f"{schema}.{rel}" not in relations:
        relations.append(f"{schema}.{rel}")
    return []


def _apply_row_limit(sql: str, tokens: list[str]) -> tuple[str, str | None]:
    """Keep a top-level LIMIT of MAX_ROWS or less; otherwise cap the result."""
    depth, limit_value, has_fetch = 0, None, False
    for i, tok in enumerate(tokens):
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0 and tok.upper() == "LIMIT" and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            limit_value = int(nxt) if nxt.isdigit() else "ALL"
        elif depth == 0 and tok.upper() == "FETCH":
            has_fetch = True

    if isinstance(limit_value, int) and limit_value <= MAX_ROWS:
        return sql, None
    if limit_value is None and not has_fetch:
        return f"{sql}\nLIMIT {MAX_ROWS}", None
    capped = f"SELECT * FROM (\n{sql}\n) AS capped_result\nLIMIT {MAX_ROWS}"
    return capped, f"Result capped at {MAX_ROWS} rows."


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
