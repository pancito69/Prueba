"""Behavioral tests for calculations, ingestion and evidence-based correction.

Run from the repository: python -m unittest discover -s tests -v
All catalogues, sources and identifiers below are fictitious; no network calls.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEST_DATA = tempfile.TemporaryDirectory(prefix="talla-tests-")
os.environ["TALLA_DATA_DIR"] = TEST_DATA.name

from fastapi.testclient import TestClient
import app as application
from engine import BANDS, ValidationError, calculate, canonical_hash, identity_key, parse_number


def product(seller="Seller A", sku="SKU-1", **overrides):
    row = dict(seller=seller, sku=sku, name="Producto ficticio", gtin="",
               brand="Marca de prueba", model="Modelo-1", variant="Única",
               presentation="Unidad", packaging="Caja comercial de envío",
               weight_g="1200", height_cm="100", length_cm="30", width_cm="20",
               reference_verified="false", source_url="", evidence_kind="")
    row.update(overrides)
    return row


def reference(**overrides):
    row = product("Seller referencia", "REF-1", height_cm="40",
                  reference_verified="true", source_url="https://example.com/empaque",
                  evidence_kind="empaque_envio")
    row.update(overrides)
    return row


class RulesTests(unittest.TestCase):
    def test_grams_volume_and_largest_weight(self):
        result = calculate("1200", "100", "30", "20")
        self.assertEqual((result["real_kg"], result["volume_kg"], result["charge_kg"], result["size"]),
                         ("1.2", "12", "12", "M"))
        corrected = calculate("1200", "40", "30", "20")
        self.assertEqual((corrected["volume_kg"], corrected["size"]), ("4.8", "S"))
        self.assertEqual(calculate("1200", "1", "1", "1")["charge_kg"], "1.2")

    def test_literal_band_upper_and_lower_boundaries(self):
        from decimal import Decimal
        for band in BANDS:
            for boundary in (band["min"], band["max"]):
                if boundary == "0":
                    continue
                with self.subTest(size=band["size"], kg=boundary):
                    result = calculate(str(Decimal(boundary) * 1000), "1", "1", "1")
                    self.assertEqual(result["size"], band["size"])

    def test_gaps_are_not_rounded_into_a_band(self):
        for grams in ("500.0001", "505", "1050", "3050", "8050", "25050", "40050", "50050", "260050", "300050"):
            with self.subTest(grams=grams):
                result = calculate(grams, "1", "1", "1")
                self.assertIsNone(result["size"])
                self.assertEqual(result["status"], "pending")
        self.assertEqual(calculate("1050", "1", "1", "1", "upper_bounds")["size"], "XS")
        self.assertIsNone(calculate("500001", "1", "1", "1", "upper_bounds")["size"])

    def test_invalid_measurements_cannot_be_classified(self):
        for value in ("", "0", "-1", "NaN", "Infinity", None, True, "1,200.50"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    calculate(value, "10", "10", "10")
        self.assertEqual(str(parse_number("1,25")), "1.25")

    def test_identity_requires_explicit_matching_presentation(self):
        a = product()
        self.assertEqual(identity_key(a), identity_key(product("Other", "Other")))
        self.assertNotEqual(identity_key(a), identity_key(product(presentation="Pack x 3")))
        self.assertIsNone(identity_key(product(packaging="")))
        self.assertIsNone(identity_key(product(gtin="1234567890123")))
        self.assertEqual(canonical_hash(product(weight_g="1200")), canonical_hash(product(weight_g="1200,0")))

    def test_metrics_identity_preserves_all_decimal_digits(self):
        first = product(weight_g="1.12345678901234567890123456789")
        second = product(weight_g="1.12345678901234567890123456788")
        self.assertNotEqual(application.metrics_key(first), application.metrics_key(second))


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(application.app)
        cls.client = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        TEST_DATA.cleanup()

    def setUp(self):
        application.TASKS.join()
        with application.connect() as db:
            for table in ("history", "proposals", "skus", "jobs", "uploads"):
                db.execute(f"DELETE FROM {table}")
            db.execute("UPDATE settings SET rule_mode='strict',version=1 WHERE id=1")

    def wait(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] in {"completed", "failed"}:
                application.TASKS.join()
                self.assertEqual(job["status"], "completed", job)
                return job
            time.sleep(.01)
        self.fail("La carga no terminó en diez segundos")

    def upload(self, rows, trust=False, xlsx=False):
        if xlsx:
            import openpyxl
            workbook = openpyxl.Workbook()
            workbook.active.append(application.FIELDS)
            for row in rows:
                workbook.active.append([row.get(k, "") for k in application.FIELDS])
            stream = io.BytesIO()
            workbook.save(stream)
            payload = stream.getvalue()
            filename = "prueba.xlsx"
        else:
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=application.FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            payload = stream.getvalue().encode("utf-8")
            filename = "prueba.csv"
        response = self.client.post("/api/uploads/preview", files={"file": (filename, payload)})
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(preview["headers"], application.FIELDS)
        response = self.client.post(f"/api/uploads/{preview['upload_id']}/process", json={
            "mapping": {k: k for k in application.FIELDS}, "trust_references": trust})
        self.assertEqual(response.status_code, 200, response.text)
        return self.wait(response.json()["job_id"])

    def get_sku(self, seller="Seller A", sku="SKU-1"):
        items = self.client.get("/api/skus", params={"search": sku}).json()["items"]
        row = next(item for item in items if item["seller"] == seller and item["sku"] == sku)
        return self.client.get(f"/api/skus/{row['id']}").json()

    def correction(self, sku_id, **overrides):
        values = dict(weight_g="1200", height_cm="40", length_cm="30", width_cm="20",
                      source_url="https://example.com/medicion", source_kind="medicion_logistica",
                      evidence_note="Medición ficticia del mismo producto y paquete de envío.",
                      reason="Corrección respaldada por medición")
        values.update(overrides)
        response = self.client.post(f"/api/skus/{sku_id}/proposal", json=values)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def approve(self, proposal_id):
        return self.client.post(f"/api/proposals/{proposal_id}/decision", json={"decision": "approve", "note": "Evidencia revisada"})

    def export_rows(self):
        response = self.client.get("/api/export.csv")
        self.assertEqual(response.status_code, 200)
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_csv_import_repeat_is_idempotent_and_persistent(self):
        first = self.upload([product()])
        second = self.upload([product(weight_g="1200.0")])
        self.assertEqual((first["inserted"], second["inserted"], second["unchanged"]), (1, 0, 1))
        self.assertEqual(self.client.get("/api/summary").json()["total"], 1)
        self.assertEqual(self.get_sku()["record"]["size"], "M")
        with application.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM skus").fetchone()[0], 1)

    def test_xlsx_import_and_decimal_comma(self):
        job = self.upload([product(height_cm="40,5")], xlsx=True)
        self.assertEqual(job["inserted"], 1)
        self.assertEqual(self.get_sku()["record"]["volume_kg"], "4.86")

    def test_duplicate_key_reports_error_and_keeps_first(self):
        job = self.upload([product(), product(height_cm="40")])
        self.assertEqual((job["processed"], job["inserted"], job["errors"]), (2, 1, 1))
        self.assertTrue(job["error_examples"])
        self.assertEqual(self.get_sku()["record"]["height_cm"], "100")

    def test_missing_physical_data_remains_pending(self):
        job = self.upload([product(height_cm="")])
        self.assertEqual(job["inserted"], 1)
        detail = self.get_sku()
        self.assertEqual(detail["record"]["status"], "pending")
        self.assertIsNone(detail["record"]["size"])

    def test_reference_trust_requires_user_attestation(self):
        self.upload([product(), reference()], trust=False)
        self.assertIsNone(self.get_sku()["proposal"])
        self.upload([product(), reference()], trust=True)
        self.assertEqual(self.get_sku()["proposal"]["state"], "pending")

    def test_reference_without_empaque_identity_is_not_used(self):
        self.upload([product(packaging=""), reference(packaging="")], trust=True)
        self.assertIsNone(self.get_sku()["proposal"])

    def test_reference_from_other_seller_is_selected_when_first_is_same_seller(self):
        own_reference = reference(seller="Seller A", sku="REF-A")
        other_reference = reference(seller="Seller B", sku="REF-B")
        self.upload([own_reference, other_reference, product()], trust=True)
        target = self.get_sku()
        other = self.get_sku("Seller B", "REF-B")
        self.assertIsNotNone(target["proposal"])
        self.assertEqual(target["proposal"]["reference_id"], other["record"]["id"])
        self.assertEqual(target["proposal"]["size"], "S")

    def test_conflicting_verified_sources_do_not_generate_proposal(self):
        self.upload([product(), reference(), reference(seller="Tercera fuente", sku="REF-2", height_cm="50")], trust=True)
        self.assertIsNone(self.get_sku()["proposal"])

    def test_reference_proposal_approval_and_export(self):
        self.upload([product(), reference()], trust=True)
        detail = self.get_sku()
        self.assertEqual(detail["record"]["size"], "M")
        self.assertEqual(detail["proposal"]["size"], "S")
        self.assertEqual(self.export_rows(), [])
        self.assertEqual(self.approve(detail["proposal"]["id"]).status_code, 200)
        corrected = self.get_sku()
        self.assertEqual(corrected["record"]["size"], "S")
        self.assertEqual(corrected["original"]["height_cm"], "100")
        rows = self.export_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["Talla"], rows[0]["Talla anterior"]), ("S", "M"))
        self.assertEqual(self.approve(detail["proposal"]["id"]).status_code, 409)

    def test_summary_counts_approved_size_instead_of_original(self):
        self.upload([product(), reference()], trust=True)
        proposal_id = self.get_sku()["proposal"]["id"]
        self.assertEqual(self.approve(proposal_id).status_code, 200)
        summary = self.client.get("/api/summary").json()
        self.assertEqual(summary["by_size"], {"S": 2})
        self.assertEqual(summary["approved"], 1)

    def test_corrected_reference_is_revoked_and_derived_approval_not_exported(self):
        self.upload([product(), reference()], trust=True)
        target = self.get_sku()
        self.assertEqual(self.approve(target["proposal"]["id"]).status_code, 200)
        source = self.get_sku("Seller referencia", "REF-1")
        manual_id = self.correction(source["record"]["id"], height_cm="50")
        response = self.approve(manual_id)
        self.assertEqual(response.status_code, 200, response.text)
        source_after = self.get_sku("Seller referencia", "REF-1")
        self.assertFalse(source_after["record"]["is_reference"])
        self.assertEqual(source_after["record"]["height_cm"], "50")
        exported = self.export_rows()
        self.assertFalse(any(row["SKU"] == "SKU-1" for row in exported), exported)
        self.assertTrue(any(row["SKU"] == "REF-1" for row in exported), exported)
        self.assertNotEqual(self.get_sku()["record"]["status"], "approved")

    def test_partial_import_conflict_blocks_export_and_pending_decision(self):
        self.upload([product(), product(sku="SKU-2"), reference()], trust=True)
        approved = self.get_sku()["proposal"]["id"]
        pending = self.get_sku(sku="SKU-2")["proposal"]["id"]
        self.assertEqual(self.approve(approved).status_code, 200)
        conflicting = reference(seller="Otra fuente verificada", sku="REF-CONFLICT", height_cm="50")
        calc = application.calculate_record(conflicting, "strict")
        # Simulate a committed import block before its final invalidation pass.
        # Export and decision must independently check current source conflicts.
        with application.connect() as db:
            db.execute("""INSERT INTO skus(seller,sku,name,identity_key,raw_json,raw_hash,
                metrics_key,calculated,status,is_reference,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                    conflicting["seller"], conflicting["sku"], conflicting["name"],
                    identity_key(conflicting), json.dumps(conflicting), canonical_hash(conflicting),
                    application.metrics_key(conflicting), json.dumps(calc), calc["status"], 1,
                    application.now()))
        self.assertEqual(self.export_rows(), [])
        response = self.approve(pending)
        self.assertEqual(response.status_code, 409, response.text)

    def test_changed_catalog_cannot_approve_stale_proposal(self):
        self.upload([product(), reference()], trust=True)
        old_id = self.get_sku()["proposal"]["id"]
        self.upload([product(height_cm="90")], trust=True)
        self.assertEqual(self.approve(old_id).status_code, 409)

    def test_changed_reference_invalidates_approval_and_export(self):
        self.upload([product(), reference()], trust=True)
        old_id = self.get_sku()["proposal"]["id"]
        self.assertEqual(self.approve(old_id).status_code, 200)
        self.upload([reference(height_cm="50")], trust=True)
        self.assertEqual(self.export_rows(), [])
        current = self.get_sku()
        self.assertNotEqual(current["record"]["status"], "approved")
        self.assertNotEqual(current["proposal"]["id"], old_id)

    def test_rejected_proposal_is_not_recreated_by_identical_import(self):
        rows = [product(), reference()]
        self.upload(rows, trust=True)
        proposal_id = self.get_sku()["proposal"]["id"]
        decision = self.client.post(f"/api/proposals/{proposal_id}/decision", json={"decision": "reject", "note": "Empaque no confirmado"})
        self.assertEqual(decision.status_code, 200)
        self.upload(rows, trust=True)
        detail = self.get_sku()
        self.assertEqual((detail["proposal"]["id"], detail["proposal"]["state"]), (proposal_id, "rejected"))

    def test_manual_evidence_and_formula_injection_export(self):
        self.upload([product(sku="=2+2")])
        detail = self.get_sku(sku="=2+2")
        proposal_id = self.correction(detail["record"]["id"])
        self.assertEqual(self.approve(proposal_id).status_code, 200)
        self.assertEqual(self.export_rows()[0]["SKU"], "'=2+2")
        invalid = self.client.post(f"/api/skus/{detail['record']['id']}/proposal", json={
            "weight_g": "1", "height_cm": "1", "length_cm": "1", "width_cm": "1",
            "source_url": "javascript:alert(1)", "source_kind": "fabricante",
            "evidence_note": "Una evidencia de ejemplo suficiente.", "reason": "Prueba inválida"})
        self.assertEqual(invalid.status_code, 422)

    def test_rule_change_recalculates_and_invalidates_prior_approval(self):
        self.upload([product()])
        sku_id = self.get_sku()["record"]["id"]
        proposal_id = self.correction(sku_id)
        self.assertEqual(self.approve(proposal_id).status_code, 200)
        response = self.client.put("/api/settings", json={"rule_mode": "upper_bounds"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rule_mode"], "upper_bounds")
        self.assertEqual(self.export_rows(), [])
        self.assertEqual(self.get_sku()["proposal"]["state"], "stale")


if __name__ == "__main__":
    unittest.main()
