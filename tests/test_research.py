"""Offline research exchange, field evidence, abstention and stale protection."""
from __future__ import annotations

import csv
import io
import unittest

from tests.test_categories import IsolatedAPI, row
import app as application


RESULT_FIELDS = ["case_id", "status", "field", "value", "unit", "measurement_type", "source_url",
                 "quote", "source_title", "identity_confirmed", "packaging_confirmed"]


class ResearchAPITests(IsolatedAPI, unittest.TestCase):
    def batch(self, **changes):
        body = dict(limit=20, direction="all", category="")
        body.update(changes)
        response = self.client.post("/api/research/batches", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def batch_detail(self, batch):
        response = self.client.get(f"/api/research/batches/{batch['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def only_case(self, batch):
        cases = self.batch_detail(batch)["cases"]
        self.assertEqual(len(cases), 1)
        return cases[0]

    def found(self, case_id, field="weight_g", value="1200", unit="g", **changes):
        result = dict(case_id=str(case_id), status="found", field=field, value=value, unit=unit,
                      measurement_type="shipping_package", source_url="https://example.com/manufacturer/package",
                      quote="Ficha del fabricante: valores del mismo producto, presentación y paquete de envío.",
                      source_title="Ficha de empaque del fabricante", identity_confirmed="true", packaging_confirmed="true")
        result.update(changes)
        return result

    def complete_evidence(self, case_id):
        return [self.found(case_id, "weight_g", "1.2", "kg"),
                self.found(case_id, "height_cm", "400", "mm"),
                self.found(case_id, "length_cm", ".3", "m"),
                self.found(case_id, "width_cm", "20", "cm")]

    def submit(self, batch, evidence, filename="resultados.csv"):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(evidence)
        response = self.client.post(f"/api/research/batches/{batch['id']}/results",
                                    files={"file": (filename, stream.getvalue().encode("utf-8"))})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_batches_deduplicate_products_and_keep_different_packaging_separate(self):
        self.upload([row(), row("Seller B", "SKU-2"), row("Seller C", "SKU-3", packaging="Bolsa de envío")])
        batch = self.batch()
        self.assertEqual((batch["case_count"], batch["member_count"]), (2, 3))
        cases = self.batch_detail(batch)["cases"]
        self.assertEqual(len(cases), 2)
        exported = self.csv_rows(f"/api/research/batches/{batch['id']}/cases.csv")
        self.assertEqual(len(exported), 2)
        response = self.client.get(f"/api/research/batches/{batch['id']}/instructions.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn("shipping_package", response.text)
        self.assertIn("source_url", response.text)

    def test_batch_limit_uses_unique_products_and_no_external_requests(self):
        self.upload([row(sku=f"SKU-{i}", model=f"Modelo-{i}") for i in range(25)])
        batch = self.batch(limit=20)
        self.assertEqual(batch["case_count"], 20)
        self.assertEqual(len(self.batch_detail(batch)["cases"]), 20)
        diagnostics = self.client.get("/api/diagnostics").json()
        self.assertEqual(diagnostics["ai"], {"enabled": False, "external_requests": 0})

    def test_four_shipping_fields_create_pending_proposals_for_compatible_skus(self):
        self.upload([row(), row("Seller B", "SKU-2")])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        result = self.submit(batch, self.complete_evidence(case_id))
        self.assertFalse(result.get("errors"), result)
        self.assertEqual(self.only_case(batch)["status"], "proposals_created")
        for sku in ("SKU-1", "SKU-2"):
            detail = self.detail(sku)
            self.assertEqual(detail["proposal"]["state"], "pending")
            self.assertEqual(detail["proposal"]["size"], "S")
            self.assertEqual(detail["proposal"]["weight_g"], "1200")
            self.assertEqual(detail["proposal"]["height_cm"], "40")
            self.assertEqual(detail["original"]["weight_g"], "10")
        self.assertEqual(self.csv_rows("/api/export.csv"), [])

    def test_weight_only_creates_reviewable_minimum_without_filling_dimensions(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        result = self.submit(batch, [self.found(case_id, value="50", unit="kg")])
        self.assertFalse(result.get("errors"), result)
        case = self.only_case(batch)
        self.assertEqual(case["status"], "minimum_proposed")
        self.assertEqual((case["minimum_weight_kg"], case["minimum_size"], case["direction"]), ("50", "XL", "up"))
        self.assertIsNone(self.detail()["proposal"])
        self.assertEqual(self.detail()["original"]["weight_g"], "10")
        self.assertEqual(self.csv_rows("/api/export.csv"), [])
        response = self.client.post(f"/api/research/cases/{case_id}/review", json={
            "decision": "accept", "note": "Verificada la evidencia del mínimo", "attestation": False})
        self.assertEqual(response.status_code, 422, response.text)
        response = self.client.post(f"/api/research/cases/{case_id}/review", json={
            "decision": "accept", "note": "Verificada la evidencia del mínimo", "attestation": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.only_case(batch)["status"], "minimum_reviewed")
        self.assertEqual(self.csv_rows("/api/export.csv"), [])

    def test_three_dimensions_produce_minimum_without_imputing_weight(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        values = [self.found(case_id, "height_cm", "100", "cm"),
                  self.found(case_id, "length_cm", "50", "cm"),
                  self.found(case_id, "width_cm", "40", "cm")]
        self.submit(batch, values)
        case = self.only_case(batch)
        self.assertEqual(case["status"], "minimum_proposed")
        self.assertEqual((case["minimum_weight_kg"], case["minimum_size"]), ("40", "L"))
        self.assertIsNone(self.detail()["proposal"])
        self.assertEqual(self.detail()["original"]["weight_g"], "10")
        self.assertEqual(self.csv_rows("/api/export.csv"), [])

    def test_identical_results_are_idempotent_and_resolved_case_not_overwritten(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        evidence = self.complete_evidence(case_id)
        self.submit(batch, evidence)
        proposal_id = self.detail()["proposal"]["id"]
        repeated = self.submit(batch, evidence)
        self.assertTrue(repeated["duplicate"], repeated)
        changed = self.complete_evidence(case_id)
        changed[0]["value"] = "2.5"
        refused = self.submit(batch, changed)
        self.assertTrue(refused.get("errors"), refused)
        self.assertEqual(self.detail()["proposal"]["id"], proposal_id)

    def test_stale_catalog_snapshot_cannot_generate_corrections(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        self.upload([row(height_cm="110")])
        result = self.submit(batch, self.complete_evidence(case_id))
        self.assertEqual(result["stale_cases"], 1)
        self.assertEqual(self.only_case(batch)["status"], "stale")
        self.assertIsNone(self.detail()["proposal"])

    def test_stale_rule_version_cannot_generate_corrections(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        response = self.client.put("/api/settings", json={"rule_mode": "upper_bounds"})
        self.assertEqual(response.status_code, 200, response.text)
        result = self.submit(batch, self.complete_evidence(case_id))
        self.assertEqual(result["stale_cases"], 1)
        self.assertEqual(self.only_case(batch)["status"], "stale")
        self.assertIsNone(self.detail()["proposal"])

    def test_invalid_sources_units_quotes_and_identity_never_create_proposals(self):
        self.upload([row(sku=f"SKU-{i}", model=f"M-{i}") for i in range(5)])
        batch = self.batch()
        cases = self.batch_detail(batch)["cases"]
        changes = [{"source_url": "javascript:alert(1)"}, {"quote": ""}, {"unit": "lb"},
                   {"identity_confirmed": "false"}, {"measurement_type": "bare_product"}]
        evidence = []
        for case, change in zip(cases, changes):
            fields = self.complete_evidence(case["id"])
            fields[0].update(change)
            evidence.extend(fields)
        result = self.submit(batch, evidence)
        self.assertTrue(result.get("errors"), result)
        with application.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)
        self.assertEqual(self.csv_rows("/api/export.csv"), [])

    def test_unknown_case_and_contradictory_duplicates_are_reported(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        evidence = self.complete_evidence(case_id)
        evidence.append(self.found(case_id, "height_cm", "50", "cm"))
        evidence.append(self.found(999999))
        result = self.submit(batch, evidence)
        self.assertTrue(result.get("errors"), result)
        self.assertEqual(self.only_case(batch)["status"], "conflict")
        self.assertIsNone(self.detail()["proposal"])

    def test_explicit_insufficient_evidence_abstains(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        abstention = {key: "" for key in RESULT_FIELDS}
        abstention.update(case_id=case_id, status="insufficient", quote="No se encontró ficha del empaque exacto.")
        self.submit(batch, [abstention])
        self.assertEqual(self.only_case(batch)["status"], "insufficient")
        self.assertIsNone(self.detail()["proposal"])

    def test_additional_fields_complete_insufficient_case_using_its_evidence_history(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        evidence = self.complete_evidence(case_id)
        self.submit(batch, [evidence[1]])
        self.assertEqual(self.only_case(batch)["status"], "insufficient")
        self.assertIsNone(self.detail()["proposal"])
        result = self.submit(batch, [evidence[0], evidence[2], evidence[3]])
        self.assertFalse(result["errors"], result)
        case = self.only_case(batch)
        self.assertEqual(case["status"], "proposals_created")
        self.assertEqual(len(case["evidence"]), 4)
        proposal = self.detail()["proposal"]
        self.assertEqual((proposal["height_cm"], proposal["size"], proposal["state"]), ("40", "S", "pending"))
        self.assertIn("height_cm", proposal["evidence_note"])
        self.assertEqual(self.csv_rows("/api/export.csv"), [])

    def test_later_results_cannot_silently_replace_a_historical_measurement(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        self.submit(batch, [self.found(case_id, value="1.2", unit="kg")])
        self.assertEqual(self.only_case(batch)["status"], "minimum_proposed")
        later = self.complete_evidence(case_id)
        later[0]["value"] = "2.5"
        result = self.submit(batch, later)
        self.assertTrue(result["errors"], result)
        case = self.only_case(batch)
        self.assertEqual(case["status"], "conflict")
        self.assertIsNone(case["minimum_size"])
        self.assertIsNone(case["minimum_weight_kg"])
        self.assertEqual(case["direction"], "unknown")
        self.assertEqual(len(case["evidence"]), 5)
        self.assertIsNone(self.detail()["proposal"])
        self.assertEqual(self.csv_rows("/api/export.csv"), [])

    def test_retry_preserves_parent_evidence_as_context_and_has_exact_csv_template(self):
        self.upload([row()])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        response = self.client.get(f"/api/research/batches/{batch['id']}/results-template.csv")
        self.assertEqual(response.status_code, 200, response.text)
        template = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(template, [RESULT_FIELDS])
        self.submit(batch, [self.found(case_id, value="1.2", unit="kg"), self.found(case_id, value="2.5", unit="kg")])
        self.assertEqual(self.only_case(batch)["status"], "conflict")
        response = self.client.post(f"/api/research/cases/{case_id}/retry", json={"note": "Buscar una fuente que resuelva la contradicción"})
        self.assertEqual(response.status_code, 200, response.text)
        retry = response.json()
        self.assertEqual(retry["parent_case_id"], case_id)
        retried = self.only_case(retry)
        self.assertEqual(retried["parent_case_id"], case_id)
        self.assertEqual(retried["status"], "open")
        self.assertEqual(retried["evidence"], [])
        self.assertEqual(len(retried["prior_evidence"]), 2)
        self.assertEqual(self.only_case(batch)["status"], "conflict")
        instructions = self.client.get(f"/api/research/batches/{retry['id']}/instructions.txt").text
        self.assertIn("CONTEXTO DE REINVESTIGACIÓN", instructions)
        new_id = retried["id"]
        self.submit(retry, [self.found(new_id, "height_cm", "40", "cm"),
                            self.found(new_id, "length_cm", "30", "cm"), self.found(new_id, "width_cm", "20", "cm")])
        self.assertEqual(self.only_case(retry)["status"], "minimum_proposed")
        self.assertIsNone(self.detail()["proposal"])

    def test_minimum_review_requires_its_specific_comparison_sku_to_remain_current(self):
        self.upload([row(), row("Seller B", "SKU-2")])
        batch = self.batch()
        case_id = self.only_case(batch)["id"]
        self.submit(batch, [self.found(case_id, value="50", unit="kg")])
        minimum = self.only_case(batch)
        representative_id = minimum["minimum_sku_id"]
        self.assertIsNotNone(representative_id)
        self.assertEqual(minimum["minimum_baseline_size"], "XS")
        members = [self.detail("SKU-1"), self.detail("SKU-2")]
        representative = next(member["record"] for member in members if member["record"]["id"] == representative_id)
        peer = next(member for member in members if member["record"]["id"] != representative_id)
        self.upload([row(representative["seller"], representative["sku"], height_cm="110")])
        self.assertEqual(self.detail(peer["record"]["sku"])["original"], peer["original"])
        response = self.client.post(f"/api/research/cases/{case_id}/review", json={
            "decision": "accept", "note": "Otro miembro sigue vigente pero la comparación original cambió", "attestation": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotEqual(self.only_case(batch)["status"], "minimum_reviewed")
        self.assertEqual(self.csv_rows("/api/export.csv"), [])

    def test_retry_after_proposals_preserves_active_decisions_and_rejected_candidates(self):
        for active_state in ("pending", "approved"):
            with self.subTest(active_state=active_state):
                first_sku, second_sku = active_state + "-1", active_state + "-2"
                self.upload([row(sku=first_sku, model=active_state), row("Seller B", second_sku, model=active_state)])
                batch = self.batch()
                case_id = self.only_case(batch)["id"]
                self.submit(batch, self.complete_evidence(case_id))
                ids = [self.detail(first_sku)["proposal"]["id"], self.detail(second_sku)["proposal"]["id"]]
                if active_state == "approved":
                    response = self.client.post(f"/api/proposals/{ids[0]}/decision", json={"decision": "approve"})
                    self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(self.only_case(batch)["can_retry"])
                response = self.client.post(f"/api/research/cases/{case_id}/retry", json={"note": "Debe respetar las decisiones vigentes"})
                self.assertEqual(response.status_code, 409, response.text)
                if active_state == "approved":
                    continue
                for index, proposal_id in enumerate(ids):
                    response = self.client.post(f"/api/proposals/{proposal_id}/decision", json={"decision": "reject", "note": "Fuente no aceptada para esta corrección"})
                    self.assertEqual(response.status_code, 200, response.text)
                    if index == 0:
                        self.assertFalse(self.only_case(batch)["can_retry"])
                self.assertTrue(self.only_case(batch)["can_retry"])
                response = self.client.post(f"/api/research/cases/{case_id}/retry", json={"note": "Investigar otras fuentes después del rechazo"})
                self.assertEqual(response.status_code, 200, response.text)
                retry = response.json()
                new_id = self.only_case(retry)["id"]
                result = self.submit(retry, self.complete_evidence(new_id))
                self.assertEqual(result["proposals_created"], 0)
                for sku, previous_id in zip((first_sku, second_sku), ids):
                    proposal = self.detail(sku)["proposal"]
                    self.assertEqual((proposal["id"], proposal["state"]), (previous_id, "rejected"))

    def test_cases_csv_escapes_spreadsheet_formulas(self):
        self.upload([row(name="=1+1")])
        batch = self.batch()
        rows = self.csv_rows(f"/api/research/batches/{batch['id']}/cases.csv")
        self.assertTrue(any("'=1+1" == cell for record in rows for cell in record.values()))


if __name__ == "__main__":
    unittest.main()
