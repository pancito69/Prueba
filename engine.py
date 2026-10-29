"""Deterministic logistics rules; no network requests or estimated measurements.

All dimensions are centimetres and recorded real weight is grams. Classification
uses exact decimal arithmetic and deliberately leaves the literal table's gaps
unclassified. ``upper_bounds`` is a separate, explicit business-policy choice.

Reference matching is intentionally conservative. A matching identity means the
same declared variant, presentation and shipping packaging; it is not proof that
the declared data or an external source is correct. The application must obtain
an explicit attestation before importing rows as verified references.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import re
import unicodedata
from urllib.parse import urlsplit


BANDS = [
    {"min": "0", "max": "0.5", "size": "XXXS"},
    {"min": "0.51", "max": "1", "size": "XXS"},
    {"min": "1.1", "max": "3", "size": "XS"},
    {"min": "3.1", "max": "8", "size": "S"},
    {"min": "8.1", "max": "25", "size": "M"},
    {"min": "25.1", "max": "40", "size": "L"},
    {"min": "40.1", "max": "50", "size": "XL"},
    {"min": "50.1", "max": "260", "size": "XXL"},
    {"min": "260.1", "max": "300", "size": "XXXL"},
    {"min": "300.1", "max": "500", "size": "XXXXL"},
]


class ValidationError(ValueError):
    """The supplied record cannot safely be calculated."""


def parse_number(value, field: str = "valor") -> Decimal:
    """Parse an ungrouped decimal number, accepting a decimal comma or point.

    A single comma is defined as a decimal separator, never a thousands separator.
    Formats mixing separators or containing grouping spaces are rejected rather
    than guessing the locale. Missing values, booleans, infinities and NaN fail.
    """
    if value is None or isinstance(value, bool):
        raise ValidationError(f"{field}: falta un número válido.")
    raw = str(value).strip()
    if not raw:
        raise ValidationError(f"{field}: falta un número válido.")
    if isinstance(value, Decimal):
        number = value
    else:
        if len(raw) > 120 or not re.fullmatch(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)", raw):
            raise ValidationError(
                f"{field}: usa un número decimal sin separadores de miles."
            )
        try:
            number = Decimal(raw.replace(",", "."))
        except InvalidOperation as exc:
            raise ValidationError(f"{field}: número inválido.") from exc
    if not number.is_finite():
        raise ValidationError(f"{field}: debe ser un número finito.")
    # Bound adversarial Decimal exponents while preserving normal measurements.
    if abs(number.adjusted()) > 100 or len(number.as_tuple().digits) > 120:
        raise ValidationError(f"{field}: número fuera de rango.")
    return number


def _text(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def calculate(weight_g, height_cm, length_cm, width_cm, rule_mode="strict") -> dict:
    """Calculate shipping weight and size without rounding boundary decisions.

    Invalid physical input raises ValidationError. Valid measurements that land
    in a gap or exceed 500 kg return ``pending`` with no invented size. Review
    flags are alerts only and never authorize changing the source measurements.
    """
    if rule_mode not in {"strict", "upper_bounds"}:
        raise ValidationError("La política de tallas no es válida.")
    fields = {
        "weight_g": (weight_g, "Peso real (gr)"),
        "height_cm": (height_cm, "Alto (cm)"),
        "length_cm": (length_cm, "Largo (cm)"),
        "width_cm": (width_cm, "Ancho (cm)"),
    }
    numbers = {}
    for key, (value, label) in fields.items():
        number = parse_number(value, label)
        if number <= 0:
            raise ValidationError(f"{label}: debe ser mayor que cero.")
        numbers[key] = number
    # Products and /5000 have finite decimal expansions. Choose enough precision
    # for all digits/exponents so Decimal's ambient context cannot round a band.
    precision = max(50, sum(len(n.as_tuple().digits) + abs(n.as_tuple().exponent)
                            for n in numbers.values()) + 20)
    with localcontext() as context:
        context.prec = precision
        real = numbers["weight_g"] / Decimal(1000)
        volume = (numbers["height_cm"] * numbers["length_cm"]
                  * numbers["width_cm"] / Decimal(5000))
        charge = max(real, volume)
        size = None
        for band in BANDS:
            if charge <= Decimal(band["max"]) and (
                rule_mode == "upper_bounds" or charge >= Decimal(band["min"])
            ):
                size = band["size"]
                break
        flags = []
        if size is None:
            flags.append(
                "Peso superior a 500 kg: requiere una regla de excepción."
                if charge > Decimal(500)
                else "Peso en un hueco de la tabla literal: falta definir el criterio de clasificación."
            )
        if volume >= real * Decimal(100):
            flags.append(
                "El peso volumétrico es al menos 100 veces el real: revisar unidades y empaque."
            )
        if any(numbers[key] > Decimal(300) for key in ("height_cm", "length_cm", "width_cm")):
            flags.append("Alguna dimensión supera 300 cm: verificar el empaque de envío.")
        return {
            **{key: _text(number) for key, number in numbers.items()},
            "real_kg": _text(real),
            "volume_kg": _text(volume),
            "charge_kg": _text(charge),
            "size": size,
            "flags": flags,
            "status": "pending" if size is None else ("review" if flags else "valid"),
        }


def _normalized(value) -> str:
    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())
    if text in {"-", "?", "n/a", "na", "none", "null", "nan", "sin datos", "sin dato",
                "desconocido", "desconocida", "no informado", "no informada", "pendiente"}:
        return ""
    return text


def normalize_header(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", text.casefold())


_ALIASES = {
    "seller": {"seller", "sellerid", "idseller", "vendedor", "idvendedor", "nombreseller", "nombrevendedor"},
    "sku": {"sku", "sellersku", "skuseller", "codigosku", "skuactivo"},
    "name": {"name", "nombre", "producto", "nombreproducto", "descripcion", "titulo"},
    "gtin": {"gtin", "ean", "ean13", "ean14", "upc", "codigoean", "codigobarras", "barcode"},
    "brand": {"brand", "marca"},
    "model": {"model", "modelo", "codigofabricante", "mpn"},
    "variant": {"variant", "variante", "variacion"},
    "presentation": {"presentation", "presentacion", "formato", "pack"},
    "packaging": {"packaging", "empaque", "embalaje", "empaquedespacho"},
    "weight_g": {"weightg", "pesogr", "pesog", "pesorealgr", "pesorealg", "pesoengramos", "pesorealgramos", "pesogramos"},
    "height_cm": {"heightcm", "alto", "altocm", "altura", "alturacm"},
    "length_cm": {"lengthcm", "largo", "largocm", "longitud", "longitudcm"},
    "width_cm": {"widthcm", "ancho", "anchocm"},
    "reference_verified": {"referenceverified", "referenciaverificada", "verificado", "fuenteverificada"},
    "source_url": {"sourceurl", "fuenteurl", "urlfuente", "fuente", "enlacefuente"},
    "evidence_kind": {"evidencekind", "tipomedida", "tipoevidencia", "tipodemedida"},
}


def suggest_mapping(headers) -> dict:
    """Suggest only unambiguous known header names; the user confirms mapping."""
    result = {}
    for canonical, aliases in _ALIASES.items():
        candidates = [header for header in headers
                      if normalize_header(header) in aliases | {normalize_header(canonical)}]
        if len(candidates) == 1:
            result[canonical] = candidates[0]
    return result


def _gtin(value) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    raw = str(value).strip()
    if not re.fullmatch(r"\d{8}|\d{12}|\d{13}|\d{14}", raw):
        return None
    digits = [int(char) for char in raw]
    total = sum(number * (3 if index % 2 == 0 else 1)
                for index, number in enumerate(reversed(digits[:-1])))
    if (10 - total % 10) % 10 != digits[-1]:
        return None
    if set(raw) == {"0"}:
        return None
    return raw.zfill(14)


def identity_key(record: dict) -> str | None:
    """Conservative same-product/presentation/packaging matching key.

    Variant, presentation and packaging must be explicitly supplied (``única``
    is acceptable for a product without variants). GTIN is checksum-validated.
    Brand/model, when present, remain in the GTIN key to avoid silent conflicts.
    A supplied invalid GTIN blocks matching rather than being ignored.
    """
    context = {field: _normalized(record.get(field))
               for field in ("variant", "presentation", "packaging")}
    if not all(context.values()):
        return None
    brand = _normalized(record.get("brand"))
    model = _normalized(record.get("model"))
    supplied_gtin = str(record.get("gtin") or "").strip()
    if supplied_gtin:
        code = _gtin(supplied_gtin)
        if code is None:
            return None
        product = {"gtin": code, "brand": brand, "model": model}
    elif brand and model:
        product = {"brand": brand, "model": model}
    else:
        return None
    raw = json.dumps({**product, **context}, ensure_ascii=False, sort_keys=True)
    return sha256(raw.encode("utf-8")).hexdigest()


_SOURCE_FIELDS = (
    "seller", "sku", "name", "gtin", "brand", "model", "variant", "presentation",
    "packaging", "weight_g", "height_cm", "length_cm", "width_cm",
    "reference_verified", "source_url", "evidence_kind",
)
_NUMBER_FIELDS = {"weight_g", "height_cm", "length_cm", "width_cm"}


def canonical_hash(record: dict) -> str:
    """Hash source identity, measurements and evidence for stale-change checks."""
    normalized = {}
    for field in _SOURCE_FIELDS:
        value = record.get(field)
        if field in _NUMBER_FIELDS:
            try:
                normalized[field] = _text(parse_number(value, field))
            except ValidationError:
                normalized[field] = str(value or "").strip()
        elif field == "reference_verified":
            normalized[field] = _truth(value)
        else:
            normalized[field] = str(value or "").strip()
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _truth(value) -> bool:
    return value is True or str(value).strip().casefold() in {"1", "true", "si", "sí", "yes", "verificado"}


def trusted_reference(record: dict) -> bool:
    """Eligibility only; the importer must separately obtain user attestation."""
    if not _truth(record.get("reference_verified")) or not identity_key(record):
        return False
    if normalize_header(record.get("evidence_kind")) not in {"empaqueenvio", "medicionlogistica"}:
        return False
    try:
        url = urlsplit(str(record.get("source_url") or "").strip())
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            return False
        _ = url.port  # reject malformed ports
    except ValueError:
        return False
    return True
