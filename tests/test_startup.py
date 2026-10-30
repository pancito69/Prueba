"""Startup readiness and repeatable migration without touching user databases."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app as application
import iniciar
from engine import canonical_hash
from startup_lock import InstanceAlreadyRunning, exclusive_instance
from tests.test_categories import IsolatedAPI, row, v2_hash


class StartupMigrationTests(IsolatedAPI, unittest.TestCase):
    def test_migration_completes_once_and_second_start_preserves_approval(self):
        self.upload([row(weight_g="1200", height_cm="100", length_cm="30", width_cm="20")])
        current = self.detail()
        values = dict(weight_g="1200", height_cm="40", length_cm="30", width_cm="20")
        response = self.client.post(f"/api/skus/{current['record']['id']}/proposal", json={
            **values, "source_url": "https://example.com/shipping-measurement",
            "source_kind": "medicion_logistica", "evidence_note": "Medición del mismo producto y paquete de envío",
            "reason": "Dimensión verificada"})
        self.assertEqual(response.status_code, 200, response.text)
        proposal_id = response.json()["id"]
        response = self.client.post(f"/api/proposals/{proposal_id}/decision", json={"decision": "approve"})
        self.assertEqual(response.status_code, 200, response.text)
        legacy_raw = dict(current["original"])
        legacy_raw.pop("category", None)
        legacy_raw.pop("subcategory", None)
        with application.connect() as c:
            c.execute("UPDATE settings SET engine_revision=2 WHERE id=1")
            c.execute("UPDATE skus SET raw_json=?,raw_hash=? WHERE id=?", (
                json.dumps(legacy_raw), v2_hash(legacy_raw), current["record"]["id"]))
            c.execute("UPDATE proposals SET base_hash=?,candidate_hash=? WHERE id=?", (
                v2_hash(legacy_raw), v2_hash(values), proposal_id))
        with self.assertLogs("uvicorn.error", level="INFO") as progress, patch.object(application, "calculate_record", wraps=application.calculate_record) as calculations:
            application.init_db()
        self.assertGreater(calculations.call_count, 0)
        self.assertIn("Actualizando catálogo", "\n".join(progress.output))
        self.assertRegex("\n".join(progress.output), r"1\s*/\s*1")
        self.assertIn("base de datos lista", "\n".join(progress.output).lower())
        first = self.detail()
        self.assertEqual(first["original"], legacy_raw)
        self.assertEqual((first["proposal"]["state"], first["record"]["size"]), ("approved", "S"))
        self.assertTrue((application.DATA / "talla.pre-v3.sqlite3").exists())
        with application.connect() as c:
            self.assertEqual(application.get_settings(c)["engine_revision"], 3)
            self.assertEqual(c.execute("SELECT raw_hash FROM skus").fetchone()[0], canonical_hash(legacy_raw))
        exported = self.csv_rows("/api/export.csv")
        self.assertEqual(len(exported), 1)
        second_output = io.StringIO()
        with redirect_stdout(second_output), patch.object(application, "upgrade_engine", side_effect=AssertionError("No debe repetir la migración")), patch.object(application, "calculate_record", side_effect=AssertionError("No debe recalcular en el segundo inicio")):
            application.init_db()
        self.assertEqual(self.detail()["original"], legacy_raw)
        self.assertEqual(self.detail()["proposal"]["state"], "approved")
        self.assertEqual(self.csv_rows("/api/export.csv"), exported)

    def test_external_sqlite_writer_reports_actionable_error_without_losing_data(self):
        self.upload([row()])
        original = self.detail()["original"]
        with application.connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaisesRegex(RuntimeError, "base de datos está ocupada") as error:
                    application.init_db(busy_timeout_ms=20)
                message = str(error.exception).lower()
                self.assertIn(str(application.DB).lower(), message)
                self.assertIn("wal", message)
                self.assertTrue("cierra" in message or "cerrar" in message, message)
            finally:
                writer.rollback()
        application.init_db()
        self.assertEqual(self.detail()["original"], original)
        self.assertEqual(self.client.get("/api/summary").json()["total"], 1)


class InstanceLockTests(unittest.TestCase):
    def test_another_process_is_rejected_then_can_start_after_release(self):
        script = """import sys
from pathlib import Path
from startup_lock import exclusive_instance, InstanceAlreadyRunning
try:
    with exclusive_instance(Path(sys.argv[1])):
        print('acquired')
except InstanceAlreadyRunning:
    print('blocked')
    sys.exit(7)
"""
        with tempfile.TemporaryDirectory(prefix="talla-instance-lock-") as directory:
            command = [sys.executable, "-c", script, directory]
            with exclusive_instance(Path(directory)):
                contender = subprocess.run(command, cwd=application.ROOT, capture_output=True, text=True, timeout=5)
                self.assertEqual(contender.returncode, 7, contender.stderr)
                self.assertIn("blocked", contender.stdout)
            next_start = subprocess.run(command, cwd=application.ROOT, capture_output=True, text=True, timeout=5)
            self.assertEqual(next_start.returncode, 0, next_start.stderr)
            self.assertIn("acquired", next_start.stdout)

    def test_failed_application_start_releases_its_instance_lock(self):
        with tempfile.TemporaryDirectory(prefix="talla-failed-start-") as directory:
            data = Path(directory)
            with patch.object(application, "DATA", data), patch.object(application, "init_db", side_effect=RuntimeError("Inicio fallido de prueba")):
                with self.assertRaisesRegex(RuntimeError, "Inicio fallido de prueba"):
                    with TestClient(application.app):
                        self.fail("La aplicación no debe anunciar disponibilidad")
            with exclusive_instance(data):
                pass


class LauncherTests(unittest.TestCase):
    def test_ready_message_waits_for_healthy_response_from_this_child(self):
        output = io.StringIO()
        child = Mock()
        child.poll.return_value = None
        def child_exits(*args, **kwargs):
            child.poll.return_value = 0
            return 0
        child.wait.side_effect = child_exits
        connection = Mock()
        calls = 0
        with patch.object(iniciar, "port_in_use", return_value=False), patch.object(iniciar.subprocess, "Popen", return_value=child) as popen:
            def health_response():
                nonlocal calls
                self.assertNotIn("Abre", output.getvalue())
                calls += 1
                token = popen.call_args.kwargs["env"]["TALLA_STARTUP_ID"]
                response = Mock()
                response.status = 503 if calls == 1 else 200
                response.read.return_value = json.dumps({"status": "starting" if calls == 1 else "ok",
                                                        "startup_id": token}).encode()
                return response
            connection.getresponse.side_effect = health_response
            with patch.object(iniciar.http.client, "HTTPConnection", return_value=connection), patch.object(iniciar.time, "sleep"), redirect_stdout(output):
                iniciar.run_server(Path(sys.executable), application.ROOT)
        self.assertGreaterEqual(calls, 2)
        self.assertIn("Aplicación lista", output.getvalue())
        self.assertIn("Abre", output.getvalue())
        child.wait.assert_called_once()

    def test_child_failure_is_reported_without_announcing_browser_url(self):
        child = Mock()
        child.poll.return_value = 9
        output = io.StringIO()
        with patch.object(iniciar.http.client, "HTTPConnection") as http, redirect_stdout(output):
            with self.assertRaises(iniciar.ServerStartupError) as error:
                iniciar.wait_until_ready(child, "test-token", check_interval=0)
        self.assertIn("9", str(error.exception))
        self.assertNotIn("Abre", output.getvalue())
        http.assert_not_called()

    def test_occupied_port_is_not_mistaken_for_a_new_ready_server(self):
        with patch.object(iniciar, "port_in_use", return_value=True), patch.object(iniciar.subprocess, "Popen") as popen, patch.object(iniciar.http.client, "HTTPConnection") as http:
            with self.assertRaises(iniciar.ServerStartupError):
                iniciar.run_server(Path(sys.executable), application.ROOT)
        popen.assert_not_called()
        http.assert_not_called()

    def test_healthy_response_from_another_instance_is_not_readiness(self):
        child = Mock()
        child.poll.return_value = None
        connection = Mock()
        response = Mock(status=200)
        response.read.return_value = json.dumps({"status": "ok", "startup_id": "another-instance"}).encode()
        connection.getresponse.return_value = response
        output = io.StringIO()
        with patch.object(iniciar.http.client, "HTTPConnection", return_value=connection), redirect_stdout(output):
            with self.assertRaises(iniciar.ServerStartupError):
                iniciar.wait_until_ready(child, "expected-instance")
        self.assertNotIn("Abre", output.getvalue())

    def test_port_probe_detects_real_listener_and_release(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertTrue(iniciar.port_in_use("127.0.0.1", port))
        self.assertFalse(iniciar.port_in_use("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
