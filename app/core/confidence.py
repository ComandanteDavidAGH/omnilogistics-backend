"""Confidence Engine de Genesis Core v1.2.

Motor de Confianza Analítica:
Determina la validez global de la conclusión combinando:
  - Calidad de Datos (Limpieza)
  - Integridad del Modelo
  - Cobertura de Hojas y Registros Económicos
  - Bounding / Cap metodológico estricto
"""
from __future__ import annotations

from dataclasses import dataclass
from .quality import QualityReport

CAP_MEDIDAS_SIN_USAR = 70.0    # Medidas económicas secundarias sin cruce por viaje
CAP_COBERTURA_BAJA = 55.0      # Menos del 60% de los registros participó


@dataclass
class ConfidenceReport:
    analytical_confidence: float     # 0.0 - 100.0
    confidence_level: str            # ALTA, MEDIA, BAJA, BLOQUEADA
    applied_cap: float | None
    cap_reason: str | None
    breakdown: dict


class ConfidenceEngine:
    """Motor de cálculo de confianza analítica ponderada y acotada."""

    @staticmethod
    def calculate(
        quality: QualityReport,
        coverage: dict | None = None,
        merge_report: dict | None = None
    ) -> ConfidenceReport:
        if quality.is_blocked:
            return ConfidenceReport(
                analytical_confidence=0.0,
                confidence_level="BLOQUEADA",
                applied_cap=0.0,
                cap_reason="Análisis bloqueado por deficiencias crónicas en los datos.",
                breakdown={"quality": 0.0, "integrity": 0.0}
            )

        # Base de confianza ponderada: 60% Limpieza, 40% Integridad del Modelo
        base_confidence = (quality.data_quality_score * 0.6) + (quality.model_integrity_score * 0.4)
        cap = 100.0
        cap_reason = None

        # Evaluador de Cobertura y Topes
        if coverage:
            pend = coverage.get("hojas_con_medidas_no_incorporadas", [])
            if not pend and coverage.get("hojas_no_incorporadas"):
                pend = coverage.get("hojas_no_incorporadas")

            if pend:
                cap = min(cap, CAP_MEDIDAS_SIN_USAR)
                cap_reason = f"Existen hojas secundarias con medidas económicas no incorporadas al viaje ({', '.join(pend)})."

            econ_cov = coverage.get("cobertura_economica")
            if econ_cov is not None and econ_cov < 60.0:
                cap = min(cap, CAP_COBERTURA_BAJA)
                cap_reason = f"Cobertura económica baja ({econ_cov}% de los registros)."

        final_confidence = round(min(base_confidence, cap), 1)

        # Nivel de Confianza
        if final_confidence >= 80.0:
            level = "ALTA"
        elif final_confidence >= 60.0:
            level = "MEDIA"
        elif final_confidence >= 40.0:
            level = "BAJA"
        else:
            level = "BLOQUEADA"

        return ConfidenceReport(
            analytical_confidence=final_confidence,
            confidence_level=level,
            applied_cap=cap if cap < 100.0 else None,
            cap_reason=cap_reason,
            breakdown={
                "data_quality": quality.data_quality_score,
                "model_integrity": quality.model_integrity_score,
                "base_confidence": round(base_confidence, 1)
            }
        )
