import pandas as pd
import time
import urllib.error

def descargar_todos_los_sismos():
    todas_las_filas = []
    pagina = 1
    max_reintentos = 5

    while True:
        url = f"https://evtdb.csn.uchile.cl/?page={pagina}"

        exito = False
        for intento in range(max_reintentos):
            try:
                tablas = pd.read_html(url)
                exito = True
                break
            except urllib.error.HTTPError as e:
                if e.code == 503:
                    espera = 5 * (intento + 1)
                    print(f"503 en página {pagina}, esperando {espera}s (intento {intento+1}/{max_reintentos})...")
                    time.sleep(espera)
                elif e.code == 500:
                    print(f"500 en página {pagina} — probablemente fin de datos, terminando.")
                    exito = False
                    break
                else:
                    raise
            except Exception as e:
                print(f"Fin de datos en página {pagina}: {e}")
                exito = False
                break

        if not exito:
            break

        print(f"Página {pagina} OK")

        tabla_eventos = None
        for t in tablas:
            if any("Magnitud" in str(col) for col in t.columns):
                tabla_eventos = t
                break

        if tabla_eventos is None or tabla_eventos.empty:
            print(f"No hay más datos en página {pagina}, terminando.")
            break

        todas_las_filas.append(tabla_eventos)
        pagina += 1

        time.sleep(1)

        if pagina > 230:
            break

    resultado = pd.concat(todas_las_filas, ignore_index=True)
    resultado.to_csv("../data/sismos/sismos_csn.csv", index=False)
    print(f"\nTotal descargado: {len(resultado)} eventos")
    print(resultado.columns.tolist())
    return resultado

if __name__ == "__main__":
    descargar_todos_los_sismos()