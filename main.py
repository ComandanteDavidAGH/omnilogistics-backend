@app.post("/api/procesar-matriz")
async def procesar_archivo(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls', '.csv', '.xlsm')):
        raise HTTPException(status_code=400, detail="Formato no soportado. Sube un Excel (.xlsx, .xls) o CSV.")
    
    try:
        contents = await file.read()
        
        # 1. Intentar lectura raw
        if file.filename.endswith('.csv'):
            df_raw = pd.read_csv(io.BytesIO(contents), header=None)
        else:
            df_raw = pd.read_excel(io.BytesIO(contents), header=None)
            
        # 2. INTENTO A: Extractor Jerárquico Avanzado (Matriz Banano)
        try:
            df_limpio = extractor_logico_estricto(df_raw.copy())
            if df_limpio.empty or len(df_limpio.columns) == 0:
                raise ValueError("Matriz no compatible con el limpiador estricto.")
        except Exception:
            # 3. INTENTO B: Fallback a Lectura Universal Estándar
            if file.filename.endswith('.csv'):
                df_limpio = pd.read_csv(io.BytesIO(contents))
            else:
                df_limpio = pd.read_excel(io.BytesIO(contents))
            
            # Limpieza básica de columnas para archivos planos
            df_limpio = df_limpio.dropna(how='all', axis=0).dropna(how='all', axis=1)
            df_limpio.columns = [str(c).strip() for c in df_limpio.columns]

        # Conversión a JSON
        json_str = df_limpio.to_json(orient="records", date_format="iso")
        datos_json = json.loads(json_str)
        
        return {
            "status": "success",
            "archivo": file.filename,
            "total_filas": len(df_limpio),
            "columnas": df_limpio.columns.tolist(),
            "datos": datos_json
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al procesar la estructura del Excel: {str(e)}")
