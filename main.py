from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import io
import re

app = FastAPI(title="OmniLogistics OS - Smart Core", version="4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

VALORES_NULOS = {"none", "nan", "nat", "null", "n/a", "#n/a", "-", "--", ""}

def extractor_logico_estricto(df_raw: pd.DataFrame):
    fila_eje = -1
    palabras_ancla = ['semana', 'cinta', 'categoría', 'producto', 'fecha', 'código', 'cliente']
    for i in range(min(20, len(df_raw))):
        text_row = " ".join([str(x).lower() for x in df_raw.iloc[i] if pd.notna(x)])
        if any(w in text_row for w in palabras_ancla):
            fila_eje = i
            break

    if fila_eje == -1:
        raise ValueError("No es una matriz bananera estándar")

    fin_encabezados = fila_eje
    if fila_eje + 1 < len(df_raw):
        vals = [str(x).replace('.0','') for x in df_raw.iloc[fila_eje + 1] if pd.notna(x)]
        if sum(1 for x in vals if str(x).isdigit() and len(str(x)) == 4) >= 2:
            fin_encabezados = fila_eje + 1

    ecuador_datos = fin_encabezados + 1
    inicio_encabezados = max(0, fila_eje - 2) 
    
    arr_headers = df_raw.iloc[inicio_encabezados:fin_encabezados + 1].to_numpy(dtype=object)
    rows, cols = arr_headers.shape
    for r in range(rows):
        for c in range(cols):
            val_str = str(arr_headers[r, c]).strip() if arr_headers[r, c] is not None else ""
            if len(val_str) > 40:
                arr_headers[r, c] = np.nan

    df_headers = pd.DataFrame(arr_headers).ffill(axis=0).ffill(axis=1)

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
        nuevas_cols.append(" | ".join(jerarquia))

    df_datos = df_raw.iloc[ecuador_datos:].copy()
    
    cols_unicas, conteo = [], {}
    for col in nuevas_cols:
        base_col = col.strip()
        if base_col in conteo:
            conteo[base_col] += 1
            # Magia: Agrega espacios en blanco al final según el número de repetidos
            espacios = " " * conteo[base_col] 
            cols_unicas.append(f"{base_col}{espacios}")
        else:
            conteo[base_col] = 0
            cols_unicas.append(base_col)
        else:
            conteo[col] = 0
            cols_unicas.append(col)
            
    df_datos.columns = cols_unicas
    
    mask = pd.Series([True] * len(df_datos), index=df_datos.index)
    if not df_datos.empty and len(df_datos.columns) > 0:
        for col in df_datos.columns[:3]:
            if df_datos[col].dtype == 'object':
                filtro = df_datos[col].astype(str).str.lower().str.contains(r'total|promedio|acumulado|año\b|ano\b|sem\b|sem\d', regex=True, na=False)
                mask = mask & (~filtro)
                
    df_datos = df_datos[mask].reset_index(drop=True).dropna(how='all', axis=0)
    return df_datos

@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.csv', '.xlsm')):
        raise HTTPException(status_code=400, detail="Formato no soportado.")
    
    try:
        contents = await file.read()
        
        if file.filename.lower().endswith('.csv'):
            df_raw = pd.read_csv(io.BytesIO(contents), header=None, low_memory=False)
        else:
            try:
                df_raw = pd.read_excel(io.BytesIO(contents), header=None, engine='calamine')
            except Exception:
                df_raw = pd.read_excel(io.BytesIO(contents), header=None, engine='openpyxl')
        
        try:
            # Intenta procesar como matriz bananera
            df_limpio = extractor_logico_estricto(df_raw.copy())
        except Exception:
            # Si no es banano, aplica lectura Big Data Universal
            if file.filename.lower().endswith('.csv'):
                df_limpio = pd.read_csv(io.BytesIO(contents), low_memory=False)
            else:
                try:
                    df_limpio = pd.read_excel(io.BytesIO(contents), engine='calamine')
                except Exception:
                    df_limpio = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
            
            df_limpio.dropna(how='all', axis=0, inplace=True)
            df_limpio.dropna(how='all', axis=1, inplace=True)
            df_limpio.columns = [str(c).strip() if pd.notna(c) else f"Col_{i}" for i, c in enumerate(df_limpio.columns)]

        total_filas_reales = len(df_limpio)
        
        # EL SECRETO PARA NO CONGELAR EL NAVEGADOR:
        # Extrae solo 100 filas para enviarlas a la interfaz visual
        df_vista = df_limpio.head(100).copy()
        df_vista = df_vista.astype(object)
        columnas_reales = list(df_vista.columns)

        for col in columnas_reales:
            df_vista[col] = df_vista[col].apply(
                lambda x: None if pd.isna(x) or str(x).lower().strip() in VALORES_NULOS else x
            )

        records = []
        for _, row in df_vista.iterrows():
            row_dict = {}
            for col in columnas_reales:
                val = row[col]
                if pd.isna(val) or val is None:
                    row_dict[col] = "-"
                elif isinstance(val, (int, float, np.integer, np.floating)):
                    row_dict[col] = float(val) if isinstance(val, (float, np.floating)) else int(val)
                else:
                    row_dict[col] = str(val).strip()
            records.append(row_dict)

        return {
            "status": "success",
            "archivo": file.filename,
            "total_filas": total_filas_reales, 
            "columnas": columnas_reales,
            "datos": records
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "Motor Inteligente OmniLogistics OS en línea y operando."}
