from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import re
import io

# 1. Inicializamos la aplicación FastAPI
app = FastAPI(title="OmniLogistics OS - Core API", version="1.0")

# Permitir que el Frontend (React) se comunique con este Backend sin bloqueos de seguridad
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En producción pondremos la URL de tu Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VALORES_NULOS = {"none", "nan", "nat", "null", "n/a", "#n/a", "-", "--", ""}

# --- NUESTRO MOTOR DE ACERO (INTACTO) ---
def extractor_logico_estricto(df_raw: pd.DataFrame, nombre_archivo: str):
    # 1. Búsqueda del Ecuador
    fila_eje = 0
    palabras_ancla = ['semana', 'cinta', 'categoría', 'producto', 'fecha', 'código', 'cliente']
    for i in range(min(20, len(df_raw))):
        text_row = " ".join([str(x).lower() for x in df_raw.iloc[i] if pd.notna(x)])
        if any(w in text_row for w in palabras_ancla):
            fila_eje = i
            break

    fin_encabezados = fila_eje
    if fila_eje + 1 < len(df_raw):
        vals = [str(x).replace('.0','') for x in df_raw.iloc[fila_eje + 1] if pd.notna(x)]
        if sum(1 for x in vals if x.isdigit() and len(x) == 4) >= 2:
            fin_encabezados = fila_eje + 1

    ecuador_datos = fin_encabezados + 1
    inicio_encabezados = max(0, fila_eje - 2) 
    
    # 2. Matriz NumPy para limpieza Vectorizada
    arr_headers = df_raw.iloc[inicio_encabezados:fin_encabezados + 1].to_numpy(dtype=object)
    rows, cols = arr_headers.shape
    for r in range(rows):
        for c in range(cols):
            val_str = str(arr_headers[r, c]).strip() if arr_headers[r, c] is not None else ""
            if len(val_str) > 40:
                arr_headers[r, c] = np.nan

    df_headers = pd.DataFrame(arr_headers).ffill(axis=0).ffill(axis=1)

    # 3. Construcción del Linaje (Separado por " | " para que el FrontEnd lo dibuje como quiera)
    nuevas_cols = []
    for col_idx in range(len(df_headers.columns)):
        jerarquia = []
        for f_idx in range(len(df_headers)):
            val = df_headers.iloc[f_idx, col_idx]
            if pd.notna(val) and str(val).strip() != "" and str(val).lower() != 'nan':
                texto = str(val).replace('.0', '').strip()
                texto = re.sub(r'\b20\d{2}(?:\s*-\s*20\d{2})+\b', '', texto).strip('- ')
                
                texto_format = " ".join(texto.split()).title() if not texto.isdigit() else " ".join(texto.split())
                if texto_format:
                    if not jerarquia or jerarquia[-1].lower() != texto_format.lower():
                        jerarquia.append(texto_format)
        
        if not jerarquia: jerarquia = [f"Columna_{col_idx}"]
        nombre_final = " | ".join(jerarquia) # Usamos Pipe para el JSON
        nuevas_cols.append(nombre_final)

    df_datos = df_raw.iloc[ecuador_datos:].copy()
    
    # Manejo de Duplicados
    cols_unicas, conteo = [], {}
    for col in nuevas_cols:
        if col in conteo:
            conteo[col] += 1
            cols_unicas.append(f"{col} | {conteo[col]}")
        else:
            conteo[col] = 0
            cols_unicas.append(col)
            
    df_datos.columns = cols_unicas
    
    # Exterminador
    mask = pd.Series([True] * len(df_datos), index=df_datos.index)
    if not df_datos.empty and len(df_datos.columns) > 0:
        for col in df_datos.columns[:3]:
            if df_datos[col].dtype == 'object':
                filtro = df_datos[col].astype(str).str.lower().str.contains(r'total|promedio|acumulado|año\b|ano\b|sem\b|sem\d', regex=True, na=False)
                mask = mask & (~filtro)
                
    df_datos = df_datos[mask].reset_index(drop=True).dropna(how='all', axis=0)

    # Autotipado Seguro (Manejo de NaN para JSON)
    for col in df_datos.columns:
        df_datos[col] = df_datos[col].map(lambda v: None if str(v).lower().strip() in VALORES_NULOS else v)
        serie_str = df_datos[col].dropna().astype(str).str.replace(r"[$\s%]", "", regex=True).str.replace(",", ".")
        num = pd.to_numeric(serie_str, errors="coerce")
        if num.notna().sum() / max(len(serie_str), 1) > 0.5: 
            # Reemplazamos NaN de numpy por None nativo de Python para que el JSON no falle
            df_datos[col] = num.replace({np.nan: None})

    return df_datos

# --- LA PUERTA DE ENTRADA (ENDPOINT) ---
@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    """
    Recibe un archivo Excel/CSV, lo procesa con el motor B2B y devuelve un JSON puro.
    """
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Formato no soportado. Sube un Excel o CSV.")
    
    try:
        # Leemos el archivo desde la memoria de internet
        contents = await file.read()
        
        if file.filename.endswith('.csv'):
            df_raw = pd.read_csv(io.BytesIO(contents), header=None)
        else:
            df_raw = pd.read_excel(io.BytesIO(contents), header=None)
            
        # Pasamos por el motor
        df_limpio = extractor_logico_estricto(df_raw, file.filename)
        
        # Convertimos la matriz de pandas a un diccionario (JSON) universal
        datos_json = df_limpio.to_dict(orient="records")
        columnas = df_limpio.columns.tolist()
        
        return {
            "status": "success",
            "archivo": file.filename,
            "total_filas": len(df_limpio),
            "columnas": columnas,
            "datos": datos_json
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando la matriz: {str(e)}")

# Ruta de prueba de salud
@app.get("/")
def health_check():
    return {"status": "Motor OmniLogistics OS en línea y operando."}
