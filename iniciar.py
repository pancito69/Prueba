"""Create a local virtualenv, install pinned dependencies and start the pilot."""
from pathlib import Path
import os
import subprocess
import sys
import venv


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
    print("\nAbre http://127.0.0.1:8000 en tu navegador. Usa Ctrl+C para detener.\n", flush=True)
    subprocess.run([str(python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"], cwd=root, check=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"No se pudo completar el inicio (código {exc.returncode}). Revisa el mensaje anterior.")
