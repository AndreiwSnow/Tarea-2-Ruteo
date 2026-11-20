"""Prepara, una vez por versión de requirements.txt, el entorno local de la entrega."""

import fcntl
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys


def preparar_entorno(programa):
    programa = Path(programa).resolve()
    requisitos = programa.parent / "requirements.txt"
    entorno = programa.parent / ".venv"
    entorno.mkdir(exist_ok=True)
    python = entorno / "bin/python"
    sello = entorno / ".requisitos-ruteo"
    huella = sha256(requisitos.read_bytes()).hexdigest()
    with (entorno / ".preparando-ruteo").open("a") as bloqueo:
        fcntl.flock(bloqueo, fcntl.LOCK_EX)
        if not python.exists() or not (entorno / "pyvenv.cfg").is_file():
            print("Creando el entorno Python de la entrega...", flush=True)
            subprocess.run([sys.executable, "-m", "venv", str(entorno)], check=True)
        if not sello.is_file() or sello.read_text(encoding="ascii") != huella:
            print("Instalando las dependencias declaradas en requirements.txt...", flush=True)
            subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                            "-r", str(requisitos)], check=True)
            sello.write_text(huella, encoding="ascii")
    if Path(sys.prefix).resolve() != entorno.resolve():
        os.execv(str(python), [str(python), str(programa), *sys.argv[1:]])
