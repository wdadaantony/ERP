"""Leads que entran de afuera: Meta, WhatsApp, formularios y automatizaciones.

Cada plataforma manda una forma distinta. Aquí se traduce todo a un mismo
diccionario y se crea el lead una sola vez, sin duplicar a quien ya estaba.

Lo que llega es dato de un tercero, nunca una orden: se toma solo lo que se
entiende, se recorta a los límites del modelo y se ignora el resto.
"""
import logging
import re

from django.db import transaction

from apps.crm.models import Actividad, EstadoLead, Lead
from apps.crm.servicios import asignar_vendedor

logger = logging.getLogger(__name__)

#: Campos que aceptamos de una carga genérica, con sus alias más comunes.
ALIAS = {
    "nombre": ("nombre", "name", "full_name", "nombre_completo", "first_name"),
    "empresa_lead": ("empresa", "company", "empresa_lead", "organizacion"),
    "email": ("email", "correo", "e-mail", "mail", "correo_electronico"),
    "telefono": ("telefono", "phone", "phone_number", "celular", "whatsapp", "movil"),
    "numero_documento": ("documento", "dni", "ruc", "numero_documento", "tax_id"),
    "interes": ("interes", "mensaje", "message", "comentario", "consulta", "notas"),
    "valor_estimado": ("valor_estimado", "valor", "presupuesto", "budget", "amount"),
    "campania": ("campania", "campaign", "campaign_name", "utm_campaign"),
    "origen": ("origen", "source", "utm_source", "canal"),
}

CLAVES_UTM = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


def _primero(datos, claves):
    for clave in claves:
        valor = datos.get(clave)
        if valor not in (None, "", []):
            return valor
    return ""


def normalizar_telefono(valor):
    """Deja solo dígitos y antepone el país si viene un número peruano de 9 cifras."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    if len(digitos) == 9 and digitos.startswith("9"):
        return f"51{digitos}"
    return digitos


def _texto(valor, limite):
    return str(valor or "").strip()[:limite]


# -- Traductores por plataforma -------------------------------------------


def desde_meta(cuerpo):
    """Lead de un formulario de Meta (Instant Forms de Facebook o Instagram).

    Meta manda `entry[].changes[].value.field_data[]`, una lista de pares
    nombre/valores. El `leadgen_id` es la identidad del lead del otro lado.
    """
    salidas = []
    for entrada in cuerpo.get("entry", []) or []:
        for cambio in entrada.get("changes", []) or []:
            valor = cambio.get("value", {}) or {}
            campos = {}
            for campo in valor.get("field_data", []) or []:
                nombre = str(campo.get("name", "")).lower()
                valores = campo.get("values") or []
                campos[nombre] = valores[0] if valores else ""
            if not campos and not valor.get("leadgen_id"):
                continue
            salidas.append(
                {
                    "nombre": _texto(_primero(campos, ALIAS["nombre"]), 150) or "Lead de Meta",
                    "empresa_lead": _texto(_primero(campos, ALIAS["empresa_lead"]), 150),
                    "email": _texto(_primero(campos, ALIAS["email"]), 254),
                    "telefono": normalizar_telefono(_primero(campos, ALIAS["telefono"])),
                    "interes": _texto(_primero(campos, ALIAS["interes"]), 2000),
                    "origen": "meta_ads",
                    "campania": _texto(valor.get("campaign_name") or valor.get("ad_name"), 120),
                    "id_externo": _texto(valor.get("leadgen_id"), 120),
                    "utm": {
                        k: _texto(valor.get(k), 120)
                        for k in ("ad_id", "adset_id", "campaign_id", "form_id", "platform")
                        if valor.get(k)
                    },
                }
            )
    return salidas


def desde_whatsapp(cuerpo):
    """Mensaje entrante de la API de WhatsApp Business (Meta Cloud).

    Un mensaje de alguien desconocido es un lead; de alguien conocido, una
    actividad más en su ficha. Aquí solo se traduce; decidir cuál es de
    `registrar_entrantes`.
    """
    salidas = []
    for entrada in cuerpo.get("entry", []) or []:
        for cambio in entrada.get("changes", []) or []:
            valor = cambio.get("value", {}) or {}
            perfiles = {
                c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                for c in valor.get("contacts", []) or []
            }
            for mensaje in valor.get("messages", []) or []:
                if mensaje.get("type") != "text":
                    texto = f"[{mensaje.get('type', 'adjunto')}]"
                else:
                    texto = (mensaje.get("text") or {}).get("body", "")
                wa_id = mensaje.get("from", "")
                salidas.append(
                    {
                        "nombre": _texto(perfiles.get(wa_id) or f"WhatsApp {wa_id}", 150),
                        "telefono": normalizar_telefono(wa_id),
                        "interes": _texto(texto, 2000),
                        "origen": "whatsapp",
                        # El id del mensaje NO se guarda como `id_externo`: cambia
                        # con cada mensaje y no identifica a la persona. La
                        # identidad aquí es el teléfono; el id del mensaje ya lo
                        # usa el receptor para no procesar dos veces el mismo aviso.
                        "utm": {},
                        "es_mensaje": True,
                    }
                )
    return salidas


def desde_generico(cuerpo):
    """Carga de n8n, Make, Zapier o un formulario propio.

    Acepta un objeto suelto o una lista en `leads`. Se toman los campos
    conocidos por sus alias más habituales y se ignora lo demás.
    """
    crudos = cuerpo.get("leads") if isinstance(cuerpo.get("leads"), list) else [cuerpo]
    salidas = []
    for crudo in crudos:
        if not isinstance(crudo, dict):
            continue
        datos = {str(k).lower(): v for k, v in crudo.items()}
        nombre = _texto(_primero(datos, ALIAS["nombre"]), 150)
        email = _texto(_primero(datos, ALIAS["email"]), 254)
        telefono = normalizar_telefono(_primero(datos, ALIAS["telefono"]))
        if not (nombre or email or telefono):
            continue

        valor = _primero(datos, ALIAS["valor_estimado"])
        try:
            valor_estimado = round(float(str(valor).replace(",", "")), 2) if valor else 0
        except ValueError:
            valor_estimado = 0

        salidas.append(
            {
                "nombre": nombre or email or telefono,
                "empresa_lead": _texto(_primero(datos, ALIAS["empresa_lead"]), 150),
                "email": email,
                "telefono": telefono,
                "numero_documento": re.sub(
                    r"\D", "", str(_primero(datos, ALIAS["numero_documento"]))
                )[:15],
                "interes": _texto(_primero(datos, ALIAS["interes"]), 2000),
                "valor_estimado": valor_estimado,
                "origen": _texto(_primero(datos, ALIAS["origen"]) or "automatizacion", 60),
                "campania": _texto(_primero(datos, ALIAS["campania"]), 120),
                "id_externo": _texto(datos.get("id") or datos.get("id_externo"), 120),
                "utm": {k: _texto(datos[k], 120) for k in CLAVES_UTM if datos.get(k)},
            }
        )
    return salidas


# -- Alta con deduplicación ------------------------------------------------


def buscar_lead_existente(empresa, email="", telefono="", numero_documento="", id_externo=""):
    """Busca a la misma persona antes de crear otro lead.

    Resuelve «llega el mismo cliente por dos canales»: quien escribe por
    WhatsApp y además llena el formulario es una sola persona, no dos.
    """
    consulta = Lead.objects.filter(empresa=empresa)
    if id_externo:
        encontrado = consulta.filter(id_externo=id_externo).first()
        if encontrado:
            return encontrado
    if numero_documento:
        encontrado = consulta.filter(numero_documento=numero_documento).first()
        if encontrado:
            return encontrado
    if email:
        encontrado = consulta.filter(email__iexact=email).first()
        if encontrado:
            return encontrado
    if telefono:
        encontrado = consulta.filter(telefono=telefono).first()
        if encontrado:
            return encontrado
    return None


@transaction.atomic
def registrar_lead(empresa, datos, servicio=None):
    """Crea el lead, o enriquece el que ya existía. Devuelve `(lead, es_nuevo)`.

    Un lead ya cerrado que vuelve a escribir se reabre como «contactado»: el
    cliente volvió, y dejarlo en «perdido» lo esconde del vendedor.
    """
    es_mensaje = datos.pop("es_mensaje", False)
    interes = datos.pop("interes", "")

    lead = buscar_lead_existente(
        empresa,
        email=datos.get("email", ""),
        telefono=datos.get("telefono", ""),
        numero_documento=datos.get("numero_documento", ""),
        id_externo=datos.get("id_externo", ""),
    )

    if lead is None:
        lead = Lead.objects.create(
            empresa=empresa,
            interes=interes,
            **{k: v for k, v in datos.items() if v not in (None, "")},
        )
        asignar_vendedor(lead)
        _registrar_actividad(lead, interes, es_mensaje, servicio, entrante=True)
        return lead, True

    # Ya existía: se completa lo que faltaba, sin pisar lo que el vendedor puso.
    cambios = []
    for campo in ("email", "telefono", "numero_documento", "empresa_lead", "id_externo"):
        nuevo = datos.get(campo)
        if nuevo and not getattr(lead, campo):
            setattr(lead, campo, nuevo)
            cambios.append(campo)
    if datos.get("utm") and not lead.utm:
        lead.utm = datos["utm"]
        cambios.append("utm")
    if not lead.esta_abierto:
        lead.estado = EstadoLead.CONTACTADO
        lead.motivo_perdida = ""
        cambios += ["estado", "motivo_perdida"]
    if cambios:
        lead.save(update_fields=cambios + ["actualizado_en"])
    if not lead.vendedor:
        asignar_vendedor(lead)

    _registrar_actividad(lead, interes, es_mensaje, servicio, entrante=True)
    return lead, False


def _registrar_actividad(lead, texto, es_mensaje, servicio, entrante):
    """Deja constancia de por dónde llegó, para que el vendedor vea el hilo."""
    if not texto and not es_mensaje:
        return
    tipo = Actividad.Tipo.WHATSAPP if es_mensaje else Actividad.Tipo.NOTA
    origen = f" ({servicio.nombre})" if servicio else ""
    Actividad.objects.create(
        empresa=lead.empresa,
        lead=lead,
        tipo=tipo,
        detalle=f"{'Mensaje recibido' if entrante else 'Enviado'}{origen}: {texto}"[:2000],
        es_entrante=entrante,
    )


def registrar_entrantes(empresa, entradas, servicio=None):
    """Da de alta una tanda ya normalizada. Devuelve el resumen legible."""
    nuevos = repetidos = 0
    for datos in entradas:
        _, es_nuevo = registrar_lead(empresa, dict(datos), servicio)
        nuevos += es_nuevo
        repetidos += not es_nuevo
    partes = []
    if nuevos:
        partes.append(f"{nuevos} lead{'s' if nuevos != 1 else ''} nuevo{'s' if nuevos != 1 else ''}")
    if repetidos:
        partes.append(f"{repetidos} ya conocido{'s' if repetidos != 1 else ''}")
    return ", ".join(partes) or "nada que registrar"
