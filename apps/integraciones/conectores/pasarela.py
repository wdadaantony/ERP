"""Conector de pasarela de pagos (Culqi, Niubiz, Izipay, Mercado Pago).

En modo simulado genera enlaces de pago falsos y reporta los cobros como
pendientes. Eso permite probar la tarea de rescate y el receptor de webhooks sin
tocar una pasarela real.
"""
from __future__ import annotations

import uuid

from apps.integraciones.conectores.base import Conector, ErrorConector, Respuesta, registrar
from apps.integraciones.models import ServicioExterno


@registrar
class ConectorPasarela(Conector):
    codigo_servicio = ServicioExterno.Codigo.PASARELA
    operaciones = ("crear_enlace_pago", "consultar_pago", "anular_pago")

    def _ejecutar(self, operacion, carga):
        if self.servicio.modo_simulado:
            return self._simular(operacion, carga)
        return self._llamar_a_la_pasarela(operacion, carga)

    # -- Modo simulado ---------------------------------------------------

    def _simular(self, operacion, carga):
        referencia = carga.get("referencia", "")
        if operacion == "crear_enlace_pago":
            if not referencia:
                raise ErrorConector("Falta la referencia del pedido", recuperable=False)
            id_externo = f"sim-pago-{uuid.uuid4().hex[:12]}"
            return Respuesta(
                exito=True,
                id_externo=id_externo,
                codigo_http=201,
                datos={
                    "id": id_externo,
                    "url": f"https://pagos.simulado.pe/{id_externo}",
                    "estado": "pendiente",
                    "monto": carga.get("monto"),
                },
            )
        if operacion == "consultar_pago":
            # La pasarela real diría «pagado» cuando corresponda; simulada, todo
            # sigue pendiente hasta que alguien lo cambie a propósito.
            return Respuesta(
                exito=True,
                codigo_http=200,
                datos={"referencia": referencia, "estado": "pendiente"},
            )
        if operacion == "anular_pago":
            return Respuesta(exito=True, codigo_http=200, datos={"estado": "anulado"})
        raise ErrorConector(f"Operación «{operacion}» sin simulación", recuperable=False)

    # -- Modo real -------------------------------------------------------

    def _llamar_a_la_pasarela(self, operacion, carga):
        """Aquí va la llamada real.

        Debe devolver un `Respuesta` con la misma forma que `_simular`, y levantar
        `ErrorConector(recuperable=True)` ante timeouts o 5xx.
        """
        raise ErrorConector(
            "El conector real de la pasarela todavía no está implementado. "
            "Deja `modo_simulado` activo o implementa este método.",
            recuperable=False,
        )
