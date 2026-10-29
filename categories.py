"""Declared category ranges for logistics plausibility, never size imputation.

Rules are operator-authored bounds in kilograms. Matching uses only explicit
category, subcategory and presentation fields; product titles are not interpreted.
An empty rule subcategory/presentation deliberately covers every value in that
dimension. The most specific matching rule wins, in this order: both fields,
subcategory, presentation, general category. No range is inferred from data.

Database mutations do not commit: callers own the transaction, including any
recalculation/audit work that must accompany a rule change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import sqlite3
import unicodedata

from engine import ValidationError, parse_number


_RANGE_FIELDS = ("real_min_kg", "real_max_kg", "volume_min_kg", "volume_max_kg")
_PUBLIC_FIELDS = ("id", "category", "subcategory", "presentation", *_RANGE_FIELDS,
                  "note", "enabled", "version", "created_at", "updated_at")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@lru_cache(maxsize=8192)
def _normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(char for char in decomposed
                            if not unicodedata.combining(char)).split())


def normalize_scope(value) -> str:
    """Case/accent/space normalization only; no inferred equivalent labels."""
    return _normalize_text(str(value or "").strip())


def _decimal_text(number):
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS category_rules (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      category TEXT NOT NULL, subcategory TEXT NOT NULL DEFAULT '',
      presentation TEXT NOT NULL DEFAULT '',
      category_key TEXT NOT NULL, subcategory_key TEXT NOT NULL DEFAULT '',
      presentation_key TEXT NOT NULL DEFAULT '',
      real_min_kg TEXT, real_max_kg TEXT, volume_min_kg TEXT, volume_max_kg TEXT,
      note TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
      version INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(category_key,subcategory_key,presentation_key)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_category_rules_enabled ON category_rules(enabled,category_key)")


def _rows(cursor):
    keys = [column[0] for column in cursor.description]
    return [dict(zip(keys, row)) for row in cursor.fetchall()]


def _public(row):
    result = {key: row[key] for key in _PUBLIC_FIELDS}
    result["enabled"] = bool(result["enabled"])
    return result


def list_rules(c) -> list[dict]:
    return [_public(row) for row in _rows(c.execute(
        "SELECT * FROM category_rules ORDER BY category_key,subcategory_key,presentation_key,id"))]


def _validated(rule):
    if not isinstance(rule, dict):
        raise ValueError("La regla debe ser un objeto con sus campos.")
    result = {}
    for field, label, limit in (("category", "Categoría", 300),
                                 ("subcategory", "Subcategoría", 300),
                                 ("presentation", "Presentación", 500),
                                 ("note", "Justificación", 5000)):
        value = rule.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"{label}: debe ser texto.")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"{label}: máximo {limit} caracteres.")
        if field in {"category", "note"} and not normalize_scope(value):
            raise ValueError(f"{label}: es obligatoria.")
        result[field] = value
    for field in ("category", "subcategory", "presentation"):
        result[field + "_key"] = normalize_scope(result[field])
    enabled = rule.get("enabled", True)
    if not isinstance(enabled, (bool, int)) or enabled not in (False, True, 0, 1):
        raise ValueError("El estado habilitado debe ser verdadero o falso.")
    result["enabled"] = int(enabled)
    numbers = {}
    for field in _RANGE_FIELDS:
        value = rule.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            numbers[field] = None
            result[field] = None
            continue
        try:
            number = parse_number(value, field)
        except ValidationError as error:
            raise ValueError(str(error)) from error
        if number <= 0:
            raise ValueError(f"{field}: el límite debe ser mayor que cero, en kg.")
        numbers[field] = number
        result[field] = _decimal_text(number)
    if all(value is None for value in numbers.values()):
        raise ValueError("Defina al menos un límite de peso real o volumétrico, en kg.")
    for prefix, label in (("real", "peso real"), ("volume", "peso volumétrico")):
        lower, upper = numbers[prefix + "_min_kg"], numbers[prefix + "_max_kg"]
        if lower is not None and upper is not None and lower > upper:
            raise ValueError(f"El mínimo de {label} no puede superar al máximo.")
    return result


def _rule_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("El identificador de regla debe ser un entero positivo.")
    return value


def save_rule(c, rule: dict, rule_id=None) -> dict:
    """Create or fully replace a scope, increasing its version on every edit."""
    values = _validated(rule)
    if rule_id is not None:
        rule_id = _rule_id(rule_id)
        if not c.execute("SELECT 1 FROM category_rules WHERE id=?", (rule_id,)).fetchone():
            raise ValueError("La regla de categoría no existe.")
    stamp = _now()
    columns = list(values)
    try:
        if rule_id is None:
            cursor = c.execute(
                "INSERT INTO category_rules(" + ",".join(columns) + ",version,created_at,updated_at) "
                "VALUES(" + ",".join("?" for _ in columns) + ",1,?,?)",
                [*(values[column] for column in columns), stamp, stamp],
            )
            rule_id = cursor.lastrowid
        else:
            c.execute(
                "UPDATE category_rules SET " + ",".join(column + "=?" for column in columns)
                + ",version=version+1,updated_at=? WHERE id=?",
                [*(values[column] for column in columns), stamp, rule_id],
            )
    except sqlite3.IntegrityError as error:
        raise ValueError(
            "Ya existe una regla para esa categoría, subcategoría y presentación. Edite la existente."
        ) from error
    return _public(_rows(c.execute("SELECT * FROM category_rules WHERE id=?", (rule_id,)))[0])


def delete_rule(c, rule_id) -> bool:
    rule_id = _rule_id(rule_id)
    return c.execute("UPDATE category_rules SET enabled=0,version=version+1,updated_at=? WHERE id=?", (_now(), rule_id)).rowcount > 0


class CategoryEngine:
    """Immutable in-memory rule snapshot; applying a row performs no SQL."""

    def __init__(self, rules=()):
        self._index = {}
        for supplied in rules:
            if not supplied.get("enabled", True):
                continue
            validated = _validated(supplied)
            rule = {**supplied, **validated}
            key = (rule["category_key"], rule["subcategory_key"], rule["presentation_key"])
            if key in self._index:
                raise ValueError("Hay reglas de categoría duplicadas para el mismo ámbito.")
            rule["_limits"] = {field: parse_number(rule[field], field) if rule[field] is not None else None
                               for field in _RANGE_FIELDS}
            self._index[key] = rule

    @classmethod
    def from_db(cls, c):
        return cls(list_rules(c))

    def apply(self, raw, calc) -> dict:
        """Return a copied calculation with an explainable plausibility check."""
        result = dict(calc)
        for field in ("flags", "reason_codes", "observations", "observation_codes"):
            result[field] = list(calc.get(field) or [])
        result["category_check"] = {"status": "no_rule", "rule_id": None,
                                    "rule_version": None, "scope": None,
                                    "note": "", "checks": []}
        category = normalize_scope(raw.get("category"))
        if not category:
            result["category_check"]["status"] = "no_category"
            result["observations"].append(
                "No se informó categoría: las reglas de categoría no pudieron evaluarse."
            )
            result["observation_codes"].append("category_missing")
            return result
        subcategory = normalize_scope(raw.get("subcategory"))
        presentation = normalize_scope(raw.get("presentation"))
        rule = None
        # Four dictionary probes, independent of the catalogue/rule count.
        for key in ((category, subcategory, presentation), (category, subcategory, ""),
                    (category, "", presentation), (category, "", "")):
            rule = self._index.get(key)
            if rule is not None:
                break
        if rule is None:
            return result
        detail = result["category_check"]
        detail.update(
            status="within_range", rule_id=rule.get("id"), rule_version=rule.get("version", 1),
            scope={key: rule[key] for key in ("category", "subcategory", "presentation")},
            note=rule["note"],
        )
        scope_text = (f"categoría={rule['category']}; "
                      f"subcategoría={rule['subcategory'] or 'todas (ámbito general)'}; "
                      f"presentación={rule['presentation'] or 'todas (ámbito general)'}")
        prefix = f"Regla #{rule.get('id', 'sin id')} v{rule.get('version', 1)} [{scope_text}]"
        missing = False
        outside = False
        for metric, field_prefix, label in (("real_kg", "real", "peso real"),
                                            ("volume_kg", "volume", "peso volumétrico")):
            lower = rule["_limits"][field_prefix + "_min_kg"]
            upper = rule["_limits"][field_prefix + "_max_kg"]
            if lower is None and upper is None:
                continue
            check = {"metric": metric, "value": None,
                     "min": rule[field_prefix + "_min_kg"],
                     "max": rule[field_prefix + "_max_kg"], "result": "missing"}
            detail["checks"].append(check)
            try:
                value = parse_number(calc.get(metric), label)
                if value <= 0:
                    raise ValidationError("El valor calculado no es positivo.")
            except ValidationError:
                missing = True
                result["observations"].append(
                    f"{prefix}: no hay {label} calculable para contrastar el rango; no se imputó un valor."
                )
                code = "category_" + field_prefix + "_missing"
                if code not in result["observation_codes"]:
                    result["observation_codes"].append(code)
                continue
            check["value"] = _decimal_text(value)
            if lower is not None and value < lower:
                check["result"] = "below_min"
                code = "category_" + field_prefix + "_below_min"
                description = f"{label} de {_decimal_text(value)} kg inferior al mínimo de {_decimal_text(lower)} kg"
            elif upper is not None and value > upper:
                check["result"] = "above_max"
                code = "category_" + field_prefix + "_above_max"
                description = f"{label} de {_decimal_text(value)} kg superior al máximo de {_decimal_text(upper)} kg"
            else:
                check["result"] = "within_range"
                continue
            outside = True
            if code not in result["reason_codes"]:
                result["reason_codes"].append(code)
            result["flags"].append(
                f"{prefix}: {description}. Sospecha para revisión; el rango no determina un valor correcto."
            )
        detail["status"] = "outside_range" if outside else ("insufficient_data" if missing else "within_range")
        if outside and result.get("status") == "valid":
            result["status"] = "review"
        return result
