"""Informe de la actividad a partir de la corrida y su restauración."""

import base64
import csv
import json
from collections import defaultdict
from html import escape
from pathlib import Path


def leer_json(ruta):
    return json.loads(ruta.read_text(encoding="utf-8"))


def filas(ruta):
    with ruta.open(encoding="utf-8-sig", newline="") as entrada:
        return list(csv.DictReader(entrada))


def numero(x, decimales=0):
    return f"{x:,.{decimales}f}".translate(str.maketrans(",.", ".,"))


def porcentaje(x):
    return numero(100 * x, 2) + " %"


def corto(nombres):
    lista = nombres.split(" | ")
    return lista[0] if len(lista) == 1 else f"{lista[0]} (+{len(lista) - 1} registros)"


def tabla(cabecera, cuerpo):
    return ("tabla", cabecera, [list(map(str, fila)) for fila in cuerpo])


def contenido(repo, prueba_sql, foto):
    datos = repo / "resultados"
    r = leer_json(datos / "resumen.json")
    t, s = r["topologia"], r["sismo"]
    cat = filas(datos / "catalogo_nodos.csv")
    nodos = filas(datos / "nodos_clave.csv")
    cambios = filas(datos / "afectacion_nodos.csv")
    extremos = filas(datos / "extremos_probabilidad.csv")
    inicial, medio, esperado = (r[k] for k in ("attr_antes", "attr_escenario", "attr_despues_esperado"))
    radio_max, radio = r["radio_maximo_km"], r["radio_escenario_km"]
    pares, antes, despues = (r[k] for k in ("pares_posibles", "pares_conectados_antes", "pares_conectados_escenario"))
    k = r["nodos_clave"]
    if len(nodos) != k or pares != k * (k - 1) // 2:
        raise ValueError("El inventario de accesos y el denominador del resumen difieren.")
    grupos = defaultdict(list)
    for n in nodos:
        grupos[int(n["componente_antes"])].append(int(n["acceso"]))
    if sum(len(g) * (len(g) - 1) // 2 for g in grupos.values()) != antes:
        raise ValueError("Los componentes de nodos_clave.csv no reproducen el ATTR inicial.")
    if len(cambios) != k or sum(int(c["perdida"]) for c in cambios) != 2 * (antes - despues):
        raise ValueError("Las pérdidas por acceso no coinciden con la pérdida de pares.")
    with (datos / "componentes_accesos.csv").open("w", encoding="utf-8", newline="") as salida:
        w = csv.writer(salida)
        w.writerow(("componente_inicial", "cantidad_accesos", "pares_conectados", "ids_accesos"))
        for componente, ids in sorted(grupos.items()):
            w.writerow((componente, len(ids), len(ids) * (len(ids)-1) // 2, " | ".join(map(str, sorted(ids)))))
    solos = sorted(int(c["acceso"]) for c in cambios if int(c["otros_accesos_antes"]) == 0)
    categorias = (
        ("instalacion_alojamiento_interconexion", "Alojamiento e interconexión", "PeeringDB · instalaciones", "Genera y recibe tráfico por los servicios alojados, como supuesto del modelo."),
        ("ixp_pit", "IXP o PIT", "PeeringDB · presencias de intercambio", "Intercambia tráfico entre redes. Una presencia física agrega el rol al acceso de su instalación."),
        ("aterrizaje_submarino", "Aterrizaje submarino", "TeleGeography · localidades", "Recibe e intercambia tráfico entre sistemas terrestres y submarinos."),
        ("ramificacion_troncal_modelada", "Ramificación troncal", "Grafo Yafún · al menos tres vecinos distintos", "Distribuye tráfico entre ramales. Su función se infiere de la geometría."),
    )
    muestra = []
    for clave, etiqueta, fuente, criterio in categorias:
        candidatas = [c for c in cat if c["evaluar"].lower() == "true" and clave in c["categorias"].split(" | ")]
        if candidatas:
            c = min(candidatas, key=lambda x: (float(x["distancia_al_vertice_m"]), x["clave"]))
            muestra.append((etiqueta, c["nombre"], f"{numero(float(c['latitud']), 4)}, {numero(float(c['longitud']), 4)}",
                            c["vertice_id"], c["roles"].replace(" | ", ", ")))
    cambios_ordenados = sorted(cambios, key=lambda c: (-int(c["perdida"]), int(c["acceso"])))
    afectados = [c for c in cambios_ordenados if int(c["perdida"]) > 0][:5]
    if len(cat) != r["registros_catalogo"] or len(muestra) != len(categorias):
        raise ValueError("El catálogo o sus categorías no corresponden al resumen.")
    excluidos = [x["nombre"] for x in r["referencias_fuera_alcance"]]
    variacion = lambda a: porcentaje((a - inicial) / inicial) if inicial else "Indefinida"
    bloques = [
        ("titulo", "Conectividad de la red de fibra observada ante el sismo del Maule"),
        ("p", f"En {numero(k)} accesos del catastro, la probabilidad media de conexión entre dos accesos pasa de {porcentaje(inicial)} en operación a {porcentaje(esperado)} bajo el modelo sísmico. La diferencia es de {numero((esperado-inicial)*100, 2)} puntos porcentuales."),
        ("seccion", "Infraestructura y selección de nodos"),
        ("p", f"El shapefile de la respuesta de transparencia T5610 contiene {numero(prueba_sql['restaurado']['trazados'])} trazados. La suma de sus segmentos de longitud positiva es de {numero(t['longitud_total_km'], 2)} km. Yafún informa cerca de 28.000 km de rutas recorridas para documentar el territorio. Las dos cifras miden cosas distintas y el grafo representa únicamente los trazados entregados."),
        tabla(("Categoría", "Fuente y criterio", "Rol asignado"), [(a,b,c) for _,a,b,c in categorias]),
        ("p", f"El catálogo conserva {numero(len(cat))} registros con sus roles y fuentes. Entran al cálculo {numero(r['registros_evaluados'])} registros que se agrupan en {numero(k)} accesos distintos. Se asocia cada referencia externa al extremo más próximo del segmento más cercano. La columna de distancia permite revisar la incertidumbre de cada asociación. Se supone que el acceso local funciona."),
        tabla(("Categoría", "Ejemplo del inventario", "Latitud, longitud (WGS84)", "Acceso", "Roles"), muestra),
        ("p", "Los aterrizajes se ubican a escala de localidad. Las ramificaciones cuentan vecinos distintos y su identificación como puntos de intercambio es una inferencia estructural. El localizador de ODATA complementa la posición del campus ST01. Tortel se excluye por una separación cercana a 94 km sin un tramo documentado."),
        ("p", "Registros fuera del conjunto evaluado en esta corrida: " + (", ".join(excluidos) if excluidos else "ninguno") + "."),
        ("links", [("Inventario con ubicación, rol, fuente y acceso de cada registro", "resultados/catalogo_nodos.csv"),
                   ("Criterios e incertidumbres de las fuentes", "docs/criterios_nodos.md")]),
        ("seccion", "Topología y conectividad en operación"),
        ("p", "La segmentación usa los vértices consecutivos de cada polilínea. Cada enlace se recorre en ambos sentidos con costo igual a su longitud en metros. En EPSG:4326, la tolerancia de 10⁻⁸ grados equivale aproximadamente a un milímetro en dirección norte-sur. Se usa para identificar extremos prácticamente coincidentes. Un cruce interior sin vértice compartido queda sin empalme."),
        ("codigo", "SELECT pgr_createTopology('public.enlaces', 0.00000001,\n    'geom', 'id', 'source', 'target', clean := true);"),
        tabla(("Control de la ejecución", "Resultado"), [
            ("pgr_createTopology", t["pgr_createtopology"]), ("Enlaces", numero(t["enlaces"])),
            ("Filas de enlaces_vertices_pgr", numero(t["vertices"])), ("Componentes no dirigidos", numero(t["componentes"])),
            ("Enlaces sin source o target", numero(t["sin_topologia"])),
            ("Enlaces con prob_falla = 0 al inicio (DEFAULT 0)", numero(t["probabilidad_inicial_cero"])),
            ("Enlaces marcados es_sintetico", numero(t["sinteticos"]))]),
        ("p", f"Hay {numero(len(grupos))} componentes con al menos un acceso clave. Dentro de cada componente, todos los accesos se alcanzan mutuamente. Antes del sismo hay {numero(pares-antes)} pares sin ruta. {numero(len(solos))} accesos no alcanzan a ningún otro acceso clave de la muestra. Pueden estar unidos a otros vértices del catastro."),
        ("p", "Accesos sin otro acceso clave alcanzable: " + (", ".join(map(str, solos)) if solos else "ninguno") + "."),
        ("links", [("Componente de cada acceso y pares por componente", "resultados/componentes_accesos.csv"),
                   ("Identidades y roles asociados a cada acceso", "resultados/nodos_clave.csv"),
                   ("SQL de topología", "sql/01_topologia.sql")]),
        ("seccion", "Evento y modelo probabilístico de falla"),
        ("p", "El GeoJSON de USGS identifica el terremoto del 27 de febrero de 2010. El programa lee la geometría y los atributos del archivo, actualiza la tabla sismos y consulta el registro utilizado para calcular las distancias."),
        tabla(("ID en public.sismos", "Magnitud", "Latitud", "Longitud", "Profundidad (km)"), [
            (s["id"], numero(s["magnitud"],1), numero(s["latitud"],3), numero(s["longitud"],3), numero(s["profundidad_km"],1))]),
        ("links", [("GeoJSON conservado", "datos/maule_2010.geojson"),
                   ("Consulta USGS del evento", "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventid=" + s["id"])]),
        ("p", "La figura 3 de Vass et al. ilustra fallas regionales correlacionadas mediante un disco. Su ejemplo usa un tamaño relativo uniforme S entre 0 y 1 y un radio de S × 50 km. Esta aplicación fija el centro en el epicentro del Maule y conserva un solo valor de S para todos los enlaces de cada realización."),
        ("formula", "R = S · Rmáx,  S ~ Uniforme(0, 1)\nP(falla de e) = P(R ≥ dₑ) = máx(0, 1 − dₑ/Rmáx)"),
        ("p", f"USGS describe una ruptura de cerca de 500 km de largo. La decisión de modelado predeterminada usa su semilongitud, 250 km, como escala de un disco isotrópico. En la corrida entregada Rmáx = {numero(radio_max, 2)} km y el escenario ilustrado usa R = {numero(radio, 2)} km. El radio del mapa fija una realización. La esperanza integra toda la distribución. La magnitud y la profundidad identifican el evento, y la escala de daño depende del supuesto geométrico."),
        ("p", "Para fibra se supone que basta una intersección del segmento con el disco para interrumpirlo. dₑ es la distancia mínima del epicentro al segmento completo. PostGIS la calcula en metros con ST_Distance sobre geography. Dividirla por Rmáx expresado también en metros da la probabilidad. El sismo comparte su radio entre todos los enlaces, de modo que las fallas cercanas pueden ocurrir juntas."),
        ("p", f"El modelo asignó probabilidad positiva a {numero(r['enlaces_con_probabilidad_positiva'])} de {numero(r['enlaces_total'])} enlaces. En el escenario ilustrado fallan {numero(r['enlaces_fallados_escenario'])}. Los extremos de la siguiente tabla provienen de la corrida y los empates conservan el orden reproducible del CSV."),
        tabla(("Grupo", "ID de enlace", "Distancia al epicentro (km)", "Probabilidad"), [
            ("Mayor" if e["grupo"]=="mas_afectados" else "Menor", e["enlace"], numero(float(e["distancia_km"]),3), numero(float(e["prob_falla"]),6)) for e in extremos]),
        ("links", [("Probabilidad, distancia y estado de cada enlace", "resultados/enlaces_probabilidades.csv"),
                   ("Implementación que actualiza la base", "modelo_sismo.py")]),
        ("seccion", "ATTR antes y después"),
        ("p", "Sea K el conjunto fijo de k accesos y sea i ↔ j el evento de que existe una ruta entre dos accesos diferentes. ATTR es el promedio de esas probabilidades sobre los pares no ordenados. Se usa la idea de confiabilidad media entre dos terminales de Neumayer y Modiano, restringida a los accesos clave."),
        ("formula", "N = k(k − 1)/2\nATTR(K) = (1/N) · ∑ᵢ<ⱼ P(i ↔ j)"),
        ("p", f"En una realización, un componente con h accesos aporta h(h−1)/2 pares. Se mantienen los {numero(pares)} pares en el denominador incluso cuando algún acceso queda aislado. Para la esperanza se leen las probabilidades de la BD y se ordenan los umbrales 1−pₑ. Entre umbrales consecutivos la conectividad permanece constante. Se suman el ancho de cada intervalo por sus pares conectados y se divide por N. La integración es exacta bajo el supuesto de radio común."),
        tabla(("Condición", "Pares con ruta", "ATTR", "Porcentaje", "Cambio (pp)", "Cambio relativo"), [
            ("Operación", numero(antes), numero(inicial,9), porcentaje(inicial), "0,00", "0,00 %"),
            (f"Escenario de {numero(radio,2)} km", numero(despues), numero(medio,9), porcentaje(medio), numero(100*(medio-inicial),2), variacion(medio)),
            ("Posterior esperado", "Promedio sobre radios", numero(esperado,9), porcentaje(esperado), numero(100*(esperado-inicial),2), variacion(esperado))]),
        ("p", f"En el escenario ilustrado pierden su ruta {numero(antes-despues)} pares que estaban conectados. La tabla identifica los accesos con mayor pérdida de alcanzabilidad en ese escenario. Cada pérdida cuenta cuántos otros accesos clave dejaron de ser alcanzables desde ese acceso."),
        tabla(("Acceso", "Una referencia asociada", "Alcanzables antes", "Alcanzables después", "Pérdida"), [
            (c["acceso"], corto(c["nombres"]), c["otros_accesos_antes"], c["otros_accesos_despues"], c["perdida"]) for c in afectados]),
        ("links", [("Afectación de todos los accesos", "resultados/afectacion_nodos.csv"),
                   ("Conectividad de cada par", "resultados/pares_nodos.csv")]),
        ("seccion", "Contraste con el 27F y alcance"),
        ("p", "Yafún documenta en su informe técnico final (§ 2.2, p. 10) que más del 60 % de la Internet chilena quedó desconectada durante unas seis horas tras el terremoto. El estudio relaciona esa interrupción con las interconexiones y los respaldos en Santiago. Durante la recuperación, el reporte publicado por SUBTEL el 3 de marzo de 2010 situó la operatividad de las redes fijas en 75 % en Maule y 45 % en Biobío."),
        ("p", "Las tres observaciones tienen horizontes y denominadores distintos. El ATTR describe rutas entre accesos de un grafo parcial. Los reportes históricos describen desconexión y operatividad de servicios en fechas concretas. La fragmentación inicial, la cartografía de otra época y los accesos aproximados limitan la comparación nacional. El disco simplifica la geometría de ruptura y carece de calibración empírica para cable chileno. La simulación omite energía, congestión, tsunami, fallas de equipos y recuperación temporal."),
        ("seccion", "Mapa y reproducción"),
        ("p", "Leaflet muestra los enlaces por probabilidad, los accesos clave, el epicentro y el radio ilustrado. La capa de fallas simuladas muestra el estado para ese radio. Las líneas discontinuas de asociación geográfica indican proximidad a un acceso y quedan fuera del grafo de ruteo."),
        ("foto", foto) if foto else ("p", "Pendiente de incorporar la captura de la corrida final de Leaflet."),
        ("p", f"El SQL de la entrega se restauró sobre una base creada desde template0, con {prueba_sql['tablas_public_antes']} tablas en public antes de la carga. Se recuperaron {numero(prueba_sql['restaurado']['probabilidades'])} probabilidades. Los tres ATTR se reprodujeron dentro de una tolerancia absoluta de 10⁻¹²."),
        ("links", [("Instrucciones de ejecución", "README.md"), ("Exportación completa", "main.sql"),
                   ("Resumen original", "resultados/resumen.json")]),
        ("seccion", "Fuentes"),
        ("links", [("Proyecto Yafún · Estudio y recomendaciones sobre la resiliencia de la infraestructura de la Internet chilena, informe técnico final, versión 2.0, diciembre de 2018", "https://yafun.nic.cl/estudio-resiliencia-internet-nacional/"),
                   ("Proyecto Yafún · recorrido documentado", "https://yafun.nic.cl/mapas/recorrido-yafun/"),
                   ("PeeringDB · instalaciones e intercambios", "https://www.peeringdb.com/"),
                   ("TeleGeography · Submarine Cable Map", "https://www.submarinecablemap.com/"),
                   ("ODATA · localizador de campus", "https://odatadc.com/es/localidades/"),
                   ("USGS · síntesis tectónica del terremoto del Maule", "https://earthquake.usgs.gov/earthquakes/eventpage/"+s["id"]+"/executive"),
                   ("Vass et al. (2021) · Probabilistic Shared Risk Link Groups Modeling Correlated Resource Failures Caused by Disasters, fig. 3", "https://doi.org/10.1109/JSAC.2021.3064652"),
                   ("Neumayer y Modiano (2016) · Network reliability under geographically correlated line and disk failure models, def. 9", "https://www.mit.edu/~modiano/papers/CV_J_83.pdf"),
                   ("SUBTEL · reporte de telecomunicaciones en las regiones afectadas, 3 de marzo de 2010", "https://www.subtel.gob.cl/reporte-telecomunicaciones-en-regiones-afectadas-emitido-por-el-ministerio-del-interior/")]),
    ]
    return bloques


def escribir_informe(repo, archivo_prueba, foto):
    bloques = contenido(repo, leer_json(archivo_prueba), foto)
    md, html = [], []
    for tipo, *args in bloques:
        x = args[0]
        if tipo in {"titulo", "seccion", "p"}:
            tag = {"titulo":"h1", "seccion":"h2", "p":"p"}[tipo]
            md.append({"titulo":"# ", "seccion":"## ", "p":""}[tipo] + x)
            html.append(f"<{tag}>{escape(x)}</{tag}>")
        elif tipo in {"formula", "codigo"}:
            md.append(("\x60"*3 + ("sql" if tipo=="codigo" else "text")) + "\n" + x + "\n" + "\x60"*3)
            html.append(f"<pre>{escape(x)}</pre>")
        elif tipo == "tabla":
            def linea(celdas):
                return "| " + " | ".join(escape(v).replace("|", r"\|").replace("\n", " ") for v in celdas) + " |"
            md.append("\n".join([linea(x), linea(["---"]*len(x)), *[linea(row) for row in args[1]]]))
            head = "".join(f"<th>{escape(v)}</th>" for v in x)
            body = "".join("<tr>"+"".join(f"<td>{escape(v)}</td>" for v in row)+"</tr>" for row in args[1])
            html.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        elif tipo == "links":
            md.append("\n".join(f"- [{lbl}]({url})" for lbl,url in x))
            html.append("<ul>"+"".join(f'<li><a href="{escape(url,quote=True)}">{escape(lbl)}</a></li>' for lbl,url in x)+"</ul>")
        elif tipo == "foto":
            alt = "Figura 1. Mapa Leaflet de la corrida final sobre la red de fibra."
            md.append(f"![{alt}]({x})\n\n{alt}")
            png = base64.b64encode((repo/x).read_bytes()).decode("ascii")
            html.append(f'<figure><img alt="{alt}" src="data:image/png;base64,{png}"><figcaption>{alt}</figcaption></figure>')
    enlace = archivo_prueba.relative_to(repo).as_posix()
    md.append(f"[Evidencia de la restauración]({enlace})")
    html.append(f'<p><a href="{escape(enlace,quote=True)}">Evidencia de la restauración</a></p>')
    (repo/"INFORME.md").write_text("\n\n".join(md)+"\n", encoding="utf-8")
    estilo = """@page{size:A4;margin:16mm}*{box-sizing:border-box}body{max-width:900px;margin:36px auto;padding:0 20px;color:#1a2c36;font:16px/1.55 'Source Sans 3',system-ui,sans-serif}h1,h2{color:#123e54;line-height:1.18}h1{font-size:2.15rem}h2{margin-top:2em;border-bottom:1px solid #9caeb5;padding-bottom:.25em}a{color:#075e71}table{border-collapse:collapse;width:100%;font-size:.9em}th,td{padding:.45em .6em;border-bottom:1px solid #cfdbdd;text-align:left;overflow-wrap:anywhere}th{background:#e9f0f1}pre{padding:.7em 1em;background:#f0f4f4;white-space:pre-wrap}figure{margin:1.2em 0}img{width:100%}figcaption{font-size:.88em}ul{padding-left:1.4em}@media print{body{margin:0;padding:0;max-width:none;font-size:10pt}h1{font-size:21pt}h2{break-after:avoid}tr,figure,pre{break-inside:avoid}a{color:inherit}thead{display:table-header-group}}"""
    (repo/"INFORME.html").write_text('<!doctype html><html lang="es"><meta charset="utf-8"><title>Conectividad de la red de fibra ante el 27F</title><style>'+estilo+'</style><body>'+"\n".join(html)+"</body></html>", encoding="utf-8")
