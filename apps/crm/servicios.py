"""Del lead al cliente y al pedido.

«Llega el mismo cliente por dos canales»: antes de crear un tercero se busca por
documento y por correo. Si existe, se enriquece el registro; no se duplica.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import TipoDocumentoIdentidad
from apps.crm.models import EstadoLead, Lead
from apps.terceros.models import Tercero


def vendedores_de(empresa):
    """Quiénes entran en el reparto: los que tienen el permiso `crm.recibir_leads`.

    Sin esto, los leads le caerían también a contabilidad y a almacén. Si nadie
    tiene el permiso todavía, se reparte entre todos para no dejar leads huérfanos.
    """
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    from apps.core.models import Usuario

    permiso = Permission.objects.filter(
        codename="recibir_leads", content_type__app_label="crm"
    ).first()
    activos = Usuario.objects.filter(is_active=True, empresas=empresa)
    if permiso is None:
        return activos
    con_permiso = activos.filter(
        Q(user_permissions=permiso) | Q(groups__permissions=permiso)
    ).distinct()
    return con_permiso if con_permiso.exists() else activos


def asignar_vendedor(lead):
    """Regla de asignación: al vendedor con menos leads abiertos.

    Simple a propósito; cuando haya turnos o zonas se cambia solo esta función.
    """
    from django.db.models import Count, Q

    candidatos = (
        vendedores_de(lead.empresa)
        .annotate(
            abiertos=Count(
                "leads",
                filter=Q(leads__empresa=lead.empresa)
                & ~Q(leads__estado__in=(EstadoLead.GANADO, EstadoLead.PERDIDO)),
            )
        )
        .order_by("abiertos", "id")
    )
    vendedor = candidatos.first()
    if vendedor is not None:
        lead.vendedor = vendedor
        lead.save(update_fields=["vendedor", "actualizado_en"])
    return vendedor


def buscar_tercero_existente(empresa, numero_documento="", email=""):
    """Evita el duplicado: por documento primero, por correo después."""
    if numero_documento:
        tercero = Tercero.objects.filter(
            empresa=empresa, numero_documento=numero_documento
        ).first()
        if tercero:
            return tercero
    if email:
        return Tercero.objects.filter(empresa=empresa, email__iexact=email).first()
    return None


@transaction.atomic
def convertir_en_tercero(lead, numero_documento="", tipo_documento=None, razon_social=""):
    """Crea (o reutiliza) el tercero del lead y lo deja en «calificado»."""
    if lead.tercero:
        return lead.tercero

    numero = numero_documento or lead.numero_documento
    tercero = buscar_tercero_existente(lead.empresa, numero, lead.email)
    if tercero is None:
        if not numero:
            raise ValidationError("Para crear el cliente hace falta su DNI o RUC.")
        if tipo_documento is None:
            tipo_documento = (
                TipoDocumentoIdentidad.RUC if len(numero) == 11 else TipoDocumentoIdentidad.DNI
            )
        tercero = Tercero(
            empresa=lead.empresa,
            es_cliente=True,
            tipo_documento=tipo_documento,
            numero_documento=numero,
            razon_social=razon_social or lead.empresa_lead or lead.nombre,
            email=lead.email,
            telefono=lead.telefono,
            vendedor_asignado=lead.vendedor,
        )
        tercero.full_clean()
        tercero.save()
    else:
        # Enriquecer sin pisar lo que ya había.
        cambios = []
        if not tercero.email and lead.email:
            tercero.email = lead.email
            cambios.append("email")
        if not tercero.telefono and lead.telefono:
            tercero.telefono = lead.telefono
            cambios.append("telefono")
        if not tercero.es_cliente:
            tercero.es_cliente = True
            cambios.append("es_cliente")
        if cambios:
            tercero.save(update_fields=cambios + ["actualizado_en"])

    lead.tercero = tercero
    lead.numero_documento = tercero.numero_documento
    if lead.estado in (EstadoLead.NUEVO, EstadoLead.CONTACTADO):
        lead.estado = EstadoLead.CALIFICADO
    lead.save(update_fields=["tercero", "numero_documento", "estado", "actualizado_en"])
    return tercero


@transaction.atomic
def convertir_en_pedido(lead, almacen, usuario=None):
    """Arma el borrador de pedido heredando origen, campaña y UTM del lead."""
    from apps.ventas.servicios import crear_pedido

    if lead.pedido:
        return lead.pedido
    tercero = lead.tercero or convertir_en_tercero(lead)
    pedido = crear_pedido(
        lead.empresa,
        tercero,
        almacen,
        lineas=[],
        usuario=usuario or lead.vendedor,
        origen=lead.origen,
        campania=lead.campania,
        utm=lead.utm,
    )
    lead.pedido = pedido
    lead.estado = EstadoLead.PROPUESTA
    lead.save(update_fields=["pedido", "estado", "actualizado_en"])
    return pedido


def marcar_ganado(lead):
    lead.estado = EstadoLead.GANADO
    lead.save(update_fields=["estado", "actualizado_en"])


def marcar_perdido(lead, motivo=""):
    lead.estado = EstadoLead.PERDIDO
    lead.motivo_perdida = motivo[:120]
    lead.save(update_fields=["estado", "motivo_perdida", "actualizado_en"])


def enviar_conversion(lead, valor=None, moneda=None):
    """Devuelve la venta real a la plataforma de anuncios.

    Es el paso 17 del diagrama, «donde se gana la plata»: mientras la plataforma
    solo sepa que hubo un lead, optimiza por leads baratos. Cuando sabe cuánto
    facturó ese lead, empieza a buscar gente que compra.

    Se encola, como todo lo que sale: no se llama a Meta mientras el usuario
    espera. Devuelve el trabajo encolado, o `None` si no hay nada que enviar.
    """
    from apps.integraciones.conectores import obtener_conector
    from apps.integraciones.conectores.meta import construir_conversion
    from apps.integraciones.models import ServicioExterno

    if lead.conversion_enviada:
        return None
    if not lead.origen:
        # Un lead que no vino de una campaña no tiene a dónde volver.
        return None

    if valor is None:
        pedido = lead.pedido
        if pedido is None:
            return None
        valor, moneda = pedido.total, pedido.moneda
    if not valor:
        return None

    servicio = ServicioExterno.objects.filter(
        empresa=lead.empresa, codigo=ServicioExterno.Codigo.ADS, activo=True
    ).first()
    if servicio is None:
        return None

    conector = obtener_conector(servicio)
    return conector.encolar(
        "enviar_conversion",
        carga=construir_conversion(lead, valor, moneda or "PEN"),
        llave=f"conversion:{lead.empresa_id}:{lead.pk}",
        objeto_id=lead.pk,
        entidad="crm.Lead",
    )


def leads_de(documento):
    """Los leads que dieron origen a un pedido o comprobante, si los hubo."""
    pedido = getattr(documento, "pedido", documento)
    if pedido is None or not hasattr(pedido, "leads"):
        return Lead.objects.none()
    return pedido.leads.all()
