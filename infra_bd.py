"""Provision y exportacion de la BD Docker local de la actividad."""

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import NamedTemporaryFile
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


MARCA_SQL = "-- EXPORTACION_AUTOCONTENIDA_RUTEO_V1"
IMAGEN = "pgrouting/pgrouting:16-3.5-3.7"


class BaseDocker:
    def __init__(self, conexion_bd):
        self.conexion_bd = conexion_bd
        self.parametros = conninfo_to_dict(conexion_bd)
        if self.parametros.get("host", "127.0.0.1") not in {"127.0.0.1", "localhost"}:
            raise ValueError("El flujo automatico de esta entrega utiliza PostgreSQL Docker local.")
        self.nombre = os.environ.get("RUTEO_CONTENEDOR", "ruteo-db")
        self.volumen = os.environ.get("RUTEO_VOLUMEN", "ruteo_pgdata")
        self.usuario = self.parametros.get("user", "postgres")
        self.bd = self.parametros.get("dbname", "ruteo")
        self.puerto = int(self.parametros.get("port", "5433"))
        self.prefijo = None

    def ejecutar(self, argumentos, **opciones):
        return subprocess.run([*self.prefijo, *argumentos], **opciones)

    def preparar(self):
        if shutil.which("docker") is None:
            raise RuntimeError("Falta Docker. Instala Docker antes de ejecutar el proyecto.")
        self.prefijo = ["docker"]
        estado = self.ejecutar(["info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15)
        if estado.returncode:
            if shutil.which("sudo") is None:
                raise RuntimeError("Docker no esta disponible para este usuario.")
            self.prefijo = ["sudo", "docker"]
            estado = self.ejecutar(["info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15)
            if estado.returncode and shutil.which("systemctl"):
                subprocess.run(["sudo", "systemctl", "start", "docker"], check=True, timeout=30)
                estado = self.ejecutar(["info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15)
            if estado.returncode:
                raise RuntimeError("No fue posible acceder al motor Docker: " + estado.stderr.strip())

        inspeccion = self.ejecutar(
            ["inspect", self.nombre, "--format", "{{.Config.Image}}|{{.State.Running}}"],
            capture_output=True, text=True, timeout=15,
        )
        if inspeccion.returncode == 0:
            imagen, ejecutando = inspeccion.stdout.strip().split("|", 1)
            if imagen != IMAGEN:
                raise RuntimeError(f"El contenedor {self.nombre} utiliza otra imagen. Define RUTEO_CONTENEDOR y RUTEO_VOLUMEN para una instancia de esta entrega.")
            if ejecutando != "true":
                self.ejecutar(["start", self.nombre], check=True, timeout=30)
        elif "no such" in inspeccion.stderr.lower():
            clave = self.parametros.get("password") or os.environ.get("PGPASSWORD")
            if not clave:
                raise ValueError("La conexion debe indicar la clave local para inicializar PostgreSQL.")
            print("Creando el contenedor PostgreSQL/PostGIS/pgRouting...", flush=True)
            variables = {"POSTGRES_DB": self.bd, "POSTGRES_USER": self.usuario, "POSTGRES_PASSWORD": clave}
            if any("\n" in valor or "\r" in valor for valor in variables.values()):
                raise ValueError("Las credenciales locales no pueden contener saltos de linea.")
            # sudo normalmente descarta variables del entorno. Un archivo temporal
            # privado permite que Docker las reciba tambien con ese prefijo.
            with NamedTemporaryFile(mode="w", encoding="utf-8") as entorno:
                entorno.write("".join(f"{nombre}={valor}\n" for nombre, valor in variables.items()))
                entorno.flush()
                self.ejecutar([
                    "run", "-d", "--name", self.nombre, "--restart", "unless-stopped",
                    "--env-file", entorno.name,
                    "-p", f"127.0.0.1:{self.puerto}:5432",
                    "-v", f"{self.volumen}:/var/lib/postgresql/data", IMAGEN,
                ], check=True, timeout=180)
        else:
            raise RuntimeError("No se pudo inspeccionar el contenedor: " + inspeccion.stderr.strip())

        puertos = self.ejecutar(["port", self.nombre, "5432/tcp"], capture_output=True, text=True, check=True, timeout=15)
        if not any(linea.rpartition(":")[2] == str(self.puerto) for linea in puertos.stdout.splitlines()):
            raise RuntimeError(f"El contenedor no publica PostgreSQL en 127.0.0.1:{self.puerto}.")

        limite = time.monotonic() + 60
        ultimo_error = None
        admin = make_conninfo(self.conexion_bd, dbname="postgres", connect_timeout=2)
        while time.monotonic() < limite:
            try:
                with psycopg.connect(admin, autocommit=True) as conexion:
                    existe = conexion.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.bd,)).fetchone()
                    if existe is None:
                        conexion.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.bd)))
                print(f"Base disponible: {self.bd} en 127.0.0.1:{self.puerto}")
                return
            except psycopg.OperationalError as error:
                ultimo_error = error
                time.sleep(1)
        raise RuntimeError(f"PostgreSQL no quedo disponible: {ultimo_error}")

    def cargar_si_falta(self, ruta_sql):
        necesarias = ["fibra_original", "referencias_instalaciones", "referencias_intercambios", "referencias_presencias", "sismos"]
        with psycopg.connect(self.conexion_bd) as conexion:
            presentes = [conexion.execute("SELECT to_regclass(%s)", ("public." + t,)).fetchone()[0] is not None for t in necesarias]
        if all(presentes):
            print("Fuentes conservadas disponibles en la base.")
            return False
        if any(presentes):
            raise RuntimeError("La base contiene solo parte de las fuentes. Usa una BD de prueba vacia para restaurar main.sql.")
        ruta_sql = Path(ruta_sql)
        if not ruta_sql.is_file():
            raise FileNotFoundError("Falta main.sql. Debe incluirse la exportacion completa generada en la VM de origen.")
        with ruta_sql.open(encoding="utf-8") as archivo:
            if archivo.readline().strip() != MARCA_SQL:
                raise ValueError("main.sql todavia no es una exportacion completa. Generala ejecutando esta version sobre la BD de origen.")
        print("Cargando infraestructura y fuentes desde main.sql...", flush=True)
        with ruta_sql.open("rb") as archivo:
            self.ejecutar([
                "exec", "-i", self.nombre, "psql", "-X", "--quiet", "-U", self.usuario,
                "-d", self.bd, "-v", "ON_ERROR_STOP=1", "--single-transaction",
            ], stdin=archivo, check=True, timeout=180)
        return True

    def exportar(self, ruta_sql):
        ruta_sql = Path(ruta_sql)
        ruta_sql.parent.mkdir(parents=True, exist_ok=True)
        temporal = None
        try:
            with NamedTemporaryFile(mode="wb", dir=ruta_sql.parent, delete=False) as archivo:
                temporal = Path(archivo.name)
                archivo.write((MARCA_SQL + "\n-- Esquema, infraestructura, referencias, topologia y probabilidades.\n").encode())
                archivo.flush()
                self.ejecutar([
                    "exec", self.nombre, "pg_dump", "-U", self.usuario, "-d", self.bd,
                    "--format=plain", "--no-owner", "--no-privileges",
                ], stdout=archivo, check=True, timeout=180)
            if temporal.stat().st_size < 1000:
                raise RuntimeError("La exportacion SQL tiene un tamano inesperado.")
            temporal.replace(ruta_sql)
        finally:
            if temporal is not None:
                temporal.unlink(missing_ok=True)
        print(f"SQL autocontenido exportado: {ruta_sql}")
        return ruta_sql
