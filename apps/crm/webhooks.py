"""Manejadores de los webhooks que traen leads.

Se registran al importar el módulo (lo hace `apps.crm.apps.CrmConfig.ready`).
Cada plataforma tiene su traductor en `entrada.py`; aquí solo se decide cuál
usar y se guarda el resultado.
"""
import logging

from apps.crm.entrada import (
    desde_generico,
    desde_meta,
    desde_whatsapp,
    registrar_entrantes,
)
from apps.integraciones.models import ServicioExterno
from apps.integraciones.webhooks import EventoNoAplicable, webhook_de

logger = logging.getLogger(__name__)


@webhook_de(ServicioExterno.Codigo.ADS)
def _leads_de_meta(evento):
    """Formularios de Meta Ads: cada `leadgen` entrante es un lead."""
    entradas = desde_meta(evento.cuerpo or {})
    if not entradas:
        raise EventoNoAplicable(
            "El aviso de Meta no trae ningún formulario; no hay lead que registrar."
        )
    return registrar_entrantes(evento.empresa, entradas, evento.servicio)


@webhook_de(ServicioExterno.Codigo.WHATSAPP)
def _mensajes_de_whatsapp(evento):
    """Mensajes entrantes de WhatsApp Business.

    Meta manda también acuses de entrega y lectura; esos no son leads y se
    descartan sin error.
    """
    entradas = desde_whatsapp(evento.cuerpo or {})
    if not entradas:
        raise EventoNoAplicable("El aviso de WhatsApp no trae mensajes (será un acuse de estado).")
    return registrar_entrantes(evento.empresa, entradas, evento.servicio)


@webhook_de(ServicioExterno.Codigo.AUTOMATIZACION)
def _leads_de_automatizacion(evento):
    """n8n, Make, Zapier o un formulario propio, con la carga que sea."""
    entradas = desde_generico(evento.cuerpo or {})
    if not entradas:
        raise EventoNoAplicable(
            "La carga no trae nombre, correo ni teléfono: no hay con qué crear un lead."
        )
    return registrar_entrantes(evento.empresa, entradas, evento.servicio)


@webhook_de(ServicioExterno.Codigo.CRM)
def _leads_de_crm_externo(evento):
    """HubSpot, Salesforce y similares, normalizados como carga genérica."""
    cuerpo = evento.cuerpo or {}
    # HubSpot envuelve el contacto en `properties`; se aplana antes de traducir.
    if isinstance(cuerpo.get("properties"), dict):
        cuerpo = {**cuerpo, **cuerpo["properties"]}
    entradas = desde_generico(cuerpo)
    if not entradas:
        raise EventoNoAplicable("El aviso del CRM externo no trae datos de contacto.")
    for entrada in entradas:
        entrada.setdefault("origen", "crm_externo")
    return registrar_entrantes(evento.empresa, entradas, evento.servicio)
