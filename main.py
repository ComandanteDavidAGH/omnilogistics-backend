from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import io
import re

app = FastAPI(title="OmniLogistics OS - Big Data Core", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

VALORES_NULOS = {"none", "nan", "nat", "null", "n/a", "#n/a", "-", "--", ""}

@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.csv', '.xlsm')):
        raise HTTPException(status_code=400, detail="Formato no soportado.")
    
    try:
        contents = await file.read()
        
        # 1. MOTOR BIG DATA (Lectura en segundo plano ultrarrápida)
        if file.filename.lower().endswith('.csv'):
            df_limpio = pd.read_csv(io.BytesIO(contents), low_memory=False)
        else:
            try:
                # Calamine está escrito en Rust: procesa 150k filas en 1.5 segundos usando 1/10 de la memoria.
                df_limpio = pd.read_excel(io.BytesIO(contents), engine='calamine')
            except Exception:
                # Fallback de emergencia
                df_limpio = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
            
        # 2. LIMPIEZA UNIVERSAL SILENCIOSA
        df_limpio.dropna(how='all', axis=0, inplace=True)
        df_limpio.dropna(how='all', axis=1, inplace=True)
        
        # Aseguramos que las columnas sean texto
        columnas_reales = [str(c).strip() if pd.notna(c) else f"Col_{i}" for i, c in enumerate(df_limpio.columns)]
        df_limpio.columns = columnas_reales

        # 3. EL TRUCO STREAMLIT: Calculamos sobre el 100%, mostramos una fracción
        total_filas_reales = len(df_limpio)
        
        # Recortamos a las primeras 100 filas SOLO para la visualización en pantalla
        # (Así el navegador de tu cliente no se congela renderizando HTML infinito)
        df_vista = df_limpio.head(100).copy()
        df_vista = df_vista.astype(object)

        for col in df_vista.columns:
            df_vista[col] = df_vista[col].apply(
                lambda x: None if pd.isna(x) or str(x).lower().strip() in VALORES_NULOS else x
            )

        # 4. PARSEO SEGURO PARA EL FRONTEND
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
            "total_filas": total_filas_reales, # Esto enviará el "150,000" a los KPIs
            "columnas": columnas_reales,
            "datos": records # Esto enviará solo 100 filas a la tabla visual
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "Motor Big Data OmniLogistics OS en línea y operando."}
