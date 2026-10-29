"""Explainable diagnostics and exact-evidence review groups for the local pilot.

``sku_analysis`` is the application's current, validity-filtered SQL view.
This module never downloads sources and never treats a calculated size as a
measurement verified in the physical world. Aggregations run in SQLite rather
than loading the whole catalogue into Python.
"""
from __future__ import annotations

import json


REASON_LABELS = {
    "invalid_physical_data": "Peso o dimensiones faltantes, no numéricos o no positivos",
    "table_gap": "Peso en un hueco de la tabla literal",
    "above_table": "Peso de clasificación superior a 500 kg",
    "volume_weight_ratio": "Relación volumétrico/real supera el umbral de alerta",
    "proposal_waiting": "Propuesta con evidencia pendiente de aprobación",
    "billed_size_invalid": "Talla de cobro declarada no reconocida",
    "billed_size_mismatch": "Talla declarada distinta de la calculada; requiere contrastar datos",
    "category_real_below_min": "Peso real por debajo del mínimo declarado para su categoría",
    "category_real_above_max": "Peso real por encima del máximo declarado para su categoría",
    "category_volume_below_min": "Peso volumétrico por debajo del mínimo declarado para su categoría",
    "category_volume_above_max": "Peso volumétrico por encima del máximo declarado para su categoría",
}

DIRECTIONS = ("up", "down", "same", "unknown")
VERIFICATIONS = ("unverified", "declared_reference", "evidence_registered", "approved")
MAX_GROUP_MEMBERS = 500

# Deliberately includes evidence and reference identity, not just product names.
# Callers validating a batch use the same expression with aliases s and p.
# A missing identity always gets a per-SKU group, never an inferred grouping.
GROUP_KEY_SQL = """json_array(
  CASE WHEN s.identity_key IS NULL OR s.identity_key='' THEN 'sku:' || s.id
       ELSE 'product:' || s.identity_key END,
  p.source_url,p.source_kind,p.evidence_note,p.candidate_hash,p.rule_version,
  p.reference_id,p.reference_hash,s.direction,s.comparison_from_size,
  s.comparison_to_size,s.direction_basis
)"""

_PENDING_JOIN = """FROM sku_analysis s
  JOIN proposals p ON p.id=s.analysis_proposal_id
  WHERE s.analysis_proposal_state='pending' AND p.state='pending'"""


def diagnostics(c) -> dict:
    """Return catalogue-wide facts; multiple reasons can apply to one record."""
    counts = c.execute("""SELECT COUNT(*) AS total,
      COALESCE(SUM(status IN ('review','pending')),0) AS attention_rows,
      COALESCE(SUM(status IN ('review','pending') AND identity_key IS NOT NULL AND identity_key<>''),0) AS identifiable_rows,
      COUNT(DISTINCT CASE WHEN status IN ('review','pending') AND identity_key<>'' THEN identity_key END) AS unique_products,
      COALESCE(SUM(status IN ('review','pending') AND analysis_proposal_id IS NOT NULL),0) AS with_proposal_rows
      FROM sku_analysis""").fetchone()
    result = dict(counts)
    status_counts = {r["status"]: r["n"] for r in c.execute(
        "SELECT status,COUNT(*) AS n FROM sku_analysis GROUP BY status")}
    directions = {key: 0 for key in DIRECTIONS}
    directions.update({r["direction"]: r["n"] for r in c.execute(
        "SELECT direction,COUNT(*) AS n FROM sku_analysis GROUP BY direction")})
    verifications = {key: 0 for key in VERIFICATIONS}
    verifications.update({r["verification"]: r["n"] for r in c.execute(
        "SELECT verification,COUNT(*) AS n FROM sku_analysis GROUP BY verification")})
    # DISTINCT sku id keeps a repeated reason code from counting twice.
    reason_counts = c.execute("""SELECT r.value AS code,COUNT(DISTINCT s.id) AS n
      FROM sku_analysis s,json_each(s.reason_codes) r
      WHERE s.status IN ('review','pending') GROUP BY r.value
      ORDER BY n DESC,r.value""")
    reasons = [{"code": r["code"], "label": REASON_LABELS.get(r["code"], r["code"]), "count": r["n"]}
               for r in reason_counts]
    return {
        "total": result["total"],
        "attention": result["attention_rows"],
        "statuses": status_counts,
        "reasons": reasons,
        "directions": directions,
        "verification": verifications,
        "research": {
            "attention_rows": result["attention_rows"],
            "identifiable_rows": result["identifiable_rows"],
            "unique_products": result["unique_products"],
            "missing_identity_rows": result["attention_rows"] - result["identifiable_rows"],
            "with_proposal_rows": result["with_proposal_rows"],
            "without_proposal_rows": result["attention_rows"] - result["with_proposal_rows"],
        },
        "ai": {"enabled": False, "external_requests": 0},
        "notes": [
            "Una talla calculada y la ausencia de alertas no certifican las medidas físicas.",
            "Un SKU puede tener varios motivos; sus conteos no deben sumarse como SKUs únicos.",
            "La dirección compara tallas según la base indicada; no calcula diferencias monetarias.",
            "Los productos identificables se agrupan por identidad declarada y contexto de empaque; esto no verifica la evidencia.",
            "La IA y la investigación externa no están conectadas en este piloto.",
        ],
    }


def _document(value, fallback):
    try:
        return json.loads(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _joined(values):
    if not isinstance(values, list):
        return ""
    return " | ".join(str(value) for value in values)


def diagnostic_rows(c, scope="attention"):
    """Stream a CSV diagnostic, preserving source data and comparison context.

    The caller handles CSV quoting and spreadsheet-formula protection. No source
    URL is fetched. Scope defaults to records needing attention, which is much
    smaller than a full catalogue export in the usual workflow.
    """
    if scope not in {"attention", "all"}:
        raise ValueError("El alcance del diagnóstico debe ser attention o all.")
    yield [
        "ID", "Seller", "SKU", "Producto", "Categoria", "Subcategoria", "Marca", "Modelo", "GTIN", "Variante",
        "Presentacion", "Empaque", "Peso (gr)", "Alto (cm)", "Largo (cm)", "Ancho (cm)",
        "Peso real (kg)", "Peso volumetrico (kg)", "Peso clasificacion (kg)",
        "Talla calculada con datos originales", "Estado", "Codigos de motivo", "Motivos",
        "Alertas", "Observaciones", "Talla de cobro declarada", "Talla comparada desde",
        "Talla comparada hasta", "Direccion", "Base de comparacion", "Estado de evidencia",
        "Identidad declarada", "Propuesta ID", "Estado de propuesta", "Fuente", "Evidencia", "Regla de categoria", "Resultado de categoria",
    ]
    where = " WHERE s.status IN ('review','pending')" if scope == "attention" else ""
    query = """SELECT s.*,p.source_url AS proposal_source_url,p.evidence_note AS proposal_evidence_note
      FROM sku_analysis s LEFT JOIN proposals p ON p.id=s.analysis_proposal_id""" + where + " ORDER BY s.id"
    for row in c.execute(query):
        raw = _document(row["raw_json"], {})
        calc = _document(row["calculated"], {})
        codes = _document(row["reason_codes"], [])
        yield [
            row["id"], row["seller"], row["sku"], row["name"], raw.get("category", ""), raw.get("subcategory", ""),
            raw.get("brand", ""), raw.get("model", ""), raw.get("gtin", ""),
            raw.get("variant", ""), raw.get("presentation", ""), raw.get("packaging", ""),
            *(raw.get(key, "") for key in ("weight_g", "height_cm", "length_cm", "width_cm")),
            calc.get("real_kg", ""), calc.get("volume_kg", ""), calc.get("charge_kg", ""),
            calc.get("size", ""), row["status"], _joined(codes),
            _joined([REASON_LABELS.get(code, code) for code in codes]),
            _joined(calc.get("flags", [])), _joined(calc.get("observations", [])),
            raw.get("billed_size", ""), row["comparison_from_size"], row["comparison_to_size"],
            row["direction"], row["direction_basis"], row["verification"], row["identity_key"],
            row["analysis_proposal_id"], row["analysis_proposal_state"],
            row["proposal_source_url"] or raw.get("source_url", ""),
            row["proposal_evidence_note"] or "",
            calc.get("category_check", {}).get("rule_id", ""), calc.get("category_check", {}).get("status", ""),
        ]


def groups(c, page=1, page_size=20, direction="all") -> dict:
    """Paginate exact-evidence groups; expose at most 500 members per group."""
    if direction not in {"all", *DIRECTIONS}:
        raise ValueError("Dirección de comparación no válida.")
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("Paginación de grupos no válida.")
    where = _PENDING_JOIN
    parameters = []
    if direction != "all":
        where += " AND s.direction=?"
        parameters.append(direction)
    query = f"""SELECT {GROUP_KEY_SQL} AS group_key,
      MIN(COALESCE(NULLIF(s.name,''),s.sku)) AS name,COUNT(*) AS member_count,
      COUNT(DISTINCT s.seller) AS seller_count,
      MIN(s.direction) AS direction,MIN(s.direction_basis) AS direction_basis,
      MIN(s.comparison_from_size) AS comparison_from_size,
      MIN(s.comparison_to_size) AS comparison_to_size,
      MIN(p.source_url) AS source_url,MIN(p.source_kind) AS source_kind,
      MIN(p.evidence_note) AS evidence_note,MIN(p.reason) AS reason
      {where} GROUP BY {GROUP_KEY_SQL}"""
    total = c.execute("SELECT COUNT(*) FROM (" + query + ")", parameters).fetchone()[0]
    selected = c.execute(query + " ORDER BY CASE direction WHEN 'up' THEN 0 WHEN 'unknown' THEN 1 WHEN 'same' THEN 2 ELSE 3 END,member_count DESC,group_key LIMIT ? OFFSET ?",
                         [*parameters, page_size, (page - 1) * page_size]).fetchall()
    items = []
    for row in selected:
        group = dict(row)
        members = c.execute(
            "SELECT p.id AS proposal_id,s.id AS sku_id,s.sku,s.seller,s.name "
            + where + f" AND {GROUP_KEY_SQL}=? ORDER BY s.id LIMIT ?",
            [*parameters, group["group_key"], MAX_GROUP_MEMBERS],
        ).fetchall()
        items.append({
            "key": group["group_key"], "name": group["name"],
            "count": group["member_count"], "seller_count": group["seller_count"],
            "proposal_ids": [member["proposal_id"] for member in members],
            "members": [dict(member) for member in members],
            "truncated": group["member_count"] > MAX_GROUP_MEMBERS,
            **{key: group[key] for key in ("direction", "direction_basis", "comparison_from_size",
                "comparison_to_size", "source_url", "source_kind", "evidence_note", "reason")},
        })
    return {
        "items": items, "total": total, "page": page,
        "pages": max(1, (total + page_size - 1) // page_size),
        "batch_limit": MAX_GROUP_MEMBERS,
        "notes": [
            "Solo se agrupan propuestas vigentes pendientes con la misma identidad, evidencia, valores propuestos y comparación de tallas.",
            "Los registros sin identidad suficiente permanecen individuales; cada lote admite hasta 500 propuestas.",
            "La aprobación de un grupo requiere revisar su evidencia y la compatibilidad del empaque.",
        ],
    }
