"""Prepara el repositorio local, genera el informe y ensaya su comando de entrada."""

import argparse
import getpass
from hashlib import sha256
import json
from math import isclose
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from informe import escribir_informe, leer_json, numero, porcentaje


HERRAMIENTAS = Path(__file__).resolve().parent
CONEXION_LOCAL = "postgresql://postgres:ruteo_local@127.0.0.1:5433/ruteo"
METRICAS = ("attr_antes", "attr_escenario", "attr_despues_esperado")
CONTEOS = ("nodos_clave", "pares_posibles", "pares_conectados_antes", "pares_conectados_escenario",
           "enlaces_total", "enlaces_con_probabilidad_positiva", "enlaces_fallados_escenario",
           "componentes_iniciales", "registros_catalogo", "registros_evaluados")
ARCHIVOS = (
    "main.py", "infra_bd.py", "modelo_sismo.py", "cargar_referencias.py", "completar_catalogo.py",
    "verificar_restauracion.py", "main.sql", "compose.yaml", "requirements.txt", ".gitignore",
    "sql/01_topologia.sql", "sql/02_nodos_attr.sql", "sql/03_probabilidades_falla.sql", "sql/04_nodos_finales.sql",
    "datos/PortalTransparencia_Yafun.zip", "datos/maule_2010.geojson", "datos/peeringdb_instalaciones.json",
    "datos/peeringdb_intercambios.json", "datos/peeringdb_relaciones.json", "datos/aterrizajes_chile.geojson",
    "datos/fuentes_catalogo.json", "datos/localizaciones_verificadas.json", "docs/criterios_nodos.md",
    "docs/modelo_y_attr.md", "resultados/resumen.json", "resultados/diagnostico_catalogo.json",
    "resultados/catalogo_nodos.csv", "resultados/catalogo_nodos.geojson", "resultados/nodos_clave.csv",
    "resultados/pares_nodos.csv", "resultados/afectacion_nodos.csv", "resultados/enlaces_probabilidades.csv",
    "resultados/extremos_probabilidad.csv", "resultados/analisis_resultados.md",
)


def git(repo, *args, capturar=False):
    return subprocess.run(["git", "-C", str(repo), *args], text=True, check=True,
                          stdout=subprocess.PIPE if capturar else None).stdout


def copia(origen, destino):
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origen, destino)


def prueba_correspondiente(fuente, ref):
    for ruta in sorted((fuente/"resultados").glob("ruteo_verificacion_*.json"),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True):
        doc = leer_json(ruta)
        rest = doc.get("restaurado", {})
        if (doc.get("verificada") is True and doc.get("tablas_public_antes") == 0
                and doc.get("radio_escenario_km") == ref["radio_escenario_km"]
                and all(rest.get(k) == ref[k] for k in ("nodos_clave", "pares_posibles", "enlaces_total",
                                                          "pares_conectados_antes", "pares_conectados_escenario"))
                and all(k in rest and isclose(rest[k],ref[k],rel_tol=0,abs_tol=1e-12) for k in METRICAS)):
            return ruta
    raise ValueError("Falta la evidencia JSON del paso 13 correspondiente a esta corrida en resultados/.")


def parametros(ref):
    if (ref["radio_maximo_km"], ref["radio_escenario_km"]) == (250, 125):
        return []
    return ["--radio-km", str(ref["radio_maximo_km"]), "--radio-escenario-km", str(ref["radio_escenario_km"])]


def agregar_arranque(ruta):
    texto = ruta.read_text(encoding="utf-8")
    marca = "import folium"
    if "from cierre_ruteo.entorno_ruteo import preparar_entorno" in texto:
        return
    if texto.count("\n"+marca+"\n") != 1:
        raise ValueError("El main.py de origen difiere de la versión integrada. Revisa el archivo de entrada.")
    inicio = ('if __name__ == "__main__":\n'
              '    from cierre_ruteo.entorno_ruteo import preparar_entorno\n'
              '    preparar_entorno(__file__)\n\n')
    ruta.write_text(texto.replace("\n"+marca+"\n", "\n"+inicio+marca+"\n",1), encoding="utf-8")


def readme(ref):
    comando = shlex.join(["python3","main.py","--abrir",*parametros(ref)])
    return f"""# Conectividad de la fibra chilena ante el sismo del Maule

Este repositorio evalúa {numero(ref['nodos_clave'])} accesos clave sobre {numero(ref['enlaces_total'])} segmentos del catastro Yafún. La tabla de probabilidades y el grafo completo se distribuyen en §main.sql§.

## Ejecutar desde un clon limpio

Se necesita Linux con Python 3.10 o posterior y su módulo §venv§, Docker en funcionamiento y acceso a Docker directo o mediante §sudo§. La primera ejecución puede descargar la imagen y las dependencias. El fondo cartográfico de OpenStreetMap y las bibliotecas web de la visualización necesitan conexión.

Desde la raíz del clon, ejecuta:

§§§bash
{comando}
§§§

El comando prepara el entorno Python local a partir de §requirements.txt§, crea o inicia PostgreSQL con la imagen declarada en §compose.yaml§, crea la BD si hace falta y restaura §main.sql§ al comenzar con una base vacía. Regenera la topología y el catálogo, aplica las probabilidades, calcula los tres ATTR y abre el mapa por HTTP local. La terminal muestra la dirección del mapa. Ctrl+C detiene el servidor. Si se omite §--abrir§, se generan los archivos y el programa termina.

La configuración local usa el contenedor §ruteo-db§, el puerto §5433§ y la base §ruteo§. §DATABASE_URL§ permite elegir otra base de la misma instancia. En una BD que ya contiene las fuentes, el programa las reutiliza. En cada corrida vuelve a exportar §main.sql§ y los archivos de §resultados/§ de esa copia del proyecto.

## Resultados de referencia

| Condición | ATTR | Pares con ruta |
| --- | ---: | ---: |
| Operación | {numero(ref['attr_antes'],9)} | {numero(ref['pares_conectados_antes'])} |
| Escenario de {numero(ref['radio_escenario_km'],2)} km | {numero(ref['attr_escenario'],9)} | {numero(ref['pares_conectados_escenario'])} |
| Posterior esperado | {numero(ref['attr_despues_esperado'],9)} | Esperanza sobre radios |

El denominador se mantiene en {numero(ref['pares_posibles'])} pares. El modelo, las fuentes, los supuestos y el contraste histórico están en el [informe](INFORME.md). El [informe imprimible](INFORME.html) se abre en un navegador y se puede guardar como PDF.

## Archivos y dependencias

- §main.py§ coordina el flujo. §infra_bd.py§ prepara PostgreSQL y exporta o restaura el SQL. PostGIS calcula distancias sobre el elipsoide y pgRouting genera la topología y los componentes.
- §completar_catalogo.py§ y §cargar_referencias.py§ reúnen las referencias de nodos. Las copias de las fuentes están en §datos/§.
- §modelo_sismo.py§ asigna las probabilidades e integra el ATTR bajo fallas correlacionadas. §sql/03_probabilidades_falla.sql§ conserva las actualizaciones por enlace.
- Psycopg conecta Python con PostgreSQL. Folium construye el mapa de Leaflet. Las versiones están en §requirements.txt§. El entorno y el servidor HTTP usan la biblioteca estándar de Python.
- §verificar_restauracion.py§ comprueba una restauración independiente. [Prueba del clon](docs/prueba_clon.md).
""".replace("§", chr(96))


def preparar_repo(fuente, foto):
    if not shutil.which("git"):
        raise ValueError("Falta Git en la VM. Puedes instalarlo con sudo apt install git.")
    faltantes = [r for r in ARCHIVOS if not (fuente/r).is_file()]
    if faltantes:
        raise FileNotFoundError("Faltan archivos en la carpeta de trabajo: "+", ".join(faltantes))
    with (fuente/"main.sql").open("rb") as f:
        if f.readline().rstrip() != b"-- EXPORTACION_AUTOCONTENIDA_RUTEO_V1":
            raise ValueError("El main.sql de origen debe ser la exportación completa verificada.")
    ref = leer_json(fuente/"resultados/resumen.json")
    prueba = prueba_correspondiente(fuente, ref)
    if foto:
        with foto.open("rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError("La captura indicada debe ser un archivo PNG.")
    repo = fuente.parent / "entrega-ruteo"
    try:
        repo.mkdir()
    except FileExistsError:
        repo = Path(tempfile.mkdtemp(prefix="entrega-ruteo-", dir=fuente.parent))
    print(f"Preparando el repositorio en {repo}", flush=True)
    for rel in ARCHIVOS:
        copia(fuente/rel, repo/rel)
    for rel in ("docs/diagnostico_previo.md", "datos/peeringdb_intercambios_complementarios.json"):
        if (fuente/rel).is_file():
            copia(fuente/rel, repo/rel)
    destino_prueba = repo/"resultados"/prueba.name
    copia(prueba, destino_prueba)
    for nombre in ("informe.py", "preparar_entrega.py", "entorno_ruteo.py"):
        copia(HERRAMIENTAS/nombre, repo/"cierre_ruteo"/nombre)
    agregar_arranque(repo/"main.py")
    if foto:
        copia(foto, repo/"docs/mapa_final.png")
    (repo/"README.md").write_text(readme(ref), encoding="utf-8")
    (repo/"docs/prueba_clon.md").write_text("# Prueba desde un clon limpio\n\nPendiente de ejecución.\n",encoding="utf-8")
    escribir_informe(repo, destino_prueba, "docs/mapa_final.png" if foto else None)
    git(repo,"init","-q","-b","main")
    # Los valores de respaldo solo afectan a este repositorio local.
    for campo, respaldo in (("user.name", getpass.getuser()), ("user.email", getpass.getuser()+"@localhost.invalid")):
        v = subprocess.run(["git","-C",str(repo),"config","--get",campo], capture_output=True, text=True)
        if not v.stdout.strip():
            git(repo,"config","--local",campo,respaldo)
    git(repo,"-c","core.excludesFile=/dev/null","add","-A")
    git(repo,"ls-files","--error-unmatch","--","main.py","main.sql","datos/maule_2010.geojson",
        "datos/PortalTransparencia_Yafun.zip","INFORME.md","INFORME.html",capturar=True)
    git(repo,"-c","commit.gpgSign=false","commit","-qm","Entrega de topología, modelo sísmico, ATTR y mapa Leaflet")
    return repo


def iniciar_y_leer_mapa(clon, comando, entorno, ruta_log):
    cliente = build_opener(ProxyHandler({}))
    with ruta_log.open("w",encoding="utf-8") as registro:
        proceso = subprocess.Popen(comando, cwd=clon, env=entorno, stdout=registro, stderr=subprocess.STDOUT)
        try:
            limite = time.monotonic()+1800
            url = None
            with ruta_log.open(encoding="utf-8", errors="replace") as salida:
                while not url:
                    for linea in salida:
                        print(linea,end="",flush=True)
                        if linea.startswith("Mapa disponible en: "):
                            url = linea.split(": ",1)[1].strip()
                    if url:
                        break
                    if proceso.poll() is not None:
                        raise RuntimeError(f"main.py terminó antes de servir el mapa. Revisa {ruta_log}")
                    if time.monotonic()>limite:
                        raise TimeoutError(f"Se agotó el tiempo de la prueba. Revisa {ruta_log}")
                    time.sleep(0.25)
            partes = urlsplit(url)
            if partes.scheme!="http" or partes.hostname!="127.0.0.1" or partes.path!="/mapa.html":
                raise ValueError("El programa anunció una dirección de mapa inesperada.")
            with cliente.open(url,timeout=45) as respuesta:
                mapa_servido = respuesta.read()
                if respuesta.status != 200 or b"L.map(" not in mapa_servido or b"L.geoJson(" not in mapa_servido:
                    raise ValueError("El servidor no entregó el mapa de Leaflet esperado.")
            mapa_clon = clon/"resultados/mapa.html"
            if sha256(mapa_servido).digest() != sha256(mapa_clon.read_bytes()).digest():
                raise ValueError("El HTML servido difiere del mapa recién generado.")
        finally:
            if proceso.poll() is None:
                proceso.send_signal(signal.SIGINT)
                try:
                    proceso.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proceso.terminate()
                    try:
                        proceso.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proceso.kill()
                        proceso.wait()
    if proceso.returncode != 0:
        raise RuntimeError(f"El servidor del clon terminó con código {proceso.returncode}.")
    return len(mapa_servido)


def comprobar_clon(repo):
    revision = git(repo,"rev-parse","HEAD",capturar=True).strip()
    nombre = "ruteo_clon_"+uuid4().hex[:12]
    dsn = os.environ.get("DATABASE_URL") or CONEXION_LOCAL
    if conninfo_to_dict(dsn).get("host","127.0.0.1") not in {"localhost","127.0.0.1"}:
        raise ValueError("La prueba está configurada para la instancia local de la VM.")
    with psycopg.connect(make_conninfo(dsn,dbname="postgres",connect_timeout=5)) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s",(nombre,)).fetchone():
            raise ValueError("El nombre de la base de prueba ya existe.")
    entorno = os.environ.copy()
    for clave in ("PYTHONPATH","PYTHONHOME","VIRTUAL_ENV"):
        entorno.pop(clave,None)
    entorno.update(DATABASE_URL=make_conninfo(dsn,dbname=nombre), PYTHONUNBUFFERED="1", BROWSER="/bin/true %s")
    log = repo/"docs/ejecucion_clon.log"
    print(f"Probando el commit {revision[:12]} con la base nueva {nombre}",flush=True)
    with tempfile.TemporaryDirectory(prefix="prueba-clon-",dir=repo.parent) as tmp:
        clon = Path(tmp)/"repo"
        subprocess.run(["git","clone","--quiet","--no-local","--",str(repo),str(clon)],check=True)
        if (git(clon,"rev-parse","HEAD",capturar=True).strip()!=revision or git(clon,"status","--porcelain",capturar=True)
                or (clon/".venv").exists() or (clon/"resultados/mapa.html").exists()):
            raise ValueError("La prueba requiere un clon limpio sin entorno ni mapa generado.")
        ref = leer_json(clon/"resultados/resumen.json")
        opciones = ["main.py","--abrir",*parametros(ref)]
        python = getattr(sys,"_base_executable",None) or sys.executable
        tamano = iniciar_y_leer_mapa(clon,[python,*opciones],entorno,log)
        salida = log.read_text(encoding="utf-8",errors="replace")
        if "Cargando infraestructura y fuentes desde main.sql" not in salida or "Topologia: OK" not in salida:
            raise ValueError("Falta evidencia de restauración o de pgr_createTopology en la ejecución del clon.")
        nuevos = leer_json(clon/"resultados/resumen.json")
        if Path(nuevos["main_sql"]).resolve()!=(clon/"main.sql").resolve() or nuevos["generado_en_utc"]==ref["generado_en_utc"]:
            raise ValueError("El resumen debe proceder de la ejecución actual del clon.")
        diferencias = [k for k in (*CONTEOS,"radio_maximo_km","radio_escenario_km","sismo") if nuevos[k]!=ref[k]]
        diferencias += [k for k in METRICAS if not isclose(nuevos[k],ref[k],rel_tol=0,abs_tol=1e-12)]
        for k in ("enlaces","vertices","componentes","sin_topologia","sinteticos","probabilidad_inicial_cero","pgr_createtopology"):
            if nuevos["topologia"][k] != ref["topologia"][k]:
                diferencias.append("topologia."+k)
        if diferencias:
            raise ValueError("Diferencias entre el clon y el informe: "+", ".join(diferencias)+". Consulta "+str(log))
    evidencia = {
        "commit_ejecutado":revision,"base_creada_por_main":nombre,"carga_desde_main_sql":True,
        "comando":shlex.join(["python3",*opciones]),"estado_http":200,"bytes_html_servidos":tamano,
        "tolerancia_absoluta_attr":1e-12,"resultado":{k:nuevos[k] for k in (*CONTEOS,*METRICAS)},
    }
    (repo/"docs/prueba_clon.json").write_text(json.dumps(evidencia,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    prueba = (f"# Prueba desde un clon limpio\n\nSe clonó el commit §{revision}§ en una carpeta vacía. "
              f"El comando §{evidencia['comando']}§ preparó su entorno Python, creó la base §{nombre}§, "
              "cargó §main.sql§ y obtuvo §OK§ de §pgr_createTopology§. "
              "El mapa respondió por HTTP con estado 200 y sus bytes coincidieron con el HTML recién generado.\n\n"
              "| ATTR | Referencia | Clon |\n| --- | ---: | ---: |\n")
    for k, etiqueta in zip(METRICAS,("Inicial","Del escenario","Posterior esperado")):
        prueba += f"| {etiqueta} | {numero(ref[k],9)} | {numero(nuevos[k],9)} |\n"
    prueba += "\n[Controles de la ejecución](prueba_clon.json)\n"
    (repo/"docs/prueba_clon.md").write_text(prueba.replace("§",chr(96)),encoding="utf-8")
    objetivo = ["docs/prueba_clon.json","docs/prueba_clon.md"]
    git(repo,"add","--",*objetivo)
    git(repo,"-c","commit.gpgSign=false","commit","-qm","Documenta la reproducción desde un clon limpio","--",*objetivo)
    print(f"\nCLON VERIFICADO • {revision[:12]} • HTTP 200")
    print("ATTR inicial "+numero(nuevos["attr_antes"],9)+" | escenario "+numero(nuevos["attr_escenario"],9)+
          " | posterior esperado "+numero(nuevos["attr_despues_esperado"],9))
    print(f"Repositorio local • {repo}\nInforme imprimible • {repo/'INFORME.html'}\nEvidencia • {repo/'docs/prueba_clon.md'}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--captura",type=Path,help="PNG de la corrida final de Leaflet")
    p.add_argument("--probar-clon",type=Path,metavar="REPO",help="Reintentar la prueba de un repositorio ya preparado")
    args = p.parse_args()
    repo = None
    try:
        if args.probar_clon:
            repo = args.probar_clon.expanduser().resolve()
        else:
            fuente = Path.cwd().resolve()
            foto = args.captura.expanduser().resolve() if args.captura else fuente/"resultados/mapa_final.png"
            if not foto.is_file():
                if args.captura:
                    raise FileNotFoundError(f"No se encontró la captura {foto}. Puedes omitir --captura para probar el clon mientras tanto.")
                foto = None
                print("El informe quedará pendiente de la captura del mapa.")
            repo = preparar_repo(fuente,foto)
        comprobar_clon(repo)
    except (OSError, ValueError, KeyError, RuntimeError, psycopg.Error, subprocess.SubprocessError) as e:
        if repo is not None:
            print(f"Repositorio conservado • {repo}",file=sys.stderr)
            print("Comando de reintento • .venv/bin/python cierre_ruteo/preparar_entrega.py --probar-clon "+shlex.quote(str(repo)),file=sys.stderr)
        raise SystemExit(f"No se completó el cierre. {e}")


if __name__=="__main__":
    main()
