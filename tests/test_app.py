"""Behavioral tests for calculations, ingestion and evidence-based correction.

Run from the repository: python -m unittest discover -s tests -v
All catalogues, sources and identifiers below are fictitious; no network calls.
"""
from __future__ import annotations

import csv
from hashlib import sha256
import io
import json
import os
import sqlite3
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


def legacy_v1_hash(record):
    """Frozen canonical_hash from engine.py at a077591, before billed_size.

    Keep this field list independent of today's engine so the migration fixture
    exercises an actual algorithm change rather than writing v2 hashes to v1.
    """
    fields = (
        "seller", "sku", "name", "gtin", "brand", "model", "variant", "presentation",
        "packaging", "weight_g", "height_cm", "length_cm", "width_cm",
        "reference_verified", "source_url", "evidence_kind",
    )
    normalized = {}
    for field in fields:
        value = record.get(field)
        if field in {"weight_g", "height_cm", "length_cm", "width_cm"}:
            try:
                text = format(parse_number(value, field), "f")
                if "." in text:
                    text = text.rstrip("0").rstrip(".")
                normalized[field] = "0" if text in {"-0", ""} else text
            except ValidationError:
                normalized[field] = str(value or "").strip()
        elif field == "reference_verified":
            normalized[field] = value is True or str(value).strip().casefold() in {
                "1", "true", "si", "sí", "yes", "verificado"}
        else:
            normalized[field] = str(value or "").strip()
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def product(seller="Seller A", sku="SKU-1", **overrides):
    row = dict(seller=seller, sku=sku, name="Producto ficticio", gtin="",
               brand="Marca de prueba", model="Modelo-1", variant="Única",
               presentation="Unidad", packaging="Caja comercial de envío",
               weight_g="1200", height_cm="100", length_cm="30", width_cm="20",
               reference_verified="false", source_url="", evidence_kind="", billed_size="")
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

    def test_long_dimension_is_an_observation_not_an_error(self):
        result = calculate("2000", "320", "10", "10")
        self.assertEqual(result["size"], "S")
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["flags"], [])
        self.assertIn("long_dimension", result["observation_codes"])
        self.assertTrue(result["observations"])

    def test_volume_ratio_threshold_is_configurable_and_does_not_change_size(self):
        default = calculate("10", "100", "10", "10")
        adjusted = calculate("10", "100", "10", "10", volume_ratio_threshold="250")
        self.assertEqual(default["size"], adjusted["size"])
        self.assertIn("volume_weight_ratio", default["reason_codes"])
        self.assertEqual(default["status"], "review")
        self.assertEqual(adjusted["status"], "valid")
        self.assertNotIn("volume_weight_ratio", adjusted["reason_codes"])
        for invalid in ("0", "-1", "NaN", "1000001"):
            with self.subTest(threshold=invalid), self.assertRaises(ValidationError):
                calculate("10", "100", "10", "10", volume_ratio_threshold=invalid)


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
            db.execute("UPDATE settings SET rule_mode='strict',version=1,volume_ratio_threshold='100' WHERE id=1")

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
        self.assertEqual(self.client.get("/api/summary").json()["approved"], 0)
        conflicted = self.get_sku()
        self.assertNotEqual(conflicted["record"]["status"], "approved")
        self.assertEqual(conflicted["record"]["size"], "M")
        self.assertEqual(conflicted["proposal"]["state"], "stale")
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

    def test_direction_filter_uses_explicit_charging_size_only(self):
        self.upload([
            product(sku="UP", billed_size="XXS"),
            product(sku="DOWN", billed_size="XL"),
            product(sku="SAME", billed_size="M"),
            product(sku="UNKNOWN"),
            product(sku="LEGACY", billed_size="Clase antigua 4"),
        ])
        expected = {"up": {"UP"}, "down": {"DOWN"}, "same": {"SAME"},
                    "unknown": {"UNKNOWN", "LEGACY"}}
        for direction, expected_skus in expected.items():
            with self.subTest(direction=direction):
                response = self.client.get("/api/skus", params={"direction": direction})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual({item["sku"] for item in response.json()["items"]}, expected_skus)
        legacy = self.get_sku(sku="LEGACY")["record"]
        self.assertIn("billed_size_invalid", legacy["reason_codes"])
        self.assertEqual(legacy["direction"], "unknown")

    def test_proposal_direction_uses_billed_size_when_available(self):
        self.upload([product(), product(sku="BILLED", billed_size="XXS"), reference()], trust=True)
        original_comparison = self.get_sku()["record"]
        billed_comparison = self.get_sku(sku="BILLED")["record"]
        self.assertEqual(original_comparison["direction"], "down")
        self.assertEqual(billed_comparison["direction"], "up")

    def test_settings_threshold_recalculates_alerts(self):
        self.upload([product(weight_g="10", height_cm="100", length_cm="10", width_cm="10")])
        self.assertEqual(self.get_sku()["record"]["status"], "review")
        response = self.client.put("/api/settings", json={"rule_mode": "strict", "volume_ratio_threshold": "250"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(str(response.json()["volume_ratio_threshold"]), "250")
        self.assertEqual(self.get_sku()["record"]["status"], "valid")
        self.assertEqual(self.get_sku()["record"]["size"], "XS")
        settings = self.client.get("/api/settings").json()
        self.assertEqual(str(settings["volume_ratio_threshold"]), "250")

    def test_diagnostics_separates_alerts_observations_and_research_work(self):
        self.upload([
            product(sku="RATIO-1", weight_g="10", height_cm="100", length_cm="10", width_cm="10"),
            product("Seller B", "RATIO-2", weight_g="10", height_cm="100", length_cm="10", width_cm="10"),
            product(sku="=bad()", height_cm="", brand="", model="", variant="", packaging=""),
            product(sku="LONG", weight_g="2000", height_cm="320", length_cm="10", width_cm="10"),
            product(sku="LEGACY", billed_size="Clase antigua 4"),
        ])
        response = self.client.get("/api/diagnostics")
        self.assertEqual(response.status_code, 200, response.text)
        diagnostics = response.json()
        self.assertEqual((diagnostics["total"], diagnostics["attention"]), (5, 4))
        counts = {reason["code"]: reason["count"] for reason in diagnostics["reasons"]}
        self.assertEqual(counts["volume_weight_ratio"], 2)
        self.assertEqual(counts["invalid_physical_data"], 1)
        self.assertEqual(counts["billed_size_invalid"], 1)
        self.assertNotIn("long_dimension", counts)
        self.assertEqual(diagnostics["research"], {
            "attention_rows": 4, "identifiable_rows": 3, "unique_products": 1,
            "missing_identity_rows": 1, "with_proposal_rows": 0, "without_proposal_rows": 4})
        self.assertEqual(diagnostics["directions"]["unknown"], 5)
        self.assertEqual(diagnostics["verification"]["unverified"], 5)
        self.assertEqual(diagnostics["ai"], {"enabled": False, "external_requests": 0})
        csv_response = self.client.get("/api/diagnostics.csv")
        self.assertEqual(csv_response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 4)
        self.assertNotIn("LONG", {row["SKU"] for row in rows})
        self.assertIn("'=bad()", {row["SKU"] for row in rows})
        all_response = self.client.get("/api/diagnostics.csv", params={"scope": "all"})
        all_rows = list(csv.DictReader(io.StringIO(all_response.content.decode("utf-8-sig"))))
        self.assertEqual(len(all_rows), 5)
        long_row = next(row for row in all_rows if row["SKU"] == "LONG")
        self.assertTrue(long_row["Observaciones"])
        self.assertEqual(long_row["Alertas"], "")

    def test_group_decision_approves_same_product_and_evidence_atomically(self):
        self.upload([product(), product("Seller B", "SKU-2"), reference()], trust=True)
        groups_response = self.client.get("/api/groups")
        self.assertEqual(groups_response.status_code, 200, groups_response.text)
        groups = groups_response.json()["items"]
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual((group["count"], group["seller_count"]), (2, 2))
        self.assertEqual(group["direction"], "down")
        self.assertEqual(len(group["proposal_ids"]), 2)
        response = self.client.post("/api/groups/decision", json={
            "proposal_ids": group["proposal_ids"], "decision": "approve", "note": "Evidencia común revisada"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.export_rows()), 2)
        self.assertEqual(self.get_sku()["proposal"]["state"], "approved")
        self.assertEqual(self.get_sku("Seller B", "SKU-2")["proposal"]["state"], "approved")

    def test_group_decision_rejects_different_products_without_partial_approval(self):
        self.upload([product(), product(sku="SKU-2", model="Otro modelo")])
        first = self.correction(self.get_sku()["record"]["id"])
        second = self.correction(self.get_sku(sku="SKU-2")["record"]["id"])
        response = self.client.post("/api/groups/decision", json={
            "proposal_ids": [first, second], "decision": "approve", "note": "No mezclar productos"})
        self.assertIn(response.status_code, {409, 422}, response.text)
        self.assertEqual(self.export_rows(), [])
        self.assertEqual(self.get_sku()["proposal"]["state"], "pending")
        self.assertEqual(self.get_sku(sku="SKU-2")["proposal"]["state"], "pending")

    def test_group_decision_rejects_different_evidence_without_partial_approval(self):
        self.upload([product(), product(sku="SKU-2")])
        first = self.correction(self.get_sku()["record"]["id"])
        second = self.correction(self.get_sku(sku="SKU-2")["record"]["id"], source_url="https://example.com/otra-medicion")
        response = self.client.post("/api/groups/decision", json={
            "proposal_ids": [first, second], "decision": "approve", "note": "No mezclar evidencia"})
        self.assertIn(response.status_code, {409, 422}, response.text)
        self.assertEqual(self.export_rows(), [])
        self.assertEqual(self.get_sku()["proposal"]["state"], "pending")

    def test_group_decision_rolls_back_if_one_proposal_is_stale(self):
        self.upload([product(), product(sku="SKU-2"), reference()], trust=True)
        first = self.get_sku()["proposal"]["id"]
        second = self.get_sku(sku="SKU-2")["proposal"]["id"]
        with application.connect() as db:
            db.execute("UPDATE proposals SET state='stale' WHERE id=?", (second,))
        response = self.client.post("/api/groups/decision", json={
            "proposal_ids": [first, second], "decision": "approve", "note": "Comprobar atomicidad"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.export_rows(), [])
        self.assertEqual(self.get_sku()["proposal"]["state"], "pending")

    def test_migration_from_v1_preserves_approval_and_reclassifies_long_dimension(self):
        saved_paths = application.DATA, application.DB, application.UPLOADS
        with tempfile.TemporaryDirectory(prefix="talla-v1-") as directory:
            try:
                application.DATA = Path(directory)
                application.DB = Path(directory) / "talla.sqlite3"
                application.UPLOADS = Path(directory) / "uploads"
                long_raw = product(sku="LONG", weight_g="2000", height_cm="320", length_cm="10", width_cm="10")
                approved_raw = product(sku="APPROVED")
                for raw in (long_raw, approved_raw):
                    raw.pop("billed_size", None)
                long_old = calculate("2000", "320", "10", "10")
                long_old.update(status="review", flags=["Alguna dimensión supera 300 cm: verificar el empaque de envío."])
                for key in ("observations", "observation_codes", "reason_codes"):
                    long_old.pop(key, None)
                approved_calc = calculate("1200", "100", "30", "20")
                proposed_values = dict(weight_g="1200", height_cm="40", length_cm="30", width_cm="20")
                proposed_calc = calculate("1200", "40", "30", "20")
                self.assertNotEqual(legacy_v1_hash(approved_raw), canonical_hash(approved_raw))
                self.assertNotEqual(legacy_v1_hash(proposed_values), canonical_hash(proposed_values))
                with sqlite3.connect(application.DB) as db:
                    db.executescript("""
                    CREATE TABLE settings(id INTEGER PRIMARY KEY,rule_mode TEXT NOT NULL,version INTEGER NOT NULL);
                    INSERT INTO settings VALUES(1,'strict',1);
                    CREATE TABLE skus(id INTEGER PRIMARY KEY,seller TEXT NOT NULL,sku TEXT NOT NULL,name TEXT,
                      identity_key TEXT,raw_json TEXT NOT NULL,raw_hash TEXT NOT NULL,metrics_key TEXT,calculated TEXT NOT NULL,
                      status TEXT NOT NULL,is_reference INTEGER DEFAULT 0,job_id TEXT,updated_at TEXT,UNIQUE(seller,sku));
                    CREATE TABLE proposals(id INTEGER PRIMARY KEY,sku_id INTEGER NOT NULL REFERENCES skus(id),base_hash TEXT NOT NULL,
                      candidate_hash TEXT NOT NULL,rule_version INTEGER NOT NULL,values_json TEXT NOT NULL,calculated TEXT NOT NULL,
                      source_url TEXT NOT NULL,source_kind TEXT NOT NULL,evidence_note TEXT NOT NULL,reason TEXT NOT NULL,
                      reference_id INTEGER,reference_hash TEXT,state TEXT NOT NULL,created_at TEXT NOT NULL,decided_at TEXT,decision_note TEXT);
                    """)
                    for raw, calc, status in ((long_raw, long_old, "review"), (approved_raw, approved_calc, "approved")):
                        db.execute("""INSERT INTO skus(seller,sku,name,identity_key,raw_json,raw_hash,metrics_key,
                            calculated,status,is_reference,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                                raw["seller"], raw["sku"], raw["name"], identity_key(raw), json.dumps(raw),
                                legacy_v1_hash(raw), application.metrics_key(raw), json.dumps(calc), status, 0,
                                application.now()))
                    db.execute("""INSERT INTO proposals(sku_id,base_hash,candidate_hash,rule_version,values_json,calculated,
                        source_url,source_kind,evidence_note,reason,state,created_at,decided_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (2, legacy_v1_hash(approved_raw), legacy_v1_hash(proposed_values),
                            1, json.dumps(proposed_values), json.dumps(proposed_calc), "https://example.com/v1",
                            "medicion_logistica", "Evidencia previa válida del mismo paquete.", "Corrección aprobada en v1",
                            "approved", application.now(), application.now()))
                application.init_db()
                with application.connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM skus").fetchone()[0], 2)
                    settings = application.get_settings(db)
                    self.assertEqual(str(settings["volume_ratio_threshold"]), "100")
                    long_row = db.execute("SELECT status,calculated,raw_json FROM skus WHERE sku='LONG'").fetchone()
                    self.assertEqual(long_row["status"], "valid")
                    self.assertIn("long_dimension", json.loads(long_row["calculated"])["observation_codes"])
                    self.assertEqual(json.loads(long_row["raw_json"]), long_raw)
                    proposal = db.execute("SELECT state,base_hash,candidate_hash FROM proposals WHERE sku_id=2").fetchone()
                    self.assertEqual(proposal["state"], "approved")
                    self.assertEqual(proposal["base_hash"], canonical_hash(approved_raw))
                    self.assertEqual(proposal["candidate_hash"], canonical_hash(proposed_values))
                    self.assertEqual(db.execute("SELECT raw_hash FROM skus WHERE sku='APPROVED'").fetchone()[0], canonical_hash(approved_raw))
                rows = self.export_rows()
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]["SKU"], rows[0]["Talla"]), ("APPROVED", "S"))
                reimported = self.upload([approved_raw])
                self.assertEqual((reimported["unchanged"], reimported["updated"], reimported["inserted"]), (1, 0, 0))
                migrated_detail = self.get_sku(sku="APPROVED")
                self.assertEqual(migrated_detail["proposal"]["state"], "approved")
                self.assertEqual(migrated_detail["record"]["size"], "S")
                self.assertEqual(self.export_rows(), rows)
            finally:
                application.DATA, application.DB, application.UPLOADS = saved_paths


if __name__ == "__main__":
    unittest.main()
