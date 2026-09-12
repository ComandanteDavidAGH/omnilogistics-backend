from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import io

app = FastAPI(title="OmniLogistics OS - Big Data Core", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.csv', '.xlsm')):
        raise HTTPException(status_code=400, detail="Formato no soportado.")
    
    try:
        contents = await file.read()
        
        # Lectura en memoria
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            try:
                df = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
            except Exception:
                df = pd.read_excel(io.BytesIO(contents))

        df.dropna(how='all', axis=0, inplace=True)
        df.dropna(how='all', axis=1, inplace=True)
        cols = [str(c).strip() for c in df.columns]
        df.columns = cols

        # Muestreo seguro: Se calculan filas totales pero se recortan 500 para el navegador
        total_filas_real = len(df)
        df_preview = df.head(500).copy()

        records = []
        for _, row in df_preview.iterrows():
            row_dict = {}
            for col in cols:
                val = row[col]
                if pd.isna(val):
                    row_dict[col] = "-"
                elif isinstance(val, (int, float, np.integer, np.floating)):
                    row_dict[col] = float(val) if isinstance(val, (float, np.floating)) else int(val)
                else:
                    row_dict[col] = str(val).strip()
            records.append(row_dict)

        return {
            "status": "success",
            "archivo": file.filename,
            "total_filas": total_filas_real,  # El KPI de total de registros reflejará los 150,000 reales
            "filas_mostradas": len(records),
            "columnas": cols,
            "datos": records
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en Big Data Engine: {str(e)}")

@app.get("/")
def health_check():
    return {"status": "Motor Big Data OmniLogistics OS activo."}
