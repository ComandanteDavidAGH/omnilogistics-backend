"""Convierte estructuras con numpy/pandas en JSON seguro (sin NaN, inf ni tipos raros)."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd


def clean(obj):
    if obj is None or obj is pd.NaT or obj is pd.NA:
        return None
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, (pd.Timestamp, dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [clean(v) for v in obj.tolist()]
    if isinstance(obj, pd.Series):
        return [clean(v) for v in obj.tolist()]
    return obj if isinstance(obj, str) else str(obj)
