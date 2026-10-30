"""Provisional local statistics: robust bounds, abstention and safe application."""
from __future__ import annotations

from decimal import Decimal
import io
import csv
import json
import time
import unittest
import uuid

import app as application
from category_profiler import fingerprint, generate
from categories import normalize_scope
from engine import canonical_hash
from tests.test_categories import IsolatedAPI, row


def population(count=60, *, prefix="P", **changes):
    records = []
    for index in range(count):
        record = row(seller=f"Seller {index % 3}", sku=f"{prefix}-{index}", model=f"{prefix}-modelo-{index}",
                     weight_g=str(1000 + (index % 30) * 20), height_cm=str(40 + index % 20),
                     length_cm="20", width_cm="20")
        record.update(changes)
        records.append(record)
    return records


class CategoryProfileStatisticsTests(IsolatedAPI, unittest.TestCase):
    def profile(self):
        run_id = uuid.uuid4().hex
        with application.connect() as c:
            summary = generate(c, run_id, {"min_count": 30, "min_sellers": 3})
            suggestions = []
            for item in c.execute("SELECT * FROM profile_suggestions WHERE run_id=? ORDER BY id", (run_id,)):
                suggestion = dict(item)
                for field in ("counts", "bounds", "method"):
                    suggestion[field] = json.loads(suggestion[field + "_json"])
                suggestions.append(suggestion)
        return summary, suggestions

    def exact_scope(self, suggestions, *, category="Electrónica", subcategory="Audio", presentation="Unidad"):
        found = [s for s in suggestions if normalize_scope(s["category"]) == normalize_scope(category)
                 and normalize_scope(s["subcategory"]) == normalize_scope(subcategory)
                 and normalize_scope(s["presentation"]) == normalize_scope(presentation)]
        self.assertEqual(len(found), 1, suggestions)
        return found[0]

    def test_extreme_outlier_does_not_set_the_provisional_range(self):
        records = population()
        records.append(row("Seller 0", "OUTLIER", model="OUTLIER", weight_g="100000", height_cm="4000", length_cm="20", width_cm="20"))
        self.upload(records)
        with application.connect() as c:
            originals = [tuple(r) for r in c.execute("SELECT id,raw_json,raw_hash FROM skus ORDER BY id")]
        _, suggestions = self.profile()
        suggestion = self.exact_scope(suggestions)
        self.assertEqual(suggestion["state"], "ready")
        self.assertLess(Decimal(suggestion["bounds"]["real_max_kg"]), Decimal(10))
        self.assertLess(Decimal(suggestion["bounds"]["volume_max_kg"]), Decimal(20))
        self.assertEqual(suggestion["source"], "catalog_statistics")
        self.assertEqual(suggestion["method"]["method"], "log10_tukey_outer_fences")
        with application.connect() as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT id,raw_json,raw_hash FROM skus ORDER BY id")], originals)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)

    def test_too_few_unique_products_abstains_even_with_many_seller_rows(self):
        records = []
        for item in population(10):
            for seller in range(3):
                records.append({**item, "seller": f"Seller {seller}", "sku": item["sku"] + f"-{seller}"})
        records += population(30, prefix="SOLO", category="Hogar", seller="Seller único")
        self.upload(records)
        _, suggestions = self.profile()
        suggestion = self.exact_scope(suggestions)
        self.assertNotEqual(suggestion["state"], "ready")
        self.assertEqual(suggestion["counts"]["entities"], 10)
        self.assertTrue(all(value is None for value in suggestion["bounds"].values()))
        single_seller = self.exact_scope(suggestions, category="Hogar")
        self.assertNotEqual(single_seller["state"], "ready")
        self.assertEqual(single_seller["counts"]["entities"], 30)
        self.assertTrue(all(value is None for value in single_seller["bounds"].values()))

    def test_degenerate_distribution_does_not_invent_limits(self):
        self.upload(population(weight_g="1000", height_cm="40"))
        _, suggestions = self.profile()
        suggestion = self.exact_scope(suggestions)
        self.assertNotEqual(suggestion["state"], "ready")
        self.assertTrue(all(value is None for value in suggestion["bounds"].values()))
        for metric in ("real", "volume"):
            self.assertEqual(suggestion["method"]["metrics"][metric]["state"], "degenerate")

    def test_heterogeneous_distribution_abstains(self):
        records = []
        for index in range(60):
            low = index < 30
            records.append(row(f"Seller {index % 3}", f"H-{index}", model=f"H-{index}",
                               weight_g=str(1 + index / 100) if low else str(100000 + index * 1000),
                               height_cm="1" if low else "100", length_cm="1" if low else "100", width_cm="1" if low else "50"))
        self.upload(records)
        _, suggestions = self.profile()
        suggestion = self.exact_scope(suggestions)
        self.assertNotEqual(suggestion["state"], "ready")
        self.assertTrue(all(value is None for value in suggestion["bounds"].values()))
        self.assertEqual(suggestion["method"]["metrics"]["real"]["state"], "heterogeneous")

    def test_each_valid_metric_supports_only_its_own_bounds_without_imputing_missing_fields(self):
        self.upload(population(height_cm="") + population(prefix="VOLUME", category="Hogar", weight_g=""))
        _, suggestions = self.profile()
        suggestion = self.exact_scope(suggestions)
        self.assertEqual(suggestion["state"], "ready")
        self.assertIsNotNone(suggestion["bounds"]["real_min_kg"])
        self.assertIsNotNone(suggestion["bounds"]["real_max_kg"])
        self.assertIsNone(suggestion["bounds"]["volume_min_kg"])
        self.assertIsNone(suggestion["bounds"]["volume_max_kg"])
        self.assertEqual(suggestion["method"]["metrics"]["volume"]["state"], "insufficient")
        detail = self.detail("P-0")
        self.assertEqual(detail["original"]["height_cm"], "")
        self.assertEqual(detail["record"]["status"], "pending")
        self.assertIsNone(detail["record"]["real_kg"])
        self.assertIsNone(detail["record"]["volume_kg"])
        volume = self.exact_scope(suggestions, category="Hogar")
        self.assertEqual(volume["state"], "ready")
        self.assertIsNone(volume["bounds"]["real_min_kg"])
        self.assertIsNone(volume["bounds"]["real_max_kg"])
        self.assertIsNotNone(volume["bounds"]["volume_min_kg"])
        self.assertIsNotNone(volume["bounds"]["volume_max_kg"])
        self.assertEqual(volume["method"]["metrics"]["real"]["state"], "insufficient")
        missing_weight = self.detail("VOLUME-0")
        self.assertEqual(missing_weight["original"]["weight_g"], "")
        self.assertEqual(missing_weight["record"]["status"], "pending")
        self.assertIsNone(missing_weight["record"]["real_kg"])
        self.assertIsNone(missing_weight["record"]["volume_kg"])

    def test_declared_reference_cohort_is_preferred_despite_untrusted_conflicts(self):
        anchors = population(30, prefix="ANCHOR")
        records = []
        for index, anchor in enumerate(anchors):
            anchor.update(seller="Seller ancla", weight_g=str(40000 + index * 1000),
                          reference_verified="true", source_url="https://example.com/shipping-package", evidence_kind="empaque_envio")
            records.extend([anchor, {**anchor, "seller": f"Seller {index % 3}", "sku": "RAW-" + str(index),
                                     "weight_g": str(1000 + index * 20), "reference_verified": "false", "source_url": "", "evidence_kind": ""}])
        self.upload(records)
        # Pure profiling fixture: explicitly designate measured reference records;
        # importer-attestation behavior is independently covered by test_app.py.
        with application.connect() as c:
            for anchor in anchors:
                calc = application.calculate_record(anchor, "strict")
                c.execute("UPDATE skus SET raw_json=?,raw_hash=?,calculated=?,is_reference=1 WHERE seller=? AND sku=?", (
                    json.dumps(anchor), canonical_hash(anchor), json.dumps(calc), anchor["seller"], anchor["sku"]))
        _, suggestions = self.profile()
        suggestion = self.exact_scope(suggestions)
        self.assertEqual(suggestion["state"], "ready")
        self.assertEqual(suggestion["source"], "declared_references")
        self.assertGreater(Decimal(suggestion["bounds"]["real_min_kg"]), Decimal(10))
        self.assertEqual(suggestion["counts"]["metrics"]["real"]["support_count"], 30)

    def test_blank_subcategory_scope_trains_on_its_full_applicable_population(self):
        records = population(30, prefix="A", subcategory="Audio")
        records += population(30, prefix="V", subcategory="Video")
        records += population(30, prefix="GENERAL", subcategory="")
        self.upload(records)
        _, suggestions = self.profile()
        broad = self.exact_scope(suggestions, subcategory="")
        self.assertEqual(broad["counts"]["entities"], 90)
        self.assertEqual(self.exact_scope(suggestions)["counts"]["entities"], 30)

    def test_fingerprint_changes_when_an_approval_changes_without_touching_raw(self):
        self.upload(population())
        with application.connect() as c:
            before = fingerprint(c, application.get_settings(c))
            raw_before = c.execute("SELECT raw_hash FROM skus WHERE sku='P-0'").fetchone()[0]
        item = self.detail("P-0")["record"]
        response = self.client.post(f"/api/skus/{item['id']}/proposal", json={
            "weight_g": "1500", "height_cm": "50", "length_cm": "20", "width_cm": "20",
            "source_url": "https://example.com/measurement", "source_kind": "medicion_logistica",
            "evidence_note": "Medición del mismo producto y paquete de envío", "reason": "Nueva evidencia"})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(f"/api/proposals/{response.json()['id']}/decision", json={"decision": "approve"})
        self.assertEqual(response.status_code, 200, response.text)
        with application.connect() as c:
            self.assertNotEqual(before, fingerprint(c, application.get_settings(c)))
            self.assertEqual(raw_before, c.execute("SELECT raw_hash FROM skus WHERE sku='P-0'").fetchone()[0])


class CategoryProfileAPITests(IsolatedAPI, unittest.TestCase):
    def completed_profile(self):
        response = self.client.post("/api/category-profiles", json={"min_count": 30, "min_sellers": 3})
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/category-profiles/{run['id']}")
            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()
            if run["status"] in {"completed", "failed"}:
                application.TASKS.join()
                self.assertEqual(run["status"], "completed", run)
                self.assertEqual(run["progress"], run["total"])
                return run
            time.sleep(.01)
        self.fail("El perfil provisional no terminó")

    def apply(self, run, **changes):
        body = dict(all_ready=True, attestation=True)
        body.update(changes)
        return self.client.post(f"/api/category-profiles/{run['id']}/apply", json=body)

    def approve_one(self, sku="P-0"):
        record = self.detail(sku)["record"]
        response = self.client.post(f"/api/skus/{record['id']}/proposal", json={
            "weight_g": "1500", "height_cm": "50", "length_cm": "20", "width_cm": "20",
            "source_url": "https://example.com/package-measurement", "source_kind": "medicion_logistica",
            "evidence_note": "Medición respaldada del mismo producto y paquete", "reason": "Dimensiones verificadas"})
        self.assertEqual(response.status_code, 200, response.text)
        proposal_id = response.json()["id"]
        response = self.client.post(f"/api/proposals/{proposal_id}/decision", json={"decision": "approve"})
        self.assertEqual(response.status_code, 200, response.text)
        return proposal_id

    def test_background_profile_and_batch_apply_only_add_provisional_alerts(self):
        records = population()
        records.append(row("Seller 0", "OUTLIER", model="OUTLIER", weight_g="100000", height_cm="4000", length_cm="20", width_cm="20"))
        self.upload(records)
        proposal_id = self.approve_one()
        with application.connect() as c:
            originals = [tuple(r) for r in c.execute("SELECT id,raw_json,raw_hash FROM skus ORDER BY id")]
        old_size = self.detail("OUTLIER")["record"]["size"]
        export_before = self.csv_rows("/api/export.csv")
        run = self.completed_profile()
        self.assertGreater(run["summary"]["ready"], 0)
        suggestions = self.client.get(f"/api/category-profiles/{run['id']}/suggestions", params={"page_size": 1}).json()
        self.assertEqual(len(suggestions["items"]), 1)
        self.assertIn("bounds", suggestions["items"][0])
        csv_items = self.csv_rows(f"/api/category-profiles/{run['id']}/suggestions.csv")
        self.assertEqual(len(csv_items), suggestions["total"])
        response = self.apply(run, attestation=False)
        self.assertEqual(response.status_code, 422)
        response = self.apply(run)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(response.json()["applied"], 0)
        rules = self.client.get("/api/category-rules").json()["items"]
        self.assertTrue(rules)
        self.assertTrue(all(rule["origin"] == "catalog_statistics" for rule in rules))
        self.assertTrue(all("provisional" in rule["note"].lower() for rule in rules))
        with application.connect() as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT id,raw_json,raw_hash FROM skus ORDER BY id")], originals)
            self.assertEqual(c.execute("SELECT state FROM proposals WHERE id=?", (proposal_id,)).fetchone()[0], "approved")
        self.assertEqual(self.csv_rows("/api/export.csv"), export_before)
        outlier = self.detail("OUTLIER")["record"]
        self.assertEqual(outlier["size"], old_size)
        self.assertIn("category_real_above_max", outlier["reason_codes"])

    def test_manual_general_rule_is_neither_replaced_nor_displaced(self):
        self.upload(population())
        manual = dict(category="Electrónica", subcategory="", presentation="", real_min_kg=".1",
                      real_max_kg="100", note="Regla manual de negocio", enabled=True)
        response = self.client.post("/api/category-rules", json=manual)
        self.assertEqual(response.status_code, 200, response.text)
        rule_id = response.json()["id"]
        run = self.completed_profile()
        suggestions = self.client.get(f"/api/category-profiles/{run['id']}/suggestions").json()["items"]
        self.assertTrue(suggestions)
        self.assertTrue(all(item["state"] == "existing_manual" for item in suggestions))
        response = self.apply(run)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied"], 0)
        rules = self.client.get("/api/category-rules").json()["items"]
        self.assertEqual(len(rules), 1)
        self.assertEqual((rules[0]["id"], rules[0]["note"], rules[0]["origin"]), (rule_id, manual["note"], "manual"))

    def test_manually_edited_generated_rule_loses_automatic_ownership(self):
        self.upload(population())
        first = self.completed_profile()
        response = self.apply(first)
        self.assertEqual(response.status_code, 200, response.text)
        generated = self.client.get("/api/category-rules").json()["items"][0]
        second = self.completed_profile()
        self.assertGreater(second["summary"]["ready"], 0)
        edited = {**generated, "real_max_kg": "9", "note": "Ajuste manual revisado por operación"}
        response = self.client.put(f"/api/category-rules/{generated['id']}", json=edited)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.apply(second)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied"], 0)
        self.assertGreater(response.json()["protected_manual"], 0)
        protected = next(item for item in self.client.get("/api/category-rules").json()["items"] if item["id"] == generated["id"])
        self.assertEqual((protected["real_max_kg"], protected["note"], protected["origin"]), ("9", edited["note"], "manual"))

    def test_changed_source_snapshot_prevents_applying_old_statistics(self):
        self.upload(population())
        run = self.completed_profile()
        self.upload([{**population(1)[0], "weight_g": "9000"}])
        response = self.apply(run)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/api/category-rules").json()["items"], [])

    def test_changed_approval_snapshot_prevents_applying_old_statistics(self):
        self.upload(population())
        run = self.completed_profile()
        self.approve_one()
        response = self.apply(run)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/api/category-rules").json()["items"], [])
        self.assertEqual(self.detail("P-0")["proposal"]["state"], "approved")

    def test_changed_size_rule_version_prevents_applying_old_statistics(self):
        self.upload(population())
        run = self.completed_profile()
        response = self.client.put("/api/settings", json={"rule_mode": "upper_bounds"})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.apply(run)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/api/category-rules").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
