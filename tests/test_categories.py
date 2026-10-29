"""Category plausibility rules and v2-to-v3 compatibility; fictitious data only."""
from __future__ import annotations

import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as application
from categories import CategoryEngine, init_schema, save_rule
from engine import calculate, canonical_hash, identity_key, parse_number


def row(seller="Seller ficticio A", sku="SKU-1", **changes):
    result = dict(seller=seller, sku=sku, name="Producto de prueba", brand="Marca ejemplo",
                  model="M-1", gtin="", variant="Única", presentation="Unidad",
                  packaging="Caja de envío", weight_g="10", height_cm="100", length_cm="10",
                  width_cm="10", reference_verified="false", source_url="", evidence_kind="",
                  billed_size="", category="Electrónica", subcategory="Audio")
    result.update(changes)
    return result


def v2_hash(raw):
    """Frozen v2 source fields, before explicit category/subcategory inputs."""
    fields = ("seller", "sku", "name", "gtin", "brand", "model", "variant", "presentation",
              "packaging", "weight_g", "height_cm", "length_cm", "width_cm", "reference_verified",
              "source_url", "evidence_kind", "billed_size")
    normalized = {}
    for field in fields:
        value = raw.get(field)
        if field in {"weight_g", "height_cm", "length_cm", "width_cm"}:
            try:
                text = format(parse_number(value), "f")
                normalized[field] = text.rstrip("0").rstrip(".") if "." in text else text
            except ValueError:
                normalized[field] = str(value or "").strip()
        elif field == "reference_verified":
            normalized[field] = value is True or str(value).strip().casefold() in {"1", "true", "si", "sí", "yes", "verificado"}
        else:
            normalized[field] = str(value or "").strip()
    return sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class IsolatedAPI:
    """A fresh persisted DB per class, without touching a running user's data."""
    @classmethod
    def setUpClass(cls):
        cls.saved_paths = application.DATA, application.DB, application.UPLOADS
        cls.directory = tempfile.TemporaryDirectory(prefix="talla-v3-tests-")
        application.DATA = Path(cls.directory.name)
        application.DB = application.DATA / "talla.sqlite3"
        application.UPLOADS = application.DATA / "uploads"
        cls.context = TestClient(application.app)
        cls.client = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        application.DATA, application.DB, application.UPLOADS = cls.saved_paths
        cls.directory.cleanup()

    def setUp(self):
        application.TASKS.join()
        with application.connect() as c:
            c.execute("PRAGMA foreign_keys=OFF")
            names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'settings'")]
            for name in names:
                c.execute('DELETE FROM "' + name.replace('"', '""') + '"')
            c.execute("UPDATE settings SET rule_mode='strict',version=1,volume_ratio_threshold='100' WHERE id=1")

    def upload(self, records):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=application.FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        response = self.client.post("/api/uploads/preview", files={"file": ("ficticio.csv", stream.getvalue().encode())})
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        response = self.client.post(f"/api/uploads/{preview['upload_id']}/process", json={"mapping": {k: k for k in application.FIELDS}})
        self.assertEqual(response.status_code, 200, response.text)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{response.json()['job_id']}").json()
            if job["status"] in {"completed", "failed"}:
                application.TASKS.join()
                self.assertEqual(job["status"], "completed", job)
                return job
            time.sleep(.01)
        self.fail("Carga ficticia no finalizada")

    def detail(self, sku="SKU-1", seller=None):
        records = self.client.get("/api/skus", params={"search": sku}).json()["items"]
        item = next(r for r in records if r["sku"] == sku and (seller is None or r["seller"] == seller))
        return self.client.get(f"/api/skus/{item['id']}").json()

    def csv_rows(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


class CategoryEngineTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        init_schema(self.db)

    def tearDown(self):
        self.db.close()

    def rule(self, **changes):
        result = dict(category="Electrónica", note="Rango revisado de ejemplo", real_min_kg="1", real_max_kg="10")
        result.update(changes)
        return save_rule(self.db, result)

    def test_category_alert_does_not_impute_measurements_or_size(self):
        rule = self.rule()
        raw = row(weight_g="500", height_cm="10", length_cm="10", width_cm="10")
        original = calculate("500", "10", "10", "10")
        result = CategoryEngine.from_db(self.db).apply(raw, original)
        self.assertEqual(result["category_check"]["rule_id"], rule["id"])
        self.assertEqual(result["category_check"]["status"], "outside_range")
        self.assertIn("category_real_below_min", result["reason_codes"])
        self.assertEqual(result["status"], "review")
        for field in ("weight_g", "height_cm", "length_cm", "width_cm", "real_kg", "volume_kg", "charge_kg", "size"):
            self.assertEqual(result[field], original[field])
        self.assertEqual(original["status"], "valid")

    def test_most_specific_scope_wins_and_titles_do_not_infer_category(self):
        general = self.rule(real_min_kg=None, real_max_kg="100")
        self.rule(subcategory="Audio", real_min_kg=None, real_max_kg="10")
        self.rule(presentation="Pack x 2", real_min_kg=None, real_max_kg="5")
        exact = self.rule(subcategory="Audio", presentation="Pack x 2", real_min_kg=None, real_max_kg="2")
        engine = CategoryEngine.from_db(self.db)
        calc = calculate("3000", "10", "10", "10")
        scoped = engine.apply(row(presentation="Pack x 2"), calc)
        self.assertEqual(scoped["category_check"]["rule_id"], exact["id"])
        self.assertIn("category_real_above_max", scoped["reason_codes"])
        generic = engine.apply(row(subcategory="Video", presentation="Unidad"), calc)
        self.assertEqual(generic["category_check"]["rule_id"], general["id"])
        missing = engine.apply(row(category="", name="Electrónica Audio Pack x 2"), calc)
        self.assertEqual(missing["category_check"]["status"], "no_category")
        self.assertEqual(missing["status"], "valid")

    def test_invalid_or_duplicate_ranges_are_rejected(self):
        self.rule()
        with self.assertRaises(ValueError):
            self.rule(category=" ELECTRONICA ")
        for change in ({"real_min_kg": "20", "real_max_kg": "10"}, {"real_min_kg": "0"},
                       {"real_min_kg": None, "real_max_kg": None}, {"note": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.rule(category="Otra categoría", **change)

    def test_missing_weight_remains_missing_and_pending(self):
        self.rule()
        calc = {"status": "pending", "size": None, "real_kg": None, "volume_kg": None,
                "charge_kg": None, "flags": ["Falta peso"], "reason_codes": ["invalid_physical_data"]}
        result = CategoryEngine.from_db(self.db).apply(row(weight_g=""), calc)
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["real_kg"])
        self.assertEqual(result["category_check"]["status"], "insufficient_data")
        self.assertIn("category_real_missing", result["observation_codes"])


class CategoryAPITests(IsolatedAPI, unittest.TestCase):
    def create_rule(self, **changes):
        rule = dict(category="Electrónica", subcategory="", presentation="", real_min_kg="1",
                    real_max_kg="10", note="Rango de revisión declarado", enabled=True)
        rule.update(changes)
        response = self.client.post("/api/category-rules", json=rule)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), rule

    def test_crud_recalculates_without_changing_raw_measurements(self):
        raw = row(weight_g="500", height_cm="10", length_cm="10", width_cm="10")
        self.upload([raw])
        before = self.detail()
        created, body = self.create_rule()
        reviewed = self.detail()
        self.assertEqual(reviewed["record"]["status"], "review")
        self.assertIn("category_real_below_min", reviewed["record"]["reason_codes"])
        self.assertEqual(reviewed["original"], before["original"])
        self.assertEqual(reviewed["record"]["size"], before["record"]["size"])
        response = self.client.put(f"/api/category-rules/{created['id']}", json={**body, "real_min_kg": ".1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.detail()["record"]["category_check"]["status"], "within_range")
        response = self.client.delete(f"/api/category-rules/{created['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.detail()["record"]["category_check"]["status"], "no_rule")
        archived = next(rule for rule in self.client.get("/api/category-rules").json()["items"] if rule["id"] == created["id"])
        self.assertFalse(archived["enabled"])

    def test_rule_updates_preserve_existing_approval_and_originals(self):
        self.upload([row(weight_g="1200", height_cm="100", length_cm="30", width_cm="20")])
        before = self.detail()
        proposal = dict(weight_g="1200", height_cm="40", length_cm="30", width_cm="20",
                        source_url="https://example.com/measurement", source_kind="medicion_logistica",
                        evidence_note="Medición documentada del mismo producto y paquete", reason="Alto verificado")
        response = self.client.post(f"/api/skus/{before['record']['id']}/proposal", json=proposal)
        self.assertEqual(response.status_code, 200, response.text)
        proposal_id = response.json()["id"]
        response = self.client.post(f"/api/proposals/{proposal_id}/decision", json={"decision": "approve"})
        self.assertEqual(response.status_code, 200, response.text)
        created, body = self.create_rule(real_min_kg=".1", volume_max_kg="3")
        response = self.client.put(f"/api/category-rules/{created['id']}", json={**body, "volume_max_kg": "5"})
        self.assertEqual(response.status_code, 200, response.text)
        after = self.detail()
        self.assertEqual(after["proposal"]["state"], "approved")
        self.assertEqual(after["original"], before["original"])
        self.assertEqual(after["record"]["size"], "S")
        self.assertEqual(len(self.csv_rows("/api/export.csv")), 1)

    def test_category_is_optional_and_not_mapped_from_legacy_logistics_class(self):
        self.upload([row(category="", subcategory="", billed_size="Clase antigua 4")])
        record = self.detail()["record"]
        self.assertEqual(record["category_check"]["status"], "no_category")
        self.assertIn("billed_size_invalid", record["reason_codes"])
        self.assertEqual(record["direction"], "unknown")

    def test_v2_migration_rebases_hashes_preserves_approval_and_does_not_infer_category(self):
        saved_paths = application.DATA, application.DB, application.UPLOADS
        with tempfile.TemporaryDirectory(prefix="talla-v2-upgrade-") as directory:
            try:
                application.DATA = Path(directory)
                application.DB = application.DATA / "talla.sqlite3"
                application.UPLOADS = application.DATA / "uploads"
                application.init_db()
                approved = row(sku="APPROVED", weight_g="1200", height_cm="100", length_cm="30", width_cm="20")
                legacy = row(sku="LEGACY", weight_g="1200", billed_size="Clase antigua 4")
                for raw in (approved, legacy):
                    raw.pop("category")
                    raw.pop("subcategory")
                values = dict(weight_g="1200", height_cm="40", length_cm="30", width_cm="20")
                self.assertNotEqual(v2_hash(approved), canonical_hash(approved))
                with application.connect() as c:
                    v2_tables = {"settings", "skus", "proposals", "history", "jobs", "uploads"}
                    c.execute("PRAGMA foreign_keys=OFF")
                    for table in [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]:
                        if table not in v2_tables:
                            c.execute('DROP TABLE "' + table.replace('"', '""') + '"')
                    c.execute("UPDATE settings SET engine_revision=2 WHERE id=1")
                    for raw in (approved, legacy):
                        calc = calculate(*(raw[key] for key in application.MEASURES))
                        c.execute("""INSERT INTO skus(seller,sku,name,identity_key,raw_json,raw_hash,metrics_key,calculated,status,is_reference,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (raw["seller"], raw["sku"], raw["name"], identity_key(raw), json.dumps(raw),
                                v2_hash(raw), application.metrics_key(raw), json.dumps(calc), "approved" if raw is approved else "valid", 0, application.now()))
                    sku_id = c.execute("SELECT id FROM skus WHERE sku='APPROVED'").fetchone()[0]
                    c.execute("""INSERT INTO proposals(sku_id,base_hash,candidate_hash,rule_version,values_json,calculated,
                        source_url,source_kind,evidence_note,reason,state,created_at,decided_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        sku_id, v2_hash(approved), v2_hash(values), 1, json.dumps(values), json.dumps(calculate("1200", "40", "30", "20")),
                        "https://example.com/v2-measurement", "medicion_logistica", "Fuente histórica válida del mismo paquete.",
                        "Aprobación v2", "approved", application.now(), application.now()))
                application.init_db()
                with application.connect() as c:
                    self.assertEqual(application.get_settings(c)["engine_revision"], 3)
                    migrated = c.execute("SELECT raw_json,raw_hash FROM skus WHERE sku='APPROVED'").fetchone()
                    self.assertEqual(json.loads(migrated["raw_json"]), approved)
                    self.assertEqual(migrated["raw_hash"], canonical_hash(approved))
                    proposal = c.execute("SELECT base_hash,candidate_hash,state FROM proposals WHERE sku_id=?", (sku_id,)).fetchone()
                    self.assertEqual((proposal["base_hash"], proposal["candidate_hash"], proposal["state"]),
                                     (canonical_hash(approved), canonical_hash(values), "approved"))
                self.assertEqual(self.detail("APPROVED")["record"]["size"], "S")
                legacy_detail = self.detail("LEGACY")["record"]
                self.assertEqual(legacy_detail["category_check"]["status"], "no_category")
                self.assertEqual(legacy_detail["direction"], "unknown")
                self.assertIn("billed_size_invalid", legacy_detail["reason_codes"])
                self.assertEqual(len(self.csv_rows("/api/export.csv")), 1)
                job = self.upload([approved])
                self.assertEqual((job["unchanged"], job["updated"]), (1, 0))
                self.assertEqual(self.detail("APPROVED")["proposal"]["state"], "approved")
            finally:
                application.DATA, application.DB, application.UPLOADS = saved_paths


if __name__ == "__main__":
    unittest.main()
