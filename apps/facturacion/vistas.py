"""Comprobantes: listado, ficha con la respuesta de SUNAT, notas de crédito."""
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from apps.core.transacciones import vista_serializada
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.alcance import ve_todo
from apps.facturacion.models import Comprobante, EstadoComprobante, TipoComprobante, LineaComprobante
from apps.facturacion.servicios import (
    MOTIVOS_NOTA_CREDITO, MOTIVOS_NOTA_DEBITO, emitir_nota_credito,
    emitir_nota_debito, emitir_correccion_descripcion, encolar_envio,
)
from apps.tesoreria.vistas import FormularioExtorno


class FormularioNota(forms.Form):
    tipo_nota = forms.ChoiceField(label="Operación", choices=(("credito", "Nota de crédito monetaria"),
        ("debito", "Nota de débito"), ("descripcion", "Corregir descripción (NC 03)")))
    motivo_credito = forms.ChoiceField(label="Motivo de crédito",
        choices=[x for x in MOTIVOS_NOTA_CREDITO.items() if x[0] != "03"], initial="06", required=False)
    motivo_debito = forms.ChoiceField(label="Motivo de débito",
        choices=list(MOTIVOS_NOTA_DEBITO.items()), required=False)
    monto = forms.DecimalField(
        label="Monto", required=False, min_value=Decimal("0.01"), decimal_places=2,
        help_text="En crédito vacío acredita el saldo vivo; en débito es obligatorio.",
    )
    linea = forms.ModelChoiceField(label="Línea que se corrige", queryset=LineaComprobante.objects.none(), required=False)
    descripcion = forms.CharField(label="Descripción correcta", max_length=255, required=False)

    def __init__(self, *args, comprobante=None, **kwargs):
        super().__init__(*args, **kwargs)
        if comprobante:
            self.fields["linea"].queryset = comprobante.lineas.all()

    def clean(self):
        datos = super().clean()
        if datos.get("tipo_nota") == "debito" and not datos.get("monto"):
            self.add_error("monto", "La nota de débito requiere un importe.")
        if datos.get("tipo_nota") == "descripcion":
            if not datos.get("linea"):
                self.add_error("linea", "Selecciona la línea que se corrige.")
            if len((datos.get("descripcion") or "").strip()) < 3:
                self.add_error("descripcion", "Indica la descripción correcta.")
        return datos


def _mis_comprobantes(request):
    qs = Comprobante.objects.filter(empresa=request.empresa).select_related("tercero", "pedido")
    if not ve_todo(request.user, "ventas"):
        qs = qs.filter(pedido__vendedor=request.user)
    return qs


@login_required
@vista_serializada
def lista(request):
    comprobantes = _mis_comprobantes(request)
    estado = request.GET.get("estado", "")
    tipo = request.GET.get("tipo", "")
    q = request.GET.get("q", "").strip()
    if estado:
        comprobantes = comprobantes.filter(estado=estado)
    if tipo:
        comprobantes = comprobantes.filter(tipo=tipo)
    if q:
        comprobantes = comprobantes.filter(
            Q(serie__icontains=q) | Q(tercero__razon_social__icontains=q)
            | Q(tercero__numero_documento__icontains=q) | Q(pedido__numero__icontains=q)
        )
    pagina = Paginator(comprobantes, 40).get_page(request.GET.get("pagina"))
    return render(request, "facturacion/lista.html", {
        "page_obj": pagina, "estado": estado, "tipo": tipo, "q": q,
        "consulta": f"estado={estado}&tipo={tipo}&q={q}",
        "estados": EstadoComprobante.choices, "tipos": TipoComprobante.choices,
    })


@login_required
@vista_serializada
def detalle(request, pk):
    c = get_object_or_404(_mis_comprobantes(request), pk=pk)
    cobros = list(c.cobros.select_related("cuenta", "extorna_a").order_by("-fecha", "-id")) if request.user.has_perm("tesoreria.view_movimiento") else []
    for movimiento in cobros:
        movimiento.ya_extornado = hasattr(movimiento, "extorno")
    return render(request, "facturacion/detalle.html", {
        "c": c,
        "lineas": c.lineas.select_related("producto"),
        "cobros": cobros,
        "notas": c.notas.all(),
        "asientos": c.asientos.all() if request.user.has_perm("contabilidad.view_asiento") else [],
        "registros": c.empresa.registrointegracion_set.filter(
            entidad="facturacion.Comprobante", objeto_id=str(c.pk)
        ).select_related("servicio")[:10] if request.user.has_perm("integraciones.view_registrointegracion") else [],
        "form_nota": FormularioNota(comprobante=c),
        "form_extorno": FormularioExtorno(),
        "E": EstadoComprobante,
        "T": TipoComprobante,
        "puede_nota": c.tipo in (TipoComprobante.FACTURA, TipoComprobante.BOLETA)
        and c.estado in (EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO),
    })


@login_required
@vista_serializada
@require_POST
def nota_credito(request, pk):
    c = get_object_or_404(_mis_comprobantes(request), pk=pk)
    form = FormularioNota(request.POST, comprobante=c)
    if not form.is_valid():
        messages.error(request, "Revisa el motivo y el monto.")
        return redirect("facturacion:detalle", pk=pk)
    try:
        datos = form.cleaned_data
        if datos["tipo_nota"] == "debito":
            nota, _ = emitir_nota_debito(c, datos["motivo_debito"], datos["monto"], request.user)
        elif datos["tipo_nota"] == "descripcion":
            nota, _ = emitir_correccion_descripcion(c, datos["linea"].pk, datos["descripcion"], request.user)
        else:
            nota, _ = emitir_nota_credito(c, motivo=datos["motivo_credito"],
                                          monto=datos["monto"], usuario=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("facturacion:detalle", pk=pk)
    messages.success(request, f"{nota.get_tipo_display()} {nota.numero_completo} emitida y en cola hacia SUNAT.")
    return redirect("facturacion:detalle", pk=nota.pk)


@login_required
@vista_serializada
@require_POST
def reenviar(request, pk):
    """Un rechazado corregido vuelve a la cola con el mismo correlativo."""
    c = get_object_or_404(_mis_comprobantes(request), pk=pk)
    if c.estado not in (EstadoComprobante.RECHAZADO, EstadoComprobante.POR_ENVIAR):
        messages.error(request, "Solo se reenvía un comprobante rechazado.")
        return redirect("facturacion:detalle", pk=pk)
    from apps.integraciones.models import TrabajoIntegracion
    from django.utils import timezone

    TrabajoIntegracion.objects.filter(
        empresa=c.empresa, entidad="facturacion.Comprobante", objeto_id=str(c.pk)
    ).update(estado=TrabajoIntegracion.Estado.EN_COLA, intentos=0, ejecutar_despues_de=timezone.now())
    c.estado = EstadoComprobante.POR_ENVIAR
    c.save(update_fields=["estado", "actualizado_en"])
    encolar_envio(c)
    messages.success(request, f"{c.numero_completo} vuelve a la cola de envío.")
    return redirect("facturacion:detalle", pk=pk)
