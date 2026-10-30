"""Auditable category-range suggestions from an explicit catalogue snapshot.

This module does not modify physical measurements, sizes or category rules.
Statistics describe the supplied catalogue; neither seller counts nor agreement
certify independent sources. Applying a suggestion remains an operator action.
Transactions belong to the caller. A progress callback may commit run progress;
temporary population tables remain connection-local and cannot affect the live
catalogue. A changed fingerprint invalidates the run rather than mixing sources.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from hashlib import sha256
import json
import math
import statistics

from categories import normalize_scope
from engine import ValidationError, parse_number


_METRICS = {"real": "real_kg", "volume": "volume_kg"}
_NULL_BOUNDS = {"real_min_kg": None, "real_max_kg": None,
                "volume_min_kg": None, "volume_max_kg": None}
_STATES = ("ready", "insufficient", "degenerate", "heterogeneous", "existing_manual")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS profile_runs (
      id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued',
      progress INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,
      config_json TEXT NOT NULL DEFAULT '{}',dataset_hash TEXT,
      summary_json TEXT NOT NULL DEFAULT '{}',error TEXT,
      created_at TEXT NOT NULL,finished_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS profile_suggestions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL REFERENCES profile_runs(id),
      scope TEXT NOT NULL,category TEXT NOT NULL,subcategory TEXT NOT NULL DEFAULT '',
      presentation TEXT NOT NULL DEFAULT '',
      state TEXT NOT NULL CHECK(state IN ('ready','insufficient','degenerate','heterogeneous','existing_manual','applied')),
      source TEXT NOT NULL CHECK(source IN ('catalog_statistics','declared_references')),
      counts_json TEXT NOT NULL,bounds_json TEXT NOT NULL,method_json TEXT NOT NULL,
      reason TEXT NOT NULL,applied_rule_id INTEGER,
      UNIQUE(run_id,scope)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_profile_suggestions_run_state ON profile_suggestions(run_id,state,id)")
    c.execute("""CREATE TABLE IF NOT EXISTS category_profile_applications (
      rule_id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,suggestion_id INTEGER NOT NULL,
      rule_version INTEGER NOT NULL
    )""")


def fingerprint(c, settings) -> str:
    """Hash ordered source rows, approval state and calculation settings."""
    digest = sha256(b"category-profile-v1\n")
    # Accept both get_settings() and SELECT * rows without hashing an incidental
    # singleton primary key or unrelated settings that the caller did not select.
    values = dict(settings)
    calculation_settings = {key: values.get(key) for key in
                            ("rule_mode", "version", "volume_ratio_threshold", "engine_revision")}
    digest.update((_json(calculation_settings) + "\n").encode("utf-8"))
    for row in c.execute("SELECT id,raw_hash FROM skus ORDER BY id"):
        digest.update((_json(list(row)) + "\n").encode("utf-8"))
    digest.update(b"approvals\n")
    for row in c.execute("""SELECT id,base_hash,candidate_hash,state,rule_version
      FROM proposals WHERE state='approved' ORDER BY id"""):
        digest.update((_json(list(row)) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _config(config):
    if not isinstance(config, dict):
        raise ValueError("La configuración del perfil debe ser un objeto.")
    result = dict(config)
    for field, minimum, default in (("min_count", 30, 30), ("min_sellers", 3, 3)):
        value = config.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 1000000:
            raise ValueError(f"{field}: use un entero entre {minimum} y 1.000.000.")
        result[field] = value
    # These method definitions are fixed so changing a support preference cannot
    # silently turn the proposed range into a different statistical procedure.
    for key, required in (("iqr_multiplier", 3), ("dominant_fraction", 0.8), ("max_log_iqr", 2)):
        if key in config and config[key] != required:
            raise ValueError(f"El método utiliza {key}={required}.")
        result[key] = required
    return result


def _document(raw):
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _metric(value):
    try:
        number = parse_number(value)
        return _text(number) if 0 < number <= Decimal(500) else None
    except (ValidationError, TypeError):
        return None


def _independent_metrics(raw, calc, approved=False):
    """Keep usable real weight when dimensions fail, and conversely.

    The application's classification requires all physical inputs together.
    Statistical support is per metric, so an absent cached metric may still be
    calculated exactly from its own original inputs. Approved rows use their
    effective calculation only; original and corrected measurements never mix.
    """
    real = _metric(calc.get("real_kg"))
    volume = _metric(calc.get("volume_kg"))
    if approved:
        return real, volume
    if calc.get("real_kg") is None:
        try:
            weight = parse_number(raw.get("weight_g"))
            if weight > 0:
                with localcontext() as context:
                    context.prec = max(50, len(weight.as_tuple().digits) + 10)
                    real = _metric(weight / Decimal(1000))
        except (ValidationError, TypeError):
            pass
    if calc.get("volume_kg") is None:
        try:
            dimensions = [parse_number(raw.get(key)) for key in
                          ("height_cm", "length_cm", "width_cm")]
            if all(number > 0 for number in dimensions):
                with localcontext() as context:
                    context.prec = max(50, sum(len(number.as_tuple().digits)
                                              + abs(number.as_tuple().exponent)
                                              for number in dimensions) + 10)
                    volume = _metric(dimensions[0] * dimensions[1] * dimensions[2] / Decimal(5000))
        except (ValidationError, TypeError):
            pass
    return real, volume


def _settings(c):
    cursor = c.execute("SELECT * FROM settings WHERE id=1")
    row = cursor.fetchone()
    if row is None:
        raise ValueError("No existe configuración de cálculo para perfilar.")
    return dict(zip([column[0] for column in cursor.description], row))


def _manual_scopes(c):
    """Protect overlapping manual scopes, including intentionally disabled ones."""
    result = defaultdict(list)
    for row in c.execute("""SELECT r.category_key,r.subcategory_key,r.presentation_key
      FROM category_rules r LEFT JOIN category_profile_applications owner
        ON owner.rule_id=r.id AND owner.rule_version=r.version
      WHERE owner.rule_id IS NULL"""):
        result[row[0]].append((row[1], row[2]))
    return result


def _manual_overlap(scope, manual):
    category, subcategory, presentation = scope
    return any((not subcategory or not other_sub or subcategory == other_sub)
               and (not presentation or not other_pres or presentation == other_pres)
               for other_sub, other_pres in manual.get(category, ()))


def _new_cohort():
    return {"values": [], "sellers": set(), "identified_support": 0,
            "unidentified_support": 0, "conflicting_entities": 0,
            "invalid_rows": 0, "valid_rows": 0}


def _append_entity(cohort, value_count, value, sellers_json, identified, invalid_rows, valid_rows):
    cohort["invalid_rows"] += invalid_rows
    cohort["valid_rows"] += valid_rows
    if value_count > 1:
        cohort["conflicting_entities"] += 1
        return
    if value_count != 1:
        return
    cohort["values"].append(value)
    cohort["sellers"].update(seller for seller in json.loads(sellers_json) if seller is not None)
    cohort["identified_support" if identified else "unidentified_support"] += 1


def _population(c, scope):
    """Aggregate each entity once per metric/cohort; conflicting values abstain."""
    category, subcategory, presentation = scope
    clauses, parameters = ["category_key=?"], [category]
    if subcategory:
        clauses.append("subcategory_key=?")
        parameters.append(subcategory)
    if presentation:
        clauses.append("presentation_key=?")
        parameters.append(presentation)
    # Blank scope fields deliberately do not filter population: a wildcard rule
    # must be trained on all records to which that wildcard would apply.
    select = ["entity", "MAX(identified)", "COUNT(*)", "json_group_array(DISTINCT seller)"]
    for metric in _METRICS:
        field = metric + "_value"
        for anchored in (False, True):
            predicate = "anchor=1 AND " if anchored else ""
            eligible = f"CASE WHEN {predicate}{field} IS NOT NULL THEN {field} END"
            sellers = f"CASE WHEN {predicate}{field} IS NOT NULL THEN seller END"
            invalid = f"CASE WHEN {predicate}{field} IS NULL THEN 1 ELSE 0 END"
            valid = f"CASE WHEN {predicate}{field} IS NOT NULL THEN 1 ELSE 0 END"
            select.extend((f"COUNT(DISTINCT {eligible})", f"MIN({eligible})",
                           f"json_group_array(DISTINCT {sellers})", f"SUM({invalid})", f"SUM({valid})"))
    query = "SELECT " + ",".join(select) + " FROM temp.profile_population WHERE " + " AND ".join(clauses) + " GROUP BY entity"
    stats = {metric: {"catalog_statistics": _new_cohort(), "declared_references": _new_cohort()}
             for metric in _METRICS}
    counts = {"rows": 0, "entities": 0, "identified_entities": 0, "unidentified_entities": 0}
    sellers = set()
    for row in c.execute(query, parameters):
        identified = bool(row[1])
        counts["rows"] += row[2]
        counts["entities"] += 1
        counts["identified_entities" if identified else "unidentified_entities"] += 1
        sellers.update(json.loads(row[3]))
        index = 4
        for metric in _METRICS:
            for source in ("catalog_statistics", "declared_references"):
                value_count, value, seller_json, invalid, valid = row[index:index + 5]
                _append_entity(stats[metric][source], value_count, value, seller_json,
                               identified, invalid, valid)
                index += 5
    counts["sellers"] = len(sellers)
    return counts, stats


def _outward_bound(value, upper=False):
    """Two significant decimal digits, rounded outward, within the domain."""
    with localcontext() as context:
        context.prec = 50
        number = Decimal(str(value))
        step = Decimal(1).scaleb(number.adjusted() - 1)
        rounded = number.quantize(step, rounding=ROUND_CEILING if upper else ROUND_FLOOR)
        return _text(min(Decimal(500), rounded))


def _describe_metric(cohorts, config):
    anchors = cohorts["declared_references"]
    source = "declared_references" if len(anchors["values"]) >= config["min_count"] else "catalog_statistics"
    selected = cohorts[source]
    count = len(selected["values"])
    method = {"state": "insufficient", "source": source, "support_count": count,
              "seller_count": len(selected["sellers"]), "q1_log10": None,
              "q3_log10": None, "log_iqr": None, "mode_fraction": None,
              "fence_min_kg": None, "fence_max_kg": None}
    counts = {"selected_source": source, "support_count": count,
              "seller_count": len(selected["sellers"]),
              "identified_support": selected["identified_support"],
              "unidentified_support": selected["unidentified_support"],
              "conflicting_entities": selected["conflicting_entities"],
              "invalid_rows": selected["invalid_rows"],
              "catalog_support": len(cohorts["catalog_statistics"]["values"]),
              "anchor_support": len(anchors["values"]),
              "catalog_conflicting_entities": cohorts["catalog_statistics"]["conflicting_entities"],
              "anchor_conflicting_entities": anchors["conflicting_entities"]}
    if count < config["min_count"]:
        method["reason"] = f"Soporte {count}, inferior al mínimo de {config['min_count']} entidades."
        return method, counts, (None, None)
    if source == "catalog_statistics" and len(selected["sellers"]) < config["min_sellers"]:
        method["reason"] = f"Catálogo provisional con menos de {config['min_sellers']} sellers en la muestra utilizable."
        return method, counts, (None, None)
    frequencies = Counter(selected["values"])
    mode_fraction = max(frequencies.values()) / count
    logs = [math.log10(float(value)) for value in selected["values"]]
    q1, _, q3 = statistics.quantiles(logs, n=4, method="inclusive")
    iqr = q3 - q1
    method.update(q1_log10=q1, q3_log10=q3, log_iqr=iqr, mode_fraction=mode_fraction)
    if iqr <= 0 or mode_fraction >= 0.8:
        method["state"] = "degenerate"
        method["reason"] = "Dispersión intercuartílica nula o un valor domina al menos el 80% de la muestra."
        return method, counts, (None, None)
    if iqr > 2:
        method["state"] = "heterogeneous"
        method["reason"] = "El IQR log10 supera 2: población demasiado heterogénea para recomendar este rango."
        return method, counts, (None, None)
    # The supported physical input parser admits no exponent below -100.
    lower = math.pow(10, max(-100.0, q1 - 3 * iqr))
    upper = math.pow(10, min(math.log10(500), q3 + 3 * iqr))
    minimum, maximum = _outward_bound(lower), _outward_bound(upper, upper=True)
    method.update(state="ready", fence_min_kg=minimum, fence_max_kg=maximum,
                  reason="Rango exterior de 3 IQR en log10, redondeado hacia fuera; solo sirve para alertas.")
    return method, counts, (minimum, maximum)


def generate(c, run_id, config, progress_callback=None) -> dict:
    """Generate suggestions, preserving measurements, rules and approvals.

    Progress is work units out of twice the catalogue row count: reading the
    population is the first half, profiling existing scopes is the second half.
    ``counts_json.rows`` always contains actual rows in a scope, not work units.
    The caller must roll back on exceptions, or explicitly mark the run failed.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Indique un identificador de ejecución.")
    config = _config(config)
    settings = _settings(c)
    initial_hash = fingerprint(c, settings)
    total_rows = c.execute("SELECT COUNT(*) FROM skus").fetchone()[0]
    total_work = total_rows * 2
    if c.execute("SELECT 1 FROM profile_runs WHERE id=?", (run_id,)).fetchone():
        if c.execute("SELECT 1 FROM profile_suggestions WHERE run_id=? AND applied_rule_id IS NOT NULL LIMIT 1", (run_id,)).fetchone():
            raise ValueError("Esta ejecución ya tiene rangos aplicados; cree una nueva para conservar el historial.")
        c.execute("DELETE FROM profile_suggestions WHERE run_id=?", (run_id,))
        c.execute("""UPDATE profile_runs SET status='running',progress=0,total=?,config_json=?,
          dataset_hash=NULL,summary_json='{}',error=NULL,finished_at=NULL WHERE id=?""",
                  (total_work, _json(config), run_id))
    else:
        c.execute("""INSERT INTO profile_runs(id,status,progress,total,config_json,created_at)
          VALUES (?,'running',0,?,?,?)""", (run_id, total_work, _json(config), _now()))
    c.execute("DROP TABLE IF EXISTS temp.profile_population")
    c.execute("""CREATE TEMP TABLE profile_population (
      sku_id INTEGER PRIMARY KEY,category_key TEXT,subcategory_key TEXT,presentation_key TEXT,
      entity TEXT,seller TEXT,identified INTEGER,real_value TEXT,volume_value TEXT,anchor INTEGER
    )""")
    scopes = {}
    missing_category = 0
    scanned = 0
    pending = []

    def report(progress):
        c.execute("UPDATE profile_runs SET progress=?,total=? WHERE id=?", (progress, total_work, run_id))
        if progress_callback:
            progress_callback(progress, total_work)

    cursor = c.execute("""SELECT id,seller,identity_key,raw_json,calculated,is_reference,
      effective_approved_id,effective_approved_calculated FROM sku_analysis ORDER BY id""")
    for row in cursor:
        sku_id, seller, identity, raw_json, calculated, is_reference, approval_id, approved_calc = row
        raw = _document(raw_json)
        category = normalize_scope(raw.get("category"))
        if not category:
            missing_category += 1
        else:
            subcategory = normalize_scope(raw.get("subcategory"))
            presentation = normalize_scope(raw.get("presentation"))
            scope = (category, subcategory, presentation)
            if scope not in scopes:
                scopes[scope] = tuple(str(raw.get(field) or "").strip()
                                      for field in ("category", "subcategory", "presentation"))
            calc = _document(approved_calc) if approval_id is not None else _document(calculated)
            real, volume = _independent_metrics(raw, calc, approved=approval_id is not None)
            identified = bool(identity)
            entity = "product:" + str(identity) if identified else "sku:" + str(sku_id)
            pending.append((sku_id, *scope, entity, str(seller), int(identified),
                            real, volume,
                            int(bool(is_reference) or approval_id is not None)))
        scanned += 1
        if scanned % 2000 == 0:
            c.executemany("INSERT INTO temp.profile_population VALUES(?,?,?,?,?,?,?,?,?,?)", pending)
            pending.clear()
            report(scanned)
    if pending:
        c.executemany("INSERT INTO temp.profile_population VALUES(?,?,?,?,?,?,?,?,?,?)", pending)
    report(scanned)
    c.execute("CREATE INDEX temp.idx_profile_population_sub ON profile_population(category_key,subcategory_key,presentation_key,entity)")
    c.execute("CREATE INDEX temp.idx_profile_population_pres ON profile_population(category_key,presentation_key,subcategory_key,entity)")
    manual = _manual_scopes(c)
    summary = {"total_scopes": len(scopes), **{state: 0 for state in _STATES},
               "applied": 0, "rows": total_rows, "missing_category_rows": missing_category,
               "dataset_hash": None,
               "notes": ["Los rangos estadísticos son propuestas para alertas; no son medidas correctas ni datos físicamente certificados.",
                         "El número de sellers no demuestra independencia de fuentes.",
                         "Los campos vacíos del ámbito cubren toda la población compatible; no solo filas con ese campo vacío."]}
    for index, (scope, display_scope) in enumerate(sorted(scopes.items()), 1):
        counts, populations = _population(c, scope)
        bounds = dict(_NULL_BOUNDS)
        methods = {}
        metric_counts = {}
        for metric in _METRICS:
            methods[metric], metric_counts[metric], (minimum, maximum) = _describe_metric(populations[metric], config)
            bounds[metric + "_min_kg"], bounds[metric + "_max_kg"] = minimum, maximum
        recommended = [metric for metric in _METRICS if methods[metric]["state"] == "ready"]
        source = ("declared_references" if recommended and all(methods[metric]["source"] == "declared_references" for metric in recommended)
                  else "catalog_statistics")
        if _manual_overlap(scope, manual):
            state = "existing_manual"
            bounds = dict(_NULL_BOUNDS)
            reason = "El ámbito se solapa con una regla manual o editada por el operador; no se propone reemplazarla."
        elif recommended:
            state = "ready"
            reason = ("Rango propuesto con soporte declarado: requiere revisión y aplicación explícita."
                      if source == "declared_references" else
                      "Rango provisional del catálogo: requiere revisión y aplicación explícita; no certifica sus datos.")
        elif any(method["state"] == "heterogeneous" for method in methods.values()):
            state = "heterogeneous"
            reason = "Sin rango recomendable: la población utilizable es demasiado heterogénea."
        elif any(method["state"] == "degenerate" for method in methods.values()):
            state = "degenerate"
            reason = "Sin rango recomendable: dispersión nula o concentración dominante en las métricas utilizables."
        else:
            state = "insufficient"
            reason = "No hay soporte suficiente de entidades y sellers para recomendar un rango."
        counts["metrics"] = metric_counts
        supports = [metric_counts[metric]["support_count"] for metric in recommended]
        counts["support_count"] = min(supports) if supports else max(metric_counts[metric]["support_count"] for metric in _METRICS)
        for metric in _METRICS:
            counts[metric + "_support_count"] = metric_counts[metric]["support_count"]
        warnings = ["Estadística del catálogo; no autoriza corregir pesos, dimensiones, tallas ni cobros.",
                    "Sellers distintos pueden compartir el mismo origen de datos."]
        if counts["unidentified_entities"]:
            warnings.append("Las filas sin identidad conservadora se cuentan como entidades SKU; no equivalen a productos únicos verificados.")
        if not scope[1] or not scope[2]:
            warnings.append("El ámbito contiene un comodín: la muestra incluye todos los valores cubiertos por ese campo vacío.")
        if any(metric_counts[metric]["catalog_conflicting_entities"] or metric_counts[metric]["anchor_conflicting_entities"] for metric in _METRICS):
            warnings.append("Las entidades con valores contradictorios se excluyen por métrica dentro de cada cohorte; no se inventa una mediana física.")
        if any(methods[metric]["state"] != "ready" for metric in _METRICS):
            warnings.append("Cada métrica se evalúa por separado; una métrica sin soporte no recibe límites imputados.")
        method = {"method": "log10_tukey_outer_fences", "version": 1, "iqr_multiplier": 3,
                  "quartile_method": "inclusive_linear_interpolation", "rounding": "two_significant_digits_outward",
                  "min_count": config["min_count"], "min_sellers": config["min_sellers"],
                  "dominant_fraction": 0.8, "max_log_iqr": 2, "domain_kg": ">0 and <=500",
                  "support_unit": "Conservative product identity, or individual SKU when identity is missing",
                  "metrics": methods, "warnings": warnings}
        c.execute("""INSERT INTO profile_suggestions(run_id,scope,category,subcategory,presentation,
          state,source,counts_json,bounds_json,method_json,reason)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (run_id, _json(list(scope)), *display_scope, state, source,
                                           _json(counts), _json(bounds), _json(method), reason))
        summary[state] += 1
        report(total_rows + (total_rows * index // len(scopes)))
    final_hash = fingerprint(c, _settings(c))
    if final_hash != initial_hash:
        raise ValueError("El catálogo, sus aprobaciones o las reglas de cálculo cambiaron durante el perfilado. Genere una nueva ejecución.")
    summary["dataset_hash"] = final_hash
    c.execute("""UPDATE profile_runs SET status='completed',progress=total,dataset_hash=?,
      summary_json=?,error=NULL,finished_at=? WHERE id=?""", (final_hash, _json(summary), _now(), run_id))
    report(total_work)
    c.execute("DROP TABLE temp.profile_population")
    return summary
