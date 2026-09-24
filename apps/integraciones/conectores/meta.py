"""Conectores de Meta: WhatsApp Business y la API de conversiones de Ads.

Los dos hablan con la misma nube de Meta, pero hacen cosas distintas:

- `ConectorWhatsApp` manda avisos al cliente con plantillas aprobadas.
- `ConectorAds` devuelve la conversión con el **valor real de la venta**. Es el
  paso 17 del diagrama: cuando la venta vuelve a la plataforma, el algoritmo
  deja de optimizar por leads baratos y empieza a optimizar por clientes que
  sí compran.

Ambos corren simulados hasta que cargues credenciales y apagues `modo_simulado`.
"""
from __future__ import annotations

import hashlib
import uuid

from apps.integraciones.conectores.base import Conector, ErrorConector, Respuesta, registrar
from apps.integraciones.models import ServicioExterno


def _hash(valor):
    """Meta exige los datos personales en SHA-256, nunca en claro."""
    texto = str(valor or "").strip().lower()
    return hashlib.sha256(texto.encode()).hexdigest() if texto else ""


@registrar
class ConectorWhatsApp(Conector):
    codigo_servicio = ServicioExterno.Codigo.WHATSAPP
    operaciones = ("enviar_plantilla", "enviar_texto", "marcar_leido")

    def _ejecutar(self, operacion, carga):
        if self.servicio.modo_simulado:
            return self._simular(operacion, carga)
        return self._llamar_a_meta(operacion, carga)

    def _simular(self, operacion, carga):
        destino = carga.get("telefono", "")
        if not destino and operacion != "marcar_leido":
            raise ErrorConector("Falta el teléfono de destino", recuperable=False)
        return Respuesta(
            exito=True,
            id_externo=f"wamid.sim{uuid.uuid4().hex[:16]}",
            codigo_http=200,
            datos={"estado": "enviado", "a": destino, "operacion": operacion},
        )

    def _llamar_a_meta(self, operacion, carga):
        """POST a https://graph.facebook.com/v21.0/<phone_number_id>/messages.

        Necesita `credenciales = {"phone_number_id": ..., "token": ...}`.
        Ante un 5xx o un timeout, levanta `ErrorConector(recuperable=True)` para
        que la cola reintente a los 1, 5 y 15 minutos.
        """
        raise ErrorConector(
            "El conector real de WhatsApp todavía no está implementado. "
            "Deja `modo_simulado` activo o implementa este método.",
            recuperable=False,
        )


@registrar
class ConectorAds(Conector):
    codigo_servicio = ServicioExterno.Codigo.ADS
    operaciones = ("enviar_conversion", "consultar_costos")

    def _ejecutar(self, operacion, carga):
        if self.servicio.modo_simulado:
            return self._simular(operacion, carga)
        return self._llamar_a_meta(operacion, carga)

    def _simular(self, operacion, carga):
        if operacion == "enviar_conversion":
            if not carga.get("valor"):
                raise ErrorConector("La conversión necesita el valor de la venta", recuperable=False)
            return Respuesta(
                exito=True,
                id_externo=f"sim-conv-{uuid.uuid4().hex[:12]}",
                codigo_http=200,
                datos={"events_received": 1, "valor": carga["valor"]},
            )
        if operacion == "consultar_costos":
            return Respuesta(exito=True, codigo_http=200, datos={"campanias": []})
        raise ErrorConector(f"Operación «{operacion}» sin simulación", recuperable=False)

    def _llamar_a_meta(self, operacion, carga):
        """POST a https://graph.facebook.com/v21.0/<pixel_id>/events.

        Necesita `credenciales = {"pixel_id": ..., "token": ...}`. El cuerpo ya
        viene con los datos personales hasheados desde `construir_conversion`.
        """
        raise ErrorConector(
            "El conector real de Ads todavía no está implementado. "
            "Deja `modo_simulado` activo o implementa este método.",
            recuperable=False,
        )


@registrar
class ConectorAutomatizacion(Conector):
    """n8n, Make o Zapier: para lo que no tiene conector propio.

    Solo salida. La entrada llega por el receptor de webhooks, que no necesita
    conector porque no hay nada que traducir del lado del ERP.
    """

    codigo_servicio = ServicioExterno.Codigo.AUTOMATIZACION
    operaciones = ("emitir_evento",)

    def _ejecutar(self, operacion, carga):
        if self.servicio.modo_simulado:
            return Respuesta(
                exito=True,
                id_externo=f"sim-zap-{uuid.uuid4().hex[:10]}",
                codigo_http=200,
                datos={"recibido": True, "evento": carga.get("evento", "")},
            )
        raise ErrorConector(
            "Configura `url_base` con el webhook de tu automatización e implementa este método.",
            recuperable=False,
        )


def construir_conversion(lead, valor, moneda="PEN"):
    """Arma el evento de conversión con el valor real de la venta.

    Los datos personales van hasheados, como exige Meta. El `lead_id` de Meta,
    cuando existe, es lo que permite atribuir la venta al anuncio exacto.
    """
    return {
        "evento": "Purchase",
        "valor": str(valor),
        "moneda": moneda,
        "lead_id": lead.id_externo,
        "origen": lead.origen,
        "campania": lead.campania,
        "utm": lead.utm,
        "usuario": {
            "em": _hash(lead.email),
            "ph": _hash(lead.telefono),
            "fn": _hash(lead.nombre.split(" ")[0] if lead.nombre else ""),
        },
    }
