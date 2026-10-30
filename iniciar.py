"""Create a local virtualenv, install pinned dependencies and start the pilot."""
from pathlib import Path
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import uuid
import venv


class ServerStartupError(RuntimeError):
    pass


def port_in_use(host="127.0.0.1", port=8000):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex((host, port)) == 0


def wait_until_ready(process, token, host="127.0.0.1", port=8000, *, timeout=900, check_interval=0.5, heartbeat_seconds=15):
    started = time.monotonic()
    reported = started
    while True:
        code = process.poll()
        if code is not None:
            raise ServerStartupError(f"La aplicación no terminó de iniciar (código {code}). Revisa el error de la consola; el navegador todavía no puede conectarse.")
        connection = http.client.HTTPConnection(host, port, timeout=1)
        try:
            # This is a loopback-only readiness check, never a remote API call.
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            payload = json.loads(response.read(65536))
            if response.status == 200 and payload.get("status") == "ok":
                if payload.get("startup_id") != token:
                    raise ServerStartupError("El puerto 8000 está ocupado por otra aplicación o instancia. Cierra la otra instancia y vuelve a iniciar una sola vez.")
                if process.poll() is None:
                    return
        except (OSError, http.client.HTTPException, ValueError):
            pass
        finally:
            connection.close()
        current = time.monotonic()
        if current - started >= timeout:
            raise ServerStartupError("El inicio superó el tiempo de espera. Revisa el último mensaje de la consola antes de reintentarlo; no borres los archivos de la base de datos.")
        if current - reported >= heartbeat_seconds:
            print("El catálogo sigue preparándose. Revisa el progreso en esta consola y no abras otra instancia.", flush=True)
            reported = current
        time.sleep(check_interval)


def run_server(python, root):
    if port_in_use():
        raise ServerStartupError("El puerto 8000 ya está en uso. Puede haber otra ventana de Talla abierta. Cierra esa instancia con Ctrl+C y espera su cierre antes de volver a iniciar.")
    token = uuid.uuid4().hex
    child_env = os.environ.copy()
    child_env["TALLA_STARTUP_ID"] = token
    print("\nPreparando el catálogo. La dirección del navegador aparecerá cuando la aplicación esté lista.\n", flush=True)
    process = subprocess.Popen([str(python), "-u", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"], cwd=root, env=child_env)
    try:
        wait_until_ready(process, token)
        print("\nAplicación lista. Abre http://127.0.0.1:8000 en tu navegador. Usa Ctrl+C para detener.\n", flush=True)
        code = process.wait()
        if code:
            raise ServerStartupError(f"La aplicación terminó con código {code}. Revisa el mensaje de la consola.")
    finally:
        # Only stop the child we created, never another Python/service process.
        if process.poll() is None:
            process.terminate()
            process.wait()


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Instala Python 3.12 o superior para ejecutar el piloto.")
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not environment.exists():
        print("Preparando el entorno de Python…", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    if not python.exists():
        raise SystemExit("La carpeta .venv existe pero no contiene Python. Revísala antes de continuar.")
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True, cwd=root)
    run_server(python, root)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"No se pudo completar el inicio (código {exc.returncode}). Revisa el mensaje anterior.")
    except ServerStartupError as exc:
        raise SystemExit(str(exc))
