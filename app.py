"""Local, persistent logistics review pilot. No remote lookups or billing writes."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import queue
import sqlite3
import sys
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager, contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import BANDS, ValidationError, calculate, canonical_hash, identity_key, suggest_mapping, trusted_reference, normalize_billed_size, parse_number
from reporting import diagnostics as diagnostic_report, diagnostic_rows, groups as review_groups, GROUP_KEY_SQL
from categories import CategoryEngine, init_schema as init_category_schema, list_rules, save_rule, delete_rule, normalize_scope
from category_profiler import init_schema as init_profile_schema, generate as generate_profile, fingerprint as profile_fingerprint
from research import init_schema as init_research_schema, register_routes as register_research_routes
from startup_lock import exclusive_instance

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("TALLA_DATA_DIR", ROOT / "data"))
DB = DATA / "talla.sqlite3"
UPLOADS = DATA / "uploads"
TASKS: queue.Queue[str | None] = queue.Queue()
MUTATION = threading.Lock()
FIELDS = ["seller", "sku", "name", "gtin", "brand", "model", "variant", "presentation", "packaging", "weight_g", "height_cm", "length_cm", "width_cm", "reference_verified", "source_url", "evidence_kind", "billed_size", "category", "subcategory"]
MEASURES = ["weight_g", "height_cm", "length_cm", "width_cm"]
REQUIRED = ["seller", "sku", *MEASURES]
MAX_UPLOAD = 512 * 1024 * 1024
LOG = logging.getLogger("uvicorn.error")
LIVE_PROPOSAL_CONDITION = """candidate.base_hash=s.raw_hash AND candidate.rule_version=(SELECT version FROM settings WHERE id=1)
  AND (candidate.reference_id IS NULL OR EXISTS(SELECT 1 FROM skus r WHERE r.id=candidate.reference_id
    AND r.raw_hash=candidate.reference_hash AND r.is_reference=1 AND NOT EXISTS(SELECT 1 FROM skus other
      WHERE other.identity_key=r.identity_key AND other.is_reference=1 AND other.metrics_key<>r.metrics_key)))"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db(*, busy_timeout_ms=5000):
    try:
        _initialize_database(busy_timeout_ms)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise RuntimeError(
                f"La base de datos está ocupada: {DB.resolve()}. "
                "Cierra las otras instancias de Talla con Ctrl+C y cualquier programa "
                "que tenga abierta esta base; después inicia una sola vez. "
                "Si no encuentras el proceso, guarda tu trabajo y reinicia el equipo. "
                "No borres talla.sqlite3 ni sus archivos -wal o -shm."
            ) from exc
        raise


def _initialize_database(busy_timeout_ms):
    if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int) or not 0 <= busy_timeout_ms <= 60000:
        raise ValueError("El tiempo de espera SQLite debe estar entre 0 y 60000 ms.")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    LOG.info("Inicio de Talla: comprobando la base local %s", DB.resolve())
    with connect() as c:
        c.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        c.execute("PRAGMA journal_mode=WAL")
        LOG.info("Comprobando el esquema de la base de datos...")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), rule_mode TEXT NOT NULL, version INTEGER NOT NULL);
        INSERT OR IGNORE INTO settings(id,rule_mode,version) VALUES (1,'strict',1);
        CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, filename TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, upload_id TEXT, filename TEXT, status TEXT, processed INTEGER DEFAULT 0,
          inserted INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, unchanged INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
          error_examples TEXT DEFAULT '[]', stage TEXT, error TEXT, mapping TEXT, trust_references INTEGER DEFAULT 0, created_at TEXT, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS skus (id INTEGER PRIMARY KEY, seller TEXT NOT NULL, sku TEXT NOT NULL, name TEXT,
          identity_key TEXT, raw_json TEXT NOT NULL, raw_hash TEXT NOT NULL, metrics_key TEXT, calculated TEXT NOT NULL,
          status TEXT NOT NULL, is_reference INTEGER DEFAULT 0, reference_revoked INTEGER DEFAULT 0, job_id TEXT, updated_at TEXT, UNIQUE(seller,sku));
        CREATE INDEX IF NOT EXISTS idx_identity ON skus(identity_key,is_reference);
        CREATE INDEX IF NOT EXISTS idx_status ON skus(status);
        CREATE TABLE IF NOT EXISTS proposals (id INTEGER PRIMARY KEY, sku_id INTEGER NOT NULL REFERENCES skus(id), base_hash TEXT NOT NULL,
          candidate_hash TEXT NOT NULL, rule_version INTEGER NOT NULL, values_json TEXT NOT NULL, calculated TEXT NOT NULL,
          source_url TEXT NOT NULL, source_kind TEXT NOT NULL, evidence_note TEXT NOT NULL, reason TEXT NOT NULL,
          reference_id INTEGER, reference_hash TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT, decision_note TEXT);
        CREATE INDEX IF NOT EXISTS idx_proposals_sku ON proposals(sku_id,state);
        CREATE INDEX IF NOT EXISTS idx_proposals_reference ON proposals(reference_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending ON proposals(sku_id) WHERE state='pending';
        CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, sku_id INTEGER NOT NULL REFERENCES skus(id), action TEXT, detail TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS configuration_events (id INTEGER PRIMARY KEY, action TEXT, detail TEXT, created_at TEXT);
        """)
        if "reference_revoked" not in {row["name"] for row in c.execute("PRAGMA table_info(skus)")}:
            c.execute("ALTER TABLE skus ADD COLUMN reference_revoked INTEGER DEFAULT 0")
        columns = {row["name"] for row in c.execute("PRAGMA table_info(settings)")}
        for name, definition in [("engine_revision", "INTEGER NOT NULL DEFAULT 1"), ("volume_ratio_threshold", "TEXT NOT NULL DEFAULT '100'")]:
            if name not in columns:
                c.execute(f"ALTER TABLE settings ADD COLUMN {name} {definition}")
        c.execute("UPDATE jobs SET status='failed',error='Proceso interrumpido. Puede volver a procesar el archivo; los registros ya cargados se conservan.',finished_at=? WHERE status IN ('queued','running')", (now(),))
        init_category_schema(c)
        init_research_schema(c)
        init_profile_schema(c)
        c.execute("UPDATE profile_runs SET status='failed',error='El cálculo se interrumpió al cerrar la aplicación. Vuelve a calcular los rangos.',finished_at=? WHERE status IN ('queued','running')", (now(),))
        c.commit()
        if get_settings(c)["engine_revision"] < 3:
            # Preserve a recoverable pre-migration copy before touching existing rows.
            if c.execute("SELECT 1 FROM skus LIMIT 1").fetchone():
                backup_path = DATA / "talla.pre-v3.sqlite3"
                if not backup_path.exists():
                    LOG.info("Guardando respaldo antes de actualizar el catálogo...")
                    temporary = DATA / (".respaldo-" + uuid.uuid4().hex + ".sqlite3")
                    try:
                        with closing(sqlite3.connect(temporary)) as backup:
                            c.backup(backup)
                        os.replace(temporary, backup_path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    LOG.info("Respaldo guardado en %s", backup_path.resolve())
            upgrade_engine(c)
        LOG.info("Preparando consultas del catálogo...")
        create_analysis_view(c)
    LOG.info("Base de datos lista. La aplicación está terminando de iniciar.")


def get_settings(c):
    return dict(c.execute("SELECT rule_mode,version,volume_ratio_threshold,engine_revision FROM settings WHERE id=1").fetchone())


def upgrade_engine(c):
    """Re-evaluate heuristics without discarding original data or decisions."""
    config = get_settings(c)
    category_engine = CategoryEngine.from_db(c)
    total = c.execute("SELECT COUNT(*) FROM skus").fetchone()[0]
    processed = 0
    started = time.monotonic()
    reported = started
    LOG.info("Actualizando catálogo existente: %s SKUs. Conservando originales y aprobaciones.", f"{total:,}")
    last_id = 0
    while True:
        rows = c.execute("SELECT * FROM skus WHERE id>? ORDER BY id LIMIT 1000", (last_id,)).fetchall()
        if not rows:
            break
        for row in rows:
            raw = json.loads(row["raw_json"])
            digest = canonical_hash(raw)
            if digest != row["raw_hash"]:
                c.execute("UPDATE proposals SET base_hash=? WHERE sku_id=? AND base_hash=?", (digest, row["id"], row["raw_hash"]))
                c.execute("UPDATE proposals SET reference_hash=? WHERE reference_id=? AND reference_hash=?", (digest, row["id"], row["raw_hash"]))
            calc = calculate_record(raw, config["rule_mode"], config["volume_ratio_threshold"], category_engine)
            live = c.execute("SELECT state FROM proposals WHERE sku_id=? AND state IN ('pending','approved') ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            status = ("approved" if live["state"] == "approved" else "review") if live else calc["status"]
            c.execute("UPDATE skus SET raw_hash=?,calculated=?,status=?,is_reference=? WHERE id=?", (digest, json.dumps(calc), status, int(trusted_reference(raw) and not row["reference_revoked"] and calc["core_status"] == "valid"), row["id"]))
        last_id = rows[-1]["id"]
        processed += len(rows)
        current_time = time.monotonic()
        if current_time - reported >= 5 or processed == total:
            LOG.info("Actualizando catálogo: %s / %s SKUs (%s%%), %s segundos.", f"{processed:,}", f"{total:,}", round(processed * 100 / total) if total else 100, round(current_time - started))
            reported = current_time
    LOG.info("Actualizando propuestas y comprobando referencias...")
    for p in c.execute("SELECT p.id,p.values_json,s.raw_json FROM proposals p JOIN skus s ON s.id=p.sku_id"):
        values = json.loads(p["values_json"])
        calc = calculate_record({**json.loads(p["raw_json"]), **values}, config["rule_mode"], config["volume_ratio_threshold"], category_engine)
        c.execute("UPDATE proposals SET candidate_hash=?,calculated=? WHERE id=?", (canonical_hash(values), json.dumps(calc), p["id"]))
    c.execute("UPDATE settings SET engine_revision=3 WHERE id=1")
    generate_proposals(c, config)
    LOG.info("Actualización terminada: %s SKUs, %s segundos.", f"{processed:,}", round(time.monotonic() - started))


def create_analysis_view(c):
    sizes = [b["size"] for b in BANDS]
    valid = ",".join("'" + s + "'" for s in sizes)
    rank = lambda field: "CASE " + field + " " + " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(sizes)) + " ELSE NULL END"
    c.execute("DROP VIEW IF EXISTS sku_analysis")
    c.execute(f"""CREATE VIEW sku_analysis AS
      WITH facts AS (
        SELECT s.id,s.seller,s.sku,s.name,s.identity_key,s.raw_json,s.raw_hash,s.metrics_key,s.calculated,
          s.is_reference,s.reference_revoked,s.job_id,s.updated_at,
          CASE WHEN p.state='pending' THEN 'review' WHEN approved.id IS NOT NULL THEN 'approved'
            ELSE json_extract(s.calculated,'$.status') END AS status,
          p.id AS analysis_proposal_id,p.state AS analysis_proposal_state,p.calculated AS proposed_calculated,
          approved.id AS effective_approved_id,approved.calculated AS effective_approved_calculated,
          json_extract(s.calculated,'$.size') AS catalog_size,json_extract(p.calculated,'$.size') AS proposed_size,
          CASE WHEN UPPER(TRIM(json_extract(s.raw_json,'$.billed_size'))) IN ({valid})
            THEN UPPER(TRIM(json_extract(s.raw_json,'$.billed_size'))) ELSE NULL END AS declared_size
        FROM skus s LEFT JOIN proposals p ON p.id=(
          SELECT candidate.id FROM proposals candidate WHERE candidate.sku_id=s.id AND candidate.state IN ('pending','approved')
          AND {LIVE_PROPOSAL_CONDITION}
          ORDER BY candidate.id DESC LIMIT 1)
        LEFT JOIN proposals approved ON approved.id=(SELECT candidate.id FROM proposals candidate
          WHERE candidate.sku_id=s.id AND candidate.state='approved' AND {LIVE_PROPOSAL_CONDITION}
          ORDER BY candidate.id DESC LIMIT 1)
      ), comparisons AS (
        SELECT *,CASE WHEN declared_size IS NOT NULL THEN declared_size WHEN analysis_proposal_id IS NOT NULL THEN catalog_size ELSE NULL END AS comparison_from_size,
          CASE WHEN analysis_proposal_id IS NOT NULL THEN proposed_size WHEN declared_size IS NOT NULL THEN catalog_size ELSE NULL END AS comparison_to_size,
          CASE WHEN declared_size IS NOT NULL AND analysis_proposal_id IS NOT NULL THEN 'billed_vs_proposed'
            WHEN declared_size IS NOT NULL THEN 'billed_vs_calculated'
            WHEN analysis_proposal_id IS NOT NULL THEN 'calculated_vs_proposed' ELSE 'unknown' END AS direction_basis
        FROM facts
      ) SELECT *,CASE WHEN comparison_from_size IS NULL OR comparison_to_size IS NULL THEN 'unknown'
          WHEN {rank('comparison_to_size')} > {rank('comparison_from_size')} THEN 'up'
          WHEN {rank('comparison_to_size')} < {rank('comparison_from_size')} THEN 'down' ELSE 'same' END AS direction,
        CASE WHEN analysis_proposal_state='approved' THEN 'approved' WHEN analysis_proposal_state='pending' THEN 'evidence_registered'
          WHEN is_reference=1 THEN 'declared_reference' ELSE 'unverified' END AS verification,
        CASE WHEN analysis_proposal_state='pending' THEN json_insert(COALESCE(json_extract(calculated,'$.reason_codes'),'[]'),'$[#]','proposal_waiting')
          WHEN analysis_proposal_state='approved' THEN COALESCE(json_extract(proposed_calculated,'$.reason_codes'),'[]')
          ELSE COALESCE(json_extract(calculated,'$.reason_codes'),'[]') END AS reason_codes
      FROM comparisons""")


def audit(c, sku_id, action, detail):
    c.execute("INSERT INTO history(sku_id,action,detail,created_at) VALUES (?,?,?,?)", (sku_id, action, detail, now()))


@contextmanager
def mutating():
    if not MUTATION.acquire(blocking=False):
        raise HTTPException(409, "Hay una carga en proceso. Espere a que termine antes de modificar datos.")
    try:
        yield
    finally:
        MUTATION.release()


def inspect_xlsx(path):
    try:
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 2 * 1024**3:
                raise ValueError("El contenido descomprimido supera 2 GiB. Divida el archivo.")
    except zipfile.BadZipFile as e:
        raise ValueError("El archivo no es un XLSX válido.") from e


def read_rows(path):
    """Yield (headers, rows) from CSV or the first populated XLSX sheet."""
    if Path(path).suffix == ".xlsx":
        import openpyxl
        inspect_xlsx(path)
        book = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            sheet = book.worksheets[0]
            sheet.reset_dimensions()
            iterator = sheet.iter_rows(values_only=True)
            header = next(iterator, None)
            if not header:
                raise ValueError("La primera hoja está vacía.")
            headers = validate_headers(header)
            yield headers
            for line in iterator:
                if any(v is not None and str(v).strip() for v in line):
                    yield {h: str(v).strip() if v is not None else "" for h, v in zip(headers, line)}
        finally:
            book.close()
    else:
        # Probe only the beginning; the full catalog is always iterated.
        with open(path, "rb") as probe:
            beginning = probe.read(65536)
        try:
            beginning.decode("utf-8-sig")
            encoding = "utf-8-sig"
        except UnicodeDecodeError:
            encoding = "cp1252"
        with open(path, encoding=encoding, newline="") as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.reader(f, dialect)
            headers = validate_headers(next(reader, []))
            yield headers
            for row in reader:
                if not any(v.strip() for v in row):
                    continue
                if len(row) > len(headers):
                    yield {"__row_error__": "Hay más valores que columnas; compruebe el delimitador."}
                else:
                    yield dict(zip(headers, row))


def validate_headers(values):
    headers = [str(x).strip() if x is not None else "" for x in values]
    while headers and not headers[-1]:
        headers.pop()
    if not headers or any(not h for h in headers):
        raise ValueError("La primera fila debe contener encabezados no vacíos.")
    if len(set(headers)) != len(headers):
        raise ValueError("Hay encabezados duplicados. Use nombres de columna distintos.")
    if len(headers) > 300:
        raise ValueError("Máximo 300 columnas por archivo.")
    return headers


def calculate_record(raw, mode, volume_ratio_threshold="100", category_engine=None):
    try:
        calc = calculate(*(raw.get(k, "") for k in MEASURES), rule_mode=mode, volume_ratio_threshold=volume_ratio_threshold)
    except (ValidationError, ValueError) as e:
        calc = {"size": None, "real_kg": None, "volume_kg": None, "charge_kg": None, "flags": [str(e)], "status": "pending", "reason_codes": ["invalid_physical_data"], "observations": [], "observation_codes": []}
    if str(raw.get("billed_size") or "").strip():
        billed = normalize_billed_size(raw["billed_size"])
        if billed is None:
            calc["reason_codes"].append("billed_size_invalid")
            calc["flags"].append("La talla de cobro declarada no pertenece a la tabla de cooperados. No se supone equivalencia con la clase del Excel.")
        elif calc["size"] and billed != calc["size"]:
            calc["reason_codes"].append("billed_size_mismatch")
            calc["flags"].append(f"La talla de cobro declarada ({billed}) difiere del cálculo del catálogo ({calc['size']}). Contrastar medidas antes de cambiar el cobro.")
        if calc["reason_codes"] and calc["status"] == "valid":
            calc["status"] = "review"
    calc["core_status"] = calc["status"]
    return category_engine.apply(raw, calc) if category_engine is not None else calc


def metrics_key(raw):
    # The sorted axes prevent a mere change in orientation being treated as an error.
    from engine import parse_number
    try:
        weight = parse_number(raw.get("weight_g", ""))
        axes = sorted(parse_number(raw.get(k, "")) for k in MEASURES[1:])
        texts = [format(v, "f") for v in [weight, *axes]]
        return "|".join(text.rstrip("0").rstrip(".") if "." in text else text for text in texts)
    except ValueError:
        return ""


def job_update(c, job_id, **values):
    c.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?", [*values.values(), job_id])
    c.commit()


def process_job(job_id):
    with MUTATION, connect() as c:
        job = c.execute("SELECT j.*,u.path FROM jobs j JOIN uploads u ON u.id=j.upload_id WHERE j.id=?", (job_id,)).fetchone()
        if not job:
            return
        counters = dict(processed=0, inserted=0, updated=0, unchanged=0, errors=0)
        examples = []
        job_update(c, job_id, status="running", stage="Leyendo y validando catálogo")
        try:
            config = get_settings(c)
            category_engine = CategoryEngine.from_db(c)
            mapping = json.loads(job["mapping"])
            iterator = read_rows(job["path"])
            next(iterator)
            c.execute("CREATE TEMP TABLE seen (seller TEXT,sku TEXT,PRIMARY KEY(seller,sku))")
            for number, row in enumerate(iterator, 2):
                counters["processed"] += 1
                try:
                    if "__row_error__" in row:
                        raise ValueError(row["__row_error__"])
                    raw = {key: str(row.get(header, "")).strip() for key, header in mapping.items() if header}
                    if any(len(v) > 10000 for v in raw.values()):
                        raise ValueError("Campo demasiado largo (máximo 10.000 caracteres).")
                    if not raw.get("seller") or not raw.get("sku"):
                        raise ValueError("Falta seller o SKU.")
                    try:
                        c.execute("INSERT INTO seen VALUES (?,?)", (raw["seller"], raw["sku"]))
                    except sqlite3.IntegrityError:
                        raise ValueError("Seller + SKU repetido en esta carga; no se sobrescribe la primera fila.")
                    if not job["trust_references"]:
                        raw["reference_verified"] = "false"
                    digest = canonical_hash(raw)
                    previous = c.execute("SELECT id,raw_hash,raw_json FROM skus WHERE seller=? AND sku=?", (raw["seller"], raw["sku"])).fetchone()
                    if previous and previous["raw_hash"] == digest:
                        counters["unchanged"] += 1
                    else:
                        calc = calculate_record(raw, config["rule_mode"], config["volume_ratio_threshold"], category_engine)
                        trusted = int(trusted_reference(raw) and calc["core_status"] == "valid")
                        values = (raw.get("name", ""), identity_key(raw), json.dumps(raw, ensure_ascii=False), digest, metrics_key(raw), json.dumps(calc), calc["status"], trusted, job_id, now())
                        if previous:
                            c.execute("UPDATE skus SET name=?,identity_key=?,raw_json=?,raw_hash=?,metrics_key=?,calculated=?,status=?,is_reference=?,job_id=?,updated_at=?,reference_revoked=0 WHERE id=?", (*values, previous["id"]))
                            c.execute("UPDATE proposals SET state='stale' WHERE sku_id=? AND state IN ('pending','approved')", (previous["id"],))
                            audit(c, previous["id"], "catalog_updated", json.dumps({"previous": json.loads(previous["raw_json"]), "current": raw}, ensure_ascii=False))
                            counters["updated"] += 1
                        else:
                            c.execute("INSERT INTO skus(seller,sku,name,identity_key,raw_json,raw_hash,metrics_key,calculated,status,is_reference,job_id,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (raw["seller"], raw["sku"], *values))
                            counters["inserted"] += 1
                except (ValueError, TypeError) as e:
                    counters["errors"] += 1
                    if len(examples) < 20:
                        examples.append(f"Fila {number}: {e}")
                if counters["processed"] % 1000 == 0:
                    job_update(c, job_id, **counters, error_examples=json.dumps(examples, ensure_ascii=False))
            job_update(c, job_id, **counters, error_examples=json.dumps(examples, ensure_ascii=False), stage="Contrastando referencias verificadas")
            generate_proposals(c, config)
            job_update(c, job_id, status="completed", stage="Análisis finalizado", finished_at=now())
        except Exception as e:
            # Committed blocks survive; an identical retry skips already imported rows.
            c.rollback()
            invalidate_references(c)
            c.commit()
            job_update(c, job_id, **counters, error_examples=json.dumps(examples, ensure_ascii=False), status="failed", error=str(e), stage="Carga interrumpida", finished_at=now())


def generate_proposals(c, config):
    invalidate_references(c)
    category_engine = CategoryEngine.from_db(c)
    # References that disagree are explicitly withheld, regardless of majority.
    c.execute("DROP TABLE IF EXISTS temp.consensus")
    c.execute("""CREATE TEMP TABLE consensus AS SELECT identity_key,MIN(id) AS reference_id FROM skus
      WHERE is_reference=1 AND identity_key IS NOT NULL GROUP BY identity_key HAVING COUNT(DISTINCT metrics_key)=1""")
    c.execute("CREATE INDEX temp.idx_consensus ON consensus(identity_key)")
    query = """SELECT s.id,s.raw_json,s.raw_hash,s.calculated,r.id AS ref_id,r.raw_json AS ref_json,r.raw_hash AS ref_hash
      FROM skus s JOIN consensus x ON x.identity_key=s.identity_key JOIN skus r ON r.id=(
        SELECT MIN(alt.id) FROM skus alt WHERE alt.identity_key=x.identity_key AND alt.is_reference=1 AND alt.seller<>s.seller)
      WHERE s.is_reference=0 AND s.seller<>r.seller AND s.metrics_key<>r.metrics_key"""
    for row in c.execute(query):
        reference = json.loads(row["ref_json"])
        values = {key: reference[key] for key in MEASURES}
        calc = calculate_record({**json.loads(row["raw_json"]), **values}, config["rule_mode"], config["volume_ratio_threshold"], category_engine)
        if calc["size"] is None:
            continue
        candidate = canonical_hash(values)
        existing = c.execute("SELECT id FROM proposals WHERE sku_id=? AND base_hash=? AND candidate_hash=? AND rule_version=? AND state IN ('pending','approved','rejected')", (row["id"], row["raw_hash"], candidate, config["version"])).fetchone()
        if existing:
            continue
        if c.execute("SELECT 1 FROM proposals WHERE sku_id=? AND state IN ('pending','approved')", (row["id"],)).fetchone():
            continue
        reason = "Diferencia frente a referencia explícitamente verificada del mismo producto, variante, presentación y empaque. Requiere aprobación."
        c.execute("INSERT INTO proposals(sku_id,base_hash,candidate_hash,rule_version,values_json,calculated,source_url,source_kind,evidence_note,reason,reference_id,reference_hash,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (row["id"], row["raw_hash"], candidate, config["version"], json.dumps(values), json.dumps(calc), reference["source_url"], "otro_seller", f"Referencia importada: seller {reference['seller']}, SKU {reference['sku']}. La verificación fue declarada al cargar; no se comprobó la URL automáticamente.", reason, row["ref_id"], row["ref_hash"], "pending", now()))
        c.execute("UPDATE skus SET status='review' WHERE id=?", (row["id"],))
        audit(c, row["id"], "proposal_created", reason)
    c.commit()


def invalidate_references(c):
    """Do not keep a correction live after its evidence changes or conflicts."""
    invalid = c.execute("""SELECT p.id,p.sku_id FROM proposals p LEFT JOIN skus r ON r.id=p.reference_id
      WHERE p.reference_id IS NOT NULL AND p.state IN ('pending','approved') AND
      (r.id IS NULL OR r.is_reference<>1 OR r.raw_hash<>p.reference_hash OR EXISTS
        (SELECT 1 FROM skus other WHERE other.identity_key=r.identity_key AND other.is_reference=1 AND other.metrics_key<>r.metrics_key))""").fetchall()
    for p in invalid:
        c.execute("UPDATE proposals SET state='stale' WHERE id=?", (p["id"],))
        row = c.execute("SELECT calculated FROM skus WHERE id=?", (p["sku_id"],)).fetchone()
        other = c.execute("SELECT state FROM proposals WHERE sku_id=? AND state IN ('pending','approved') ORDER BY state='pending' DESC LIMIT 1", (p["sku_id"],)).fetchone()
        status = ("review" if other["state"] == "pending" else "approved") if other else json.loads(row["calculated"])["status"]
        c.execute("UPDATE skus SET status=? WHERE id=?", (status, p["sku_id"]))
        audit(c, p["sku_id"], "evidence_stale", "La referencia cambió, dejó de estar verificada o contradice otra referencia. Propuesta invalidada.")


def worker_loop():
    while True:
        job_id = TASKS.get()
        try:
            if job_id is None:
                return
            if job_id.startswith("category-profile:"):
                process_category_profile(job_id.split(":", 1)[1])
            else:
                process_job(job_id)
        finally:
            TASKS.task_done()


@asynccontextmanager
async def lifespan(app):
    with exclusive_instance(DATA):
        init_db()
        thread = threading.Thread(target=worker_loop, daemon=True, name="catalog-worker")
        thread.start()
        try:
            yield
        finally:
            TASKS.put(None)
            LOG.info("Cerrando Talla. Esperando a que termine el trabajo en curso...")
            thread.join()


app = FastAPI(title="Talla · Control logístico", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok", "external_ai": False, "mode": "local_pilot", "startup_id": os.environ.get("TALLA_STARTUP_ID", "")}


@app.get("/api/settings")
def settings():
    with connect() as c:
        result = get_settings(c)
    return {**result, "bands": BANDS, "formula": "máximo(peso_gr / 1000, alto_cm × largo_cm × ancho_cm / 5000)"}


def recalculate_category_checks(c):
    """Refresh plausibility only; measured corrections and approvals survive."""
    config = get_settings(c)
    category_engine = CategoryEngine.from_db(c)
    last = 0
    count = 0
    while True:
        rows = c.execute("SELECT * FROM skus WHERE id>? ORDER BY id LIMIT 1000", (last,)).fetchall()
        if not rows:
            break
        for row in rows:
            raw = json.loads(row["raw_json"])
            calc = calculate_record(raw, config["rule_mode"], config["volume_ratio_threshold"], category_engine)
            live = c.execute("SELECT state FROM proposals WHERE sku_id=? AND state IN ('pending','approved') ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            status = ("review" if live["state"] == "pending" else "approved") if live else calc["status"]
            c.execute("UPDATE skus SET calculated=?,status=? WHERE id=?", (json.dumps(calc), status, row["id"]))
            count += 1
        last = rows[-1]["id"]
    for row in c.execute("SELECT p.id,p.values_json,s.raw_json FROM proposals p JOIN skus s ON s.id=p.sku_id WHERE p.state IN ('pending','approved')"):
        raw = {**json.loads(row["raw_json"]), **json.loads(row["values_json"])}
        calc = calculate_record(raw, config["rule_mode"], config["volume_ratio_threshold"], category_engine)
        c.execute("UPDATE proposals SET calculated=? WHERE id=?", (json.dumps(calc), row["id"]))
    return count


@app.get("/api/categories")
def categories():
    with connect() as c:
        rows = c.execute("""SELECT COALESCE(json_extract(raw_json,'$.category'),'') category,
          COALESCE(json_extract(raw_json,'$.subcategory'),'') subcategory,COUNT(*) count FROM skus
          GROUP BY category,subcategory ORDER BY count DESC LIMIT 1001""").fetchall()
        return {"items": [dict(r) for r in rows[:1000]], "truncated": len(rows) > 1000}


@app.get("/api/category-rules")
def category_rules():
    with connect() as c:
        items = list_rules(c)
        owners = {r["rule_id"]: dict(r) for r in c.execute("""SELECT a.*,s.source,s.counts_json FROM category_profile_applications a
          JOIN profile_suggestions s ON s.id=a.suggestion_id""")}
        for item in items:
            owner = owners.get(item["id"])
            if owner and owner["rule_version"] == item["version"]:
                support = json.loads(owner["counts_json"])
                item.update(origin=owner["source"], profile_run_id=owner["run_id"], support_count=support.get("support_count"), profile_support=support)
            else:
                item.update(origin="manual", profile_run_id=None, support_count=None)
        return {"items": items}


def process_category_profile(run_id):
    with MUTATION, connect() as c:
        row = c.execute("SELECT * FROM profile_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        try:
            def progress(processed, total):
                c.execute("UPDATE profile_runs SET progress=?,total=? WHERE id=?", (processed, total, run_id))
                c.commit()
            generate_profile(c, run_id, json.loads(row["config_json"]), progress_callback=progress)
            c.commit()
        except Exception as exc:
            c.rollback()
            c.execute("UPDATE profile_runs SET status='failed',error=?,finished_at=? WHERE id=?", (str(exc), now(), run_id))
            c.commit()


def serialize_profile(c, row):
    data = dict(row)
    data["config"] = json.loads(data.pop("config_json") or "{}")
    data["summary"] = json.loads(data.pop("summary_json") or "{}")
    current = {r["state"]: r["n"] for r in c.execute("SELECT state,COUNT(*) n FROM profile_suggestions WHERE run_id=? GROUP BY state", (row["id"],))}
    data["summary"].update(current)
    data["summary"]["ready"] = current.get("ready", 0)
    data["summary"]["applied"] = current.get("applied", 0)
    return data


class ProfileInput(BaseModel):
    min_count: int = Field(default=30, ge=30, le=100000)
    min_sellers: int = Field(default=3, ge=3, le=1000)


@app.post("/api/category-profiles")
def create_category_profile(body: ProfileInput):
    with mutating(), connect() as c:
        if c.execute("SELECT 1 FROM profile_runs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise HTTPException(409, "Ya hay un cálculo de rangos pendiente. Consulta su progreso.")
        if not c.execute("SELECT 1 FROM skus LIMIT 1").fetchone():
            raise HTTPException(422, "Carga primero tu catálogo para calcular rangos sobre sus categorías.")
        if not c.execute("SELECT 1 FROM skus WHERE TRIM(COALESCE(json_extract(raw_json,'$.category'),''))<>'' LIMIT 1").fetchone():
            raise HTTPException(422, "Tu catálogo no tiene categorías mapeadas. Vuelve a cargarlo asignando la columna Categoría; no necesitas definir sus límites manualmente.")
        run_id = uuid.uuid4().hex
        c.execute("INSERT INTO profile_runs(id,status,progress,total,config_json,summary_json,created_at) VALUES (?,?,?,?,?,?,?)", (run_id, "queued", 0, 0, json.dumps(body.model_dump()), "{}", now()))
        row = c.execute("SELECT * FROM profile_runs WHERE id=?", (run_id,)).fetchone()
        result = serialize_profile(c, row)
    TASKS.put("category-profile:" + run_id)
    return result


@app.get("/api/category-profiles")
def category_profiles():
    with connect() as c:
        return {"items": [serialize_profile(c, row) for row in c.execute("SELECT * FROM profile_runs ORDER BY created_at DESC,rowid DESC LIMIT 20")]}


@app.get("/api/category-profiles/{run_id}")
def category_profile(run_id: str):
    with connect() as c:
        row = c.execute("SELECT * FROM profile_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Cálculo de rangos no encontrado.")
        return serialize_profile(c, row)


@app.get("/api/category-profiles/{run_id}/suggestions")
def category_suggestions(run_id: str, page: int = 1, page_size: int = 25, state: str = "all"):
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422, "Paginación inválida.")
    states = {"ready", "insufficient", "degenerate", "heterogeneous", "existing_manual", "applied"}
    if state not in states | {"all"}:
        raise HTTPException(422, "Estado de sugerencia inválido.")
    with connect() as c:
        if not c.execute("SELECT 1 FROM profile_runs WHERE id=?", (run_id,)).fetchone():
            raise HTTPException(404, "Cálculo no encontrado.")
        where, args = "WHERE run_id=?", [run_id]
        if state != "all":
            where += " AND state=?"
            args.append(state)
        total = c.execute("SELECT COUNT(*) FROM profile_suggestions " + where, args).fetchone()[0]
        items = []
        for row in c.execute("SELECT * FROM profile_suggestions " + where + " ORDER BY CASE state WHEN 'ready' THEN 0 ELSE 1 END,category,subcategory,presentation LIMIT ? OFFSET ?", [*args, page_size, (page - 1) * page_size]):
            item = dict(row)
            for field in ("counts", "bounds", "method"):
                item[field] = json.loads(item.pop(field + "_json") or "{}")
            items.append(item)
        return {"items": items, "total": total, "page": page, "pages": max(1, (total + page_size - 1) // page_size)}


@app.get("/api/category-profiles/{run_id}/suggestions.csv")
def category_suggestions_csv(run_id: str):
    with connect() as c:
        if not c.execute("SELECT 1 FROM profile_runs WHERE id=?", (run_id,)).fetchone():
            raise HTTPException(404, "Cálculo no encontrado.")
    def rows():
        yield ["Categoría", "Subcategoría", "Presentación", "Estado", "Origen", "Peso real mínimo kg", "Peso real máximo kg", "Volumétrico mínimo kg", "Volumétrico máximo kg", "Soporte", "Método", "Motivo", "Regla aplicada"]
        with connect() as c:
            for row in c.execute("SELECT * FROM profile_suggestions WHERE run_id=? ORDER BY category,subcategory,presentation", (run_id,)):
                bounds = json.loads(row["bounds_json"] or "{}")
                yield [row["category"], row["subcategory"], row["presentation"], row["state"], row["source"], *(bounds.get(key, "") for key in ("real_min_kg", "real_max_kg", "volume_min_kg", "volume_max_kg")), row["counts_json"], row["method_json"], row["reason"], row["applied_rule_id"]]
    return csv_response(rows(), "rangos_propuestos_por_categoria.csv")


class ProfileApplyInput(BaseModel):
    suggestion_ids: list[int] = Field(default_factory=list, max_length=10000)
    all_ready: bool = False
    attestation: bool = False


@app.post("/api/category-profiles/{run_id}/apply")
def apply_category_suggestions(run_id: str, body: ProfileApplyInput):
    if not body.attestation:
        raise HTTPException(422, "Confirma que estos rangos solo generan alertas de plausibilidad y no verifican medidas ni corrigen cobros.")
    if body.all_ready == bool(body.suggestion_ids):
        raise HTTPException(422, "Selecciona sugerencias o elige todas las aptas, mediante una sola modalidad.")
    if len(set(body.suggestion_ids)) != len(body.suggestion_ids):
        raise HTTPException(422, "La selección contiene sugerencias repetidas.")
    with mutating(), connect() as c:
        run = c.execute("SELECT * FROM profile_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, "Cálculo no encontrado.")
        if run["status"] != "completed":
            raise HTTPException(409, "El cálculo no ha terminado correctamente.")
        if profile_fingerprint(c, get_settings(c)) != run["dataset_hash"]:
            raise HTTPException(409, "El catálogo, las reglas de talla o las aprobaciones cambiaron. Vuelve a calcular los rangos antes de aplicarlos.")
        condition, args = "run_id=? AND state='ready'", [run_id]
        if not body.all_ready:
            condition += " AND id IN (" + ",".join("?" for _ in body.suggestion_ids) + ")"
            args.extend(body.suggestion_ids)
        suggestions = c.execute("SELECT * FROM profile_suggestions WHERE " + condition, args).fetchall()
        if not body.all_ready and len(suggestions) != len(body.suggestion_ids):
            raise HTTPException(409, "La selección incluye sugerencias ajenas, ya aplicadas o no aptas.")
        # A more-specific generated rule must not displace a manual broad rule.
        owned = {r["rule_id"]: r["rule_version"] for r in c.execute("SELECT rule_id,rule_version FROM category_profile_applications")}
        existing = c.execute("SELECT * FROM category_rules").fetchall()
        manual = [r for r in existing if owned.get(r["id"]) != r["version"]]
        protected = applied = 0
        for row in suggestions:
            category = normalize_scope(row["category"])
            subcategory = normalize_scope(row["subcategory"])
            presentation = normalize_scope(row["presentation"])
            overlaps = any(r["category_key"] == category and (not r["subcategory_key"] or not subcategory or r["subcategory_key"] == subcategory) and (not r["presentation_key"] or not presentation or r["presentation_key"] == presentation) for r in manual)
            if overlaps:
                c.execute("UPDATE profile_suggestions SET state='existing_manual',reason=? WHERE id=?", ("Ámbito protegido por una regla manual compatible; no se reemplaza ni se desplaza.", row["id"]))
                protected += 1
                continue
            bounds = json.loads(row["bounds_json"])
            counts = json.loads(row["counts_json"])
            note = ("Rango de plausibilidad automático. " + ("Soporte de referencias declaradas o correcciones aprobadas. " if row["source"] == "declared_references" else "Estadístico provisional del catálogo; medidas no verificadas. ") + "No imputa medidas ni corrige tallas o cobros. " + f"Perfil {run_id}; soporte real={counts.get('real_support_count', 0)}, volumétrico={counts.get('volume_support_count', 0)}. Consulte el perfil para método y exclusiones.")
            rule_data = {"category": row["category"], "subcategory": row["subcategory"], "presentation": row["presentation"], **bounds, "note": note, "enabled": True}
            previous = next((r for r in existing if r["category_key"] == category and r["subcategory_key"] == subcategory and r["presentation_key"] == presentation), None)
            rule = save_rule(c, rule_data, previous["id"] if previous else None)
            c.execute("INSERT INTO category_profile_applications(rule_id,run_id,suggestion_id,rule_version) VALUES (?,?,?,?) ON CONFLICT(rule_id) DO UPDATE SET run_id=excluded.run_id,suggestion_id=excluded.suggestion_id,rule_version=excluded.rule_version", (rule["id"], run_id, row["id"], rule["version"]))
            c.execute("UPDATE profile_suggestions SET state='applied',applied_rule_id=? WHERE id=?", (rule["id"], row["id"]))
            applied += 1
        recalculated = recalculate_category_checks(c) if applied else 0
        c.execute("INSERT INTO configuration_events(action,detail,created_at) VALUES (?,?,?)", ("category_profile_applied", json.dumps({"run_id": run_id, "applied": applied, "protected_manual": protected}), now()))
        return {"applied": applied, "skipped": protected, "protected_manual": protected, "recalculated": recalculated}


def write_category_rule(body, rule_id=None):
    with mutating(), connect() as c:
        previous = next((r for r in list_rules(c) if r["id"] == rule_id), None)
        if rule_id is not None and previous is None:
            raise HTTPException(404, "Regla no encontrada.")
        try:
            result = save_rule(c, body, rule_id)
        except ValueError as e:
            raise HTTPException(422, str(e))
        c.execute("INSERT INTO configuration_events(action,detail,created_at) VALUES (?,?,?)", ("category_rule_saved", json.dumps({"previous": previous, "current": result}, ensure_ascii=False), now()))
        count = recalculate_category_checks(c)
        return {**result, "recalculated": count}


@app.post("/api/category-rules")
def create_category_rule(body: dict):
    return write_category_rule(body)


@app.put("/api/category-rules/{rule_id}")
def update_category_rule(rule_id: int, body: dict):
    return write_category_rule(body, rule_id)


@app.delete("/api/category-rules/{rule_id}")
def disable_category_rule(rule_id: int):
    with mutating(), connect() as c:
        previous = next((r for r in list_rules(c) if r["id"] == rule_id), None)
        if previous is None:
            raise HTTPException(404, "Regla no encontrada.")
        delete_rule(c, rule_id)
        c.execute("INSERT INTO configuration_events(action,detail,created_at) VALUES (?,?,?)", ("category_rule_disabled", json.dumps(previous, ensure_ascii=False), now()))
        count = recalculate_category_checks(c)
        return {"disabled": True, "id": rule_id, "recalculated": count}


class RuleUpdate(BaseModel):
    rule_mode: str
    volume_ratio_threshold: str | float | int | None = None


@app.put("/api/settings")
def change_settings(body: RuleUpdate):
    if body.rule_mode not in {"strict", "upper_bounds"}:
        raise HTTPException(422, "Modo de reglas inválido.")
    with mutating(), connect() as c:
        config = get_settings(c)
        try:
            threshold = parse_number(body.volume_ratio_threshold if body.volume_ratio_threshold is not None else config["volume_ratio_threshold"], "Umbral volumétrico/real")
            if threshold < 1 or threshold > 1000000:
                raise ValidationError("El umbral volumétrico/real debe estar entre 1 y 1.000.000.")
        except ValueError as e:
            raise HTTPException(422, str(e))
        if config["rule_mode"] == body.rule_mode and threshold == parse_number(config["volume_ratio_threshold"]):
            return settings()
        c.execute("UPDATE settings SET rule_mode=?,volume_ratio_threshold=?,version=version+1 WHERE id=1", (body.rule_mode, str(threshold)))
        c.execute("UPDATE proposals SET state='stale' WHERE state IN ('pending','approved')")
        category_engine = CategoryEngine.from_db(c)
        for row in c.execute("SELECT id,raw_json,is_reference,reference_revoked FROM skus"):
            raw = json.loads(row["raw_json"])
            calc = calculate_record(raw, body.rule_mode, str(threshold), category_engine)
            c.execute("UPDATE skus SET calculated=?,status=?,is_reference=? WHERE id=?", (json.dumps(calc), calc["status"], int(trusted_reference(raw) and not row["reference_revoked"] and calc["core_status"] == "valid"), row["id"]))
            audit(c, row["id"], "rules_changed", f"Política {body.rule_mode}; umbral volumétrico/real {threshold}. Las propuestas anteriores quedan obsoletas.")
        generate_proposals(c, get_settings(c))
    return settings()


@app.get("/api/summary")
def summary():
    with connect() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status,COUNT(*) AS n FROM sku_analysis GROUP BY status")}
        by_size = {r["size"] or "Pendiente": r["n"] for r in c.execute("""SELECT COALESCE(
          json_extract(effective_approved_calculated,'$.size'),json_extract(calculated,'$.size')) AS size,
          COUNT(*) AS n FROM sku_analysis GROUP BY size""")}
        updated = c.execute("SELECT MAX(updated_at) FROM skus").fetchone()[0]
        directions = {key: 0 for key in ["up", "down", "same", "unknown"]}
        directions.update({r["direction"]: r["n"] for r in c.execute("SELECT direction,COUNT(*) n FROM sku_analysis GROUP BY direction")})
        verification = {key: 0 for key in ["unverified", "declared_reference", "evidence_registered", "approved"]}
        verification.update({r["verification"]: r["n"] for r in c.execute("SELECT verification,COUNT(*) n FROM sku_analysis GROUP BY verification")})
        return {"total": sum(counts.values()), **{k: counts.get(k, 0) for k in ["valid", "review", "pending", "approved"]}, "by_size": by_size, "updated_at": updated, "rule_mode": get_settings(c)["rule_mode"], "directions": directions, "verification": verification}


def serialize_record(row, proposal=None, approved=None):
    raw = json.loads(row["raw_json"])
    calc = json.loads(row["calculated"])
    effective = approved or (proposal if proposal and proposal["state"] == "approved" else None)
    if effective:
        raw = {**raw, **json.loads(effective["values_json"])}
        calc = json.loads(effective["calculated"])
    analysis = {k: row[k] for k in ["direction", "direction_basis", "comparison_from_size", "comparison_to_size", "verification"] if k in row.keys()}
    if "reason_codes" in row.keys():
        analysis["reason_codes"] = json.loads(row["reason_codes"])
    return {**raw, **calc, **analysis, "id": row["id"], "seller": row["seller"], "sku": row["sku"], "status": row["status"], "has_proposal": bool(proposal and proposal["state"] == "pending"), "is_reference": bool(row["is_reference"]), "updated_at": row["updated_at"]}


@app.get("/api/skus")
def skus(page: int = 1, page_size: int = 25, search: str = "", status: str = "all", direction: str = "all", reason: str = "all"):
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422, "Paginación inválida.")
    clauses, args = [], []
    if direction != "all":
        if direction not in {"up", "down", "same", "unknown"}:
            raise HTTPException(422, "Dirección inválida.")
        clauses.append("direction=?")
        args.append(direction)
    if reason != "all":
        if reason not in {"invalid_physical_data", "table_gap", "above_table", "volume_weight_ratio", "proposal_waiting", "billed_size_invalid", "billed_size_mismatch", "category_real_below_min", "category_real_above_max", "category_volume_below_min", "category_volume_above_max"}:
            raise HTTPException(422, "Motivo inválido.")
        clauses.append("EXISTS(SELECT 1 FROM json_each(sku_analysis.reason_codes) WHERE value=?)")
        args.append(reason)
    if status != "all":
        if status not in {"valid", "review", "pending", "approved"}:
            raise HTTPException(422, "Estado inválido.")
        clauses.append("status=?")
        args.append(status)
    if search:
        clauses.append("(sku LIKE ? ESCAPE '\\' OR seller LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')")
        term = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:200] + "%"
        args.extend([term] * 3)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as c:
        total = c.execute("SELECT COUNT(*) FROM sku_analysis" + where, args).fetchone()[0]
        rows = c.execute("SELECT * FROM sku_analysis" + where + " ORDER BY CASE direction WHEN 'up' THEN 0 WHEN 'unknown' THEN 1 WHEN 'same' THEN 2 ELSE 3 END,id DESC LIMIT ? OFFSET ?", [*args, page_size, (page - 1) * page_size]).fetchall()
        result = []
        for row in rows:
            proposal = c.execute("SELECT * FROM proposals WHERE id=?", (row["analysis_proposal_id"],)).fetchone()
            approved = c.execute("SELECT * FROM proposals WHERE id=?", (row["effective_approved_id"],)).fetchone()
            result.append(serialize_record(row, proposal, approved))
        return {"items": result, "total": total, "page": page, "pages": max(1, (total + page_size - 1) // page_size)}


@app.get("/api/skus/{sku_id}")
def detail(sku_id: int):
    with connect() as c:
        row = c.execute("SELECT * FROM sku_analysis WHERE id=?", (sku_id,)).fetchone()
        if not row:
            raise HTTPException(404, "SKU no encontrado.")
        proposal = c.execute("SELECT * FROM proposals WHERE id=?", (row["analysis_proposal_id"],)).fetchone()
        if not proposal:
            historical = c.execute("SELECT * FROM proposals WHERE sku_id=? ORDER BY id DESC LIMIT 1", (sku_id,)).fetchone()
            if historical:
                proposal = dict(historical)
                if proposal["state"] in {"pending", "approved"}:
                    proposal["state"] = "stale"
        history = [dict(r) for r in c.execute("SELECT action,detail,created_at FROM history WHERE sku_id=? ORDER BY id DESC LIMIT 50", (sku_id,))]
        p = None
        if proposal:
            p = {**dict(proposal), **json.loads(proposal["values_json"]), **json.loads(proposal["calculated"])}
        approved = c.execute("SELECT * FROM proposals WHERE id=?", (row["effective_approved_id"],)).fetchone()
        return {"record": serialize_record(row, proposal, approved), "original": json.loads(row["raw_json"]), "original_calculated": json.loads(row["calculated"]), "proposal": p, "history": history}


class ProposalInput(BaseModel):
    weight_g: str | float | int
    height_cm: str | float | int
    length_cm: str | float | int
    width_cm: str | float | int
    source_url: str = Field(min_length=8, max_length=2000)
    source_kind: str
    evidence_note: str = Field(min_length=15, max_length=5000)
    reason: str = Field(min_length=5, max_length=2000)


def create_evidence_proposal(c, sku_row, values, source_url, source_kind, evidence_note, reason):
    """Stage researched values without replacing originals or human decisions."""
    if set(values) != set(MEASURES):
        raise ValueError("La evidencia debe aportar los cuatro campos físicos; los faltantes no se completan con el catálogo.")
    if not valid_url(source_url) or len(source_url) > 2000 or not 15 <= len(evidence_note) <= 40000:
        raise ValueError("La fuente o la evidencia de la propuesta no es válida.")
    current = c.execute("SELECT * FROM skus WHERE id=?", (sku_row["id"],)).fetchone()
    if current is None or current["raw_hash"] != sku_row["raw_hash"]:
        raise ValueError("El registro cambió desde la investigación.")
    config = get_settings(c)
    converted = {field: str(values[field]) for field in MEASURES}
    raw = {**json.loads(current["raw_json"]), **converted}
    calc = calculate_record(raw, config["rule_mode"], config["volume_ratio_threshold"], CategoryEngine.from_db(c))
    if calc["size"] is None:
        raise ValueError("Las medidas encontradas no permiten asignar una talla con las reglas actuales.")
    digest = canonical_hash(converted)
    previous = c.execute("""SELECT id,state FROM proposals WHERE sku_id=? AND base_hash=? AND rule_version=?
      AND (candidate_hash=? OR state IN ('pending','approved'))
      AND state IN ('pending','approved','rejected') ORDER BY id DESC LIMIT 1""", (current["id"], current["raw_hash"], config["version"], digest)).fetchone()
    if previous:
        return {"id": previous["id"], "state": previous["state"], "created": False}
    cur = c.execute("""INSERT INTO proposals(sku_id,base_hash,candidate_hash,rule_version,values_json,calculated,
      source_url,source_kind,evidence_note,reason,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (current["id"], current["raw_hash"], digest, config["version"], json.dumps(converted), json.dumps(calc), source_url, source_kind, evidence_note, reason, "pending", now()))
    c.execute("UPDATE skus SET status='review' WHERE id=?", (current["id"],))
    audit(c, current["id"], "research_proposal_created", reason)
    return {"id": cur.lastrowid, "state": "pending", "created": True}


def valid_url(url):
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False


@app.post("/api/skus/{sku_id}/proposal")
def propose(sku_id: int, body: ProposalInput):
    if not valid_url(body.source_url):
        raise HTTPException(422, "Indique una URL http(s) de evidencia sin credenciales.")
    if body.source_kind not in {"medicion_logistica", "fabricante", "tienda_oficial", "otro_seller", "investigacion_externa"}:
        raise HTTPException(422, "Tipo de fuente inválido.")
    with mutating(), connect() as c:
        row = c.execute("SELECT * FROM skus WHERE id=?", (sku_id,)).fetchone()
        if not row:
            raise HTTPException(404, "SKU no encontrado.")
        values = {key: str(getattr(body, key)) for key in MEASURES}
        config = get_settings(c)
        try:
            calc = calculate_record({**json.loads(row["raw_json"]), **values}, config["rule_mode"], config["volume_ratio_threshold"], CategoryEngine.from_db(c))
        except ValueError as e:
            raise HTTPException(422, str(e))
        if calc["size"] is None:
            raise HTTPException(422, "La propuesta cae en un hueco de la tabla o excede 500 kg. Resuelva la regla antes de aprobar.")
        c.execute("UPDATE proposals SET state='superseded' WHERE sku_id=? AND state='pending'", (sku_id,))
        cur = c.execute("INSERT INTO proposals(sku_id,base_hash,candidate_hash,rule_version,values_json,calculated,source_url,source_kind,evidence_note,reason,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (sku_id, row["raw_hash"], canonical_hash(values), config["version"], json.dumps(values), json.dumps(calc), body.source_url, body.source_kind, body.evidence_note, body.reason, "pending", now()))
        c.execute("UPDATE skus SET status='review' WHERE id=?", (sku_id,))
        audit(c, sku_id, "proposal_created", body.reason)
        return {"id": cur.lastrowid, "status": "pending"}


class Decision(BaseModel):
    decision: str
    note: str = Field(default="", max_length=2000)


def decide_one(c, proposal_id: int, body: Decision):
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(422, "Decisión inválida.")
    p = c.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Propuesta no encontrada.")
    if p["state"] != "pending":
        raise HTTPException(409, "La propuesta ya fue revisada o quedó obsoleta.")
    row = c.execute("SELECT * FROM skus WHERE id=?", (p["sku_id"],)).fetchone()
    if p["base_hash"] != row["raw_hash"] or p["rule_version"] != get_settings(c)["version"]:
        raise HTTPException(409, "El catálogo o las reglas cambiaron. Revise una nueva propuesta.")
    if p["reference_id"]:
        ref = c.execute("SELECT raw_hash,is_reference,identity_key,metrics_key FROM skus WHERE id=?", (p["reference_id"],)).fetchone()
        if not ref or not ref["is_reference"] or ref["raw_hash"] != p["reference_hash"]:
            raise HTTPException(409, "La referencia cambió o dejó de estar verificada. Cree una nueva propuesta.")
        if c.execute("SELECT 1 FROM skus WHERE identity_key=? AND is_reference=1 AND metrics_key<>? LIMIT 1", (ref["identity_key"], ref["metrics_key"])).fetchone():
            raise HTTPException(409, "Hay referencias verificadas contradictorias. Resuelva la evidencia antes de aprobar.")
    state = "approved" if body.decision == "approve" else "rejected"
    if state == "approved":
        c.execute("UPDATE proposals SET state='superseded' WHERE sku_id=? AND state='approved'", (p["sku_id"],))
    c.execute("UPDATE proposals SET state=?,decided_at=?,decision_note=? WHERE id=?", (state, now(), body.note, proposal_id))
    if state == "approved":
        status = "approved"
    elif c.execute("SELECT 1 FROM proposals WHERE sku_id=? AND state='approved'", (p["sku_id"],)).fetchone():
        status = "approved"
    else:
        status = json.loads(row["calculated"])["status"]
    c.execute("UPDATE skus SET status=?,updated_at=? WHERE id=?", (status, now(), p["sku_id"]))
    audit(c, p["sku_id"], state, json.dumps({"proposal_id": proposal_id, "note": body.note}, ensure_ascii=False))
    if state == "approved" and row["is_reference"]:
        c.execute("UPDATE skus SET is_reference=0,reference_revoked=1 WHERE id=?", (p["sku_id"],))
        audit(c, p["sku_id"], "reference_revoked", "Referencia corregida: sus datos originales dejan de respaldar propuestas de otros sellers.")
        invalidate_references(c)
    return {"state": state}


@app.post("/api/proposals/{proposal_id}/decision")
def decide(proposal_id: int, body: Decision):
    with mutating(), connect() as c:
        return decide_one(c, proposal_id, body)


@app.get("/api/diagnostics")
def diagnostics():
    with connect() as c:
        return diagnostic_report(c)


@app.get("/api/diagnostics.csv")
def diagnostics_export(scope: str = "attention"):
    if scope not in {"attention", "all"}:
        raise HTTPException(422, "El alcance debe ser attention o all.")
    def rows():
        with connect() as c:
            yield from diagnostic_rows(c, scope)
    return csv_response(rows(), "diagnostico_catalogo.csv")


@app.get("/api/groups")
def groups(page: int = 1, page_size: int = 20, direction: str = "all"):
    with connect() as c:
        try:
            return review_groups(c, page, page_size, direction)
        except ValueError as e:
            raise HTTPException(422, str(e))


class GroupDecision(Decision):
    proposal_ids: list[int] = Field(min_length=1, max_length=500)


@app.post("/api/groups/decision")
def decide_group(body: GroupDecision):
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(422, "Decisión inválida.")
    if body.decision == "reject" and not body.note.strip():
        raise HTTPException(422, "Indique el motivo del rechazo.")
    if len(set(body.proposal_ids)) != len(body.proposal_ids):
        raise HTTPException(422, "La selección contiene propuestas repetidas.")
    with mutating(), connect() as c:
        placeholders = ",".join("?" for _ in body.proposal_ids)
        eligible = c.execute(f"""SELECT p.id,{GROUP_KEY_SQL} AS group_key FROM sku_analysis s
          JOIN proposals p ON p.id=s.analysis_proposal_id
          WHERE p.id IN ({placeholders}) AND s.analysis_proposal_state='pending' AND p.state='pending'""", body.proposal_ids).fetchall()
        if len(eligible) != len(body.proposal_ids):
            raise HTTPException(409, "La selección incluye una propuesta revisada, obsoleta o con evidencia desactualizada. Actualice el grupo.")
        if len({r["group_key"] for r in eligible}) != 1:
            raise HTTPException(422, "El lote mezcla productos, evidencia, valores o comparaciones diferentes. Revise cada grupo por separado.")
        # One transaction: any stale member prevents the whole selected batch.
        for proposal_id in body.proposal_ids:
            decide_one(c, proposal_id, body)
        return {"state": "approved" if body.decision == "approve" else "rejected", "processed": len(body.proposal_ids)}


def safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def csv_response(rows, filename):
    def generate():
        yield "\ufeff"
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in rows:
            writer.writerow([safe_cell(v) for v in row])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
    return StreamingResponse(generate(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export.csv")
def export():
    def rows():
        yield ["Seller", "SKU", "Peso (gr)", "Alto (cm)", "Largo (cm)", "Ancho (cm)", "Talla", "Talla anterior", "Peso cobro (kg)", "Motivo", "Fuente", "Evidencia", "Aprobado en", "Version regla", "Propuesta ID"]
        with connect() as c:
            version = get_settings(c)["version"]
            for p in c.execute("""SELECT p.*,s.seller,s.sku,s.calculated AS original_calculated FROM proposals p JOIN skus s ON s.id=p.sku_id
              WHERE p.state='approved' AND p.base_hash=s.raw_hash AND p.rule_version=?
              AND (p.reference_id IS NULL OR EXISTS(SELECT 1 FROM skus r WHERE r.id=p.reference_id AND r.raw_hash=p.reference_hash AND r.is_reference=1
                AND NOT EXISTS(SELECT 1 FROM skus other WHERE other.identity_key=r.identity_key AND other.is_reference=1 AND other.metrics_key<>r.metrics_key))) ORDER BY p.id""", (version,)):
                values, calc = json.loads(p["values_json"]), json.loads(p["calculated"])
                yield [p["seller"], p["sku"], *(values[k] for k in MEASURES), calc["size"], json.loads(p["original_calculated"])["size"], calc["charge_kg"], p["reason"], p["source_url"], p["evidence_note"], p["decided_at"], p["rule_version"], p["id"]]
    return csv_response(rows(), "correcciones_aprobadas.csv")


@app.get("/api/template.csv")
def template():
    return csv_response([FIELDS], "plantilla_catalogo.csv")


@app.post("/api/uploads/preview")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise HTTPException(422, "Use un archivo CSV o XLSX.")
    upload_id = uuid.uuid4().hex
    path = UPLOADS / (upload_id + suffix)
    size = 0
    try:
        with path.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "Máximo 512 MiB por archivo. Divida el catálogo.")
                target.write(chunk)
        iterator = read_rows(path)
        try:
            headers = next(iterator)
            sample = []
            for _, row in zip(range(5), iterator):
                sample.append(row)
        finally:
            iterator.close()
        if not sample:
            raise ValueError("El archivo tiene encabezados pero no contiene registros.")
        name = Path(file.filename or "catalogo").name[:200]
        with connect() as c:
            c.execute("INSERT INTO uploads VALUES (?,?,?,?)", (upload_id, name, str(path), now()))
        return {"upload_id": upload_id, "filename": name, "headers": headers, "sample": sample, "suggested_mapping": suggest_mapping(headers)}
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception as e:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"No se pudo leer el archivo: {e}")
    finally:
        await file.close()


class ProcessInput(BaseModel):
    mapping: dict[str, str]
    trust_references: bool = False


def enqueue(upload_id, body):
    with connect() as c:
        record = c.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if not record:
            raise HTTPException(404, "Carga no encontrada.")
        iterator = read_rows(record["path"])
        try:
            headers = next(iterator)
        finally:
            iterator.close()
        mapping = {k: v for k, v in body.mapping.items() if v}
        if any(k not in FIELDS for k in mapping) or any(v not in headers for v in mapping.values()):
            raise HTTPException(422, "El mapeo contiene columnas desconocidas.")
        if any(not mapping.get(k) for k in REQUIRED):
            raise HTTPException(422, "Asigne seller, SKU, peso y las tres dimensiones.")
        if len(set(mapping.values())) != len(mapping):
            raise HTTPException(422, "Una columna no puede asignarse a dos campos.")
        job_id = uuid.uuid4().hex
        c.execute("INSERT INTO jobs(id,upload_id,filename,status,stage,mapping,trust_references,created_at) VALUES (?,?,?,?,?,?,?,?)", (job_id, upload_id, record["filename"], "queued", "En cola", json.dumps(mapping), body.trust_references, now()))
    TASKS.put(job_id)
    return {"job_id": job_id}


@app.post("/api/uploads/{upload_id}/process")
def start_process(upload_id: str, body: ProcessInput):
    return enqueue(upload_id, body)


def serialize_job(row):
    data = dict(row)
    data["error_examples"] = json.loads(data["error_examples"])
    data.pop("mapping", None)
    return data


@app.get("/api/jobs")
def jobs():
    with connect() as c:
        return {"items": [serialize_job(r) for r in c.execute("SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT 30")]}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Trabajo no encontrado.")
        return serialize_job(row)


@app.post("/api/demo")
def demo():
    path = ROOT / "examples" / "catalogo_demo.csv"
    upload_id = uuid.uuid4().hex
    with connect() as c:
        c.execute("INSERT INTO uploads VALUES (?,?,?,?)", (upload_id, "DEMO · datos ficticios.csv", str(path), now()))
    iterator = read_rows(path)
    try:
        headers = next(iterator)
    finally:
        iterator.close()
    return enqueue(upload_id, ProcessInput(mapping={key: key for key in FIELDS if key in headers}, trust_references=True))


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
register_research_routes(app, sys.modules[__name__])
