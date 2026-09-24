"""Conector de facturación electrónica (OSE / PSE).

Hoy corre en modo simulado: acepta el comprobante y devuelve un CDR falso, con la
misma forma que devolverá el proveedor real. Cuando contrates el OSE se
implementa `_llamar_al_ose` y el resto del ERP no se entera del cambio.
"""
from __future__ import annotations

import hashlib
import uuid

from apps.integraciones.conectores.base import Conector, ErrorConector, Respuesta, registrar
from apps.integraciones.models import ServicioExterno


@registrar
class ConectorOSE(Conector):
    codigo_servicio = ServicioExterno.Codigo.OSE
    operaciones = ("emitir_comprobante", "emitir_guia", "consultar_estado", "dar_de_baja")

    def _ejecutar(self, operacion, carga):
        if self.servicio.modo_simulado:
            return self._simular(operacion, carga)
        return self._llamar_al_ose(operacion, carga)

    # -- Modo simulado ---------------------------------------------------

    def _simular(self, operacion, carga):
        if operacion in ("emitir_comprobante", "emitir_guia"):
            numero = carga.get("numero_completo", "")
            if not numero:
                raise ErrorConector("Falta el número del comprobante", recuperable=False)
            hash_falso = hashlib.sha256(numero.encode()).hexdigest()[:40]
            return Respuesta(
                exito=True,
                id_externo=f"sim-{uuid.uuid4().hex[:12]}",
                codigo_http=202,
                datos={
                    "estado": "aceptado",
                    "codigo_respuesta": "0",
                    "mensaje": f"{carga.get('tipo_nombre', 'La factura')} {numero} "
                    "ha sido aceptada (simulado)",
                    "hash": hash_falso,
                },
                mensaje="Aceptado en modo simulado",
            )
        if operacion == "consultar_estado":
            return Respuesta(
                exito=True, codigo_http=200, datos={"estado": "aceptado", "codigo_respuesta": "0"}
            )
        if operacion == "dar_de_baja":
            return Respuesta(
                exito=True,
                codigo_http=202,
                datos={"ticket": f"sim-baja-{uuid.uuid4().hex[:8]}"},
            )
        raise ErrorConector(f"Operación «{operacion}» sin simulación", recuperable=False)

    # -- Modo real -------------------------------------------------------

    def _llamar_al_ose(self, operacion, carga):
        """Aquí va la llamada al proveedor real (Nubefact, Efact, SUNAT directo).

        Debe devolver un `Respuesta` con la misma forma que `_simular`, y levantar
        `ErrorConector(recuperable=True)` ante timeouts o errores 5xx para que la
        cola reintente a los 1, 5 y 15 minutos.
        """
        raise ErrorConector(
            "El conector real del OSE todavía no está implementado. "
            "Deja `modo_simulado` activo o implementa este método.",
            recuperable=False,
        )
