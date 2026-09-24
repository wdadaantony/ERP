"""Cobranza: qué se debe, registrar cobros, conciliar."""
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.alcance import ve_todo
from apps.core.transacciones import vista_serializada
from apps.core.monedas import a_moneda_base
from apps.facturacion.models import Comprobante, EstadoComprobante, TipoComprobante
from apps.tesoreria.models import CuentaBancaria, LineaExtractoBancario, MetodoPago, Movimiento
from apps.tesoreria.servicios import conciliar_extracto, registrar_cobro, extornar_cobro


class FormularioCobro(forms.Form):
    tipo_cambio = forms.DecimalField(label="Tipo de cambio del cobro", required=False,
        min_value=Decimal("0.000001"), decimal_places=6,
        help_text="Moneda base por unidad de la moneda cobrada; obligatorio en moneda extranjera.")
    monto = forms.DecimalField(label="Monto", min_value=Decimal("0.01"), decimal_places=2)
    metodo = forms.ChoiceField(label="Método", choices=MetodoPago.choices, initial=MetodoPago.TRANSFERENCIA)
    cuenta = forms.ModelChoiceField(label="Cuenta bancaria", queryset=CuentaBancaria.objects.none(), required=False)
    fecha = forms.DateField(label="Fecha", initial=date.today, widget=forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"}))
    referencia_externa = forms.CharField(label="Referencia / N.º de operación", max_length=120, required=False)

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta"].queryset = CuentaBancaria.objects.filter(empresa=empresa, activa=True)


class FormularioExtorno(forms.Form):
    motivo = forms.CharField(min_length=5, max_length=255, widget=forms.Textarea(attrs={"rows": 2}))
    fecha = forms.DateField(initial=date.today, widget=forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"}))


def _por_cobrar(request):
    qs = Comprobante.objects.filter(
        empresa=request.empresa,
        tipo__in=(TipoComprobante.FACTURA, TipoComprobante.BOLETA),
        estado__in=(EstadoComprobante.ACEPTADO, EstadoComprobante.OBSERVADO),
    ).select_related("tercero", "pedido")
    if not ve_todo(request.user, "ventas"):
        qs = qs.filter(pedido__vendedor=request.user)
    return [c for c in qs if not c.esta_pagado]


@login_required
def cobranza(request):
    pendientes = _por_cobrar(request)
    hoy = date.today()
    for c in pendientes:
        c.vencido = bool(c.fecha_vencimiento and c.fecha_vencimiento < hoy)
        c.dias = (hoy - c.fecha_vencimiento).days if c.vencido else 0
    pendientes.sort(key=lambda c: (not c.vencido, c.fecha_vencimiento or hoy))
    return render(request, "tesoreria/cobranza.html", {
        "pendientes": pendientes,
        "total": sum((c.saldo_base for c in pendientes), Decimal("0")),
        "vencido": sum((c.saldo_base for c in pendientes if c.vencido), Decimal("0")),
        "por_vencer": sum((c.saldo_base for c in pendientes if not c.vencido), Decimal("0")),
        "form": FormularioCobro(empresa=request.empresa),
    })


@login_required
@vista_serializada
@require_POST
def cobrar(request, pk):
    from apps.facturacion.vistas import _mis_comprobantes
    c = get_object_or_404(_mis_comprobantes(request), pk=pk)
    form = FormularioCobro(request.POST, empresa=request.empresa)
    if not form.is_valid():
        messages.error(request, "Revisa los datos del cobro: " + " ".join(
            f"{campo}: {' '.join(errs)}" for campo, errs in form.errors.items()
        ))
        return redirect(request.POST.get("volver") or "tesoreria:cobranza")
    try:
        movimiento = registrar_cobro(
            c, form.cleaned_data["monto"], metodo=form.cleaned_data["metodo"],
            cuenta=form.cleaned_data["cuenta"], referencia_externa=form.cleaned_data["referencia_externa"],
            fecha=form.cleaned_data["fecha"],
            tipo_cambio=form.cleaned_data["tipo_cambio"], usuario=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(request.POST.get("volver") or "tesoreria:cobranza")
    c.refresh_from_db()
    if c.esta_pagado:
        messages.success(request, f"{c.numero_completo} quedó pagado. Cobro de {movimiento.monto} registrado.")
    else:
        messages.success(request, f"Cobro parcial de {movimiento.monto} registrado. Saldo: {c.saldo}.")
    return redirect(request.POST.get("volver") or "tesoreria:cobranza")


@login_required
@vista_serializada
@require_POST
def extornar(request, pk):
    movimiento = get_object_or_404(
        Movimiento, pk=pk, empresa=request.empresa, sentido=Movimiento.Sentido.COBRO,
        comprobante__pedido__vendedor=request.user,
    ) if not ve_todo(request.user, "ventas") else get_object_or_404(
        Movimiento, pk=pk, empresa=request.empresa, sentido=Movimiento.Sentido.COBRO,
    )
    form = FormularioExtorno(request.POST)
    if not form.is_valid():
        messages.error(request, "El extorno requiere fecha y motivo de al menos cinco caracteres.")
    else:
        try:
            extorno = extornar_cobro(movimiento, form.cleaned_data["motivo"],
                                     request.user, form.cleaned_data["fecha"])
            messages.success(request, f"Extorno {extorno.pk} registrado. La deuda volvió a abrirse.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("facturacion:detalle", pk=movimiento.comprobante_id)


@login_required
def movimientos(request):
    movs = Movimiento.objects.filter(empresa=request.empresa).select_related("tercero", "comprobante", "cuenta")
    sentido = request.GET.get("sentido", "")
    if sentido:
        movs = movs.filter(sentido=sentido)
    pagina = Paginator(movs, 40).get_page(request.GET.get("pagina"))
    return render(request, "tesoreria/movimientos.html", {
        "page_obj": pagina, "sentido": sentido, "consulta": f"sentido={sentido}",
    })


@login_required
@vista_serializada
def conciliacion(request):
    cuentas = CuentaBancaria.objects.filter(empresa=request.empresa, activa=True)
    cuenta = None
    if request.GET.get("cuenta"):
        cuenta = get_object_or_404(cuentas, pk=request.GET["cuenta"])
    elif cuentas.exists():
        cuenta = cuentas.first()

    if request.method == "POST" and cuenta:
        if "conciliar" in request.POST:
            n = conciliar_extracto(cuenta)
            messages.success(request, f"{n} línea{'s' if n != 1 else ''} conciliada{'s' if n != 1 else ''}.")
        elif "importar" in request.POST:
            creadas = _importar_extracto(request, cuenta)
            messages.success(request, f"{creadas} línea{'s' if creadas != 1 else ''} del extracto importada{'s' if creadas != 1 else ''}.")
        return redirect(f"{request.path}?cuenta={cuenta.pk}")

    lineas = (
        LineaExtractoBancario.objects.filter(cuenta=cuenta).select_related("movimiento")[:200]
        if cuenta else []
    )
    return render(request, "tesoreria/conciliacion.html", {
        "cuentas": cuentas, "cuenta": cuenta, "lineas": lineas,
        "sin_conciliar": sum(1 for l in lineas if not l.conciliada),
    })


def _importar_extracto(request, cuenta):
    """Pega el extracto como texto: fecha;descripción;monto;referencia por línea."""
    import csv
    import io
    from datetime import datetime

    texto = request.POST.get("extracto", "")
    creadas = 0
    for fila in csv.reader(io.StringIO(texto), delimiter=";"):
        if len(fila) < 3:
            continue
        try:
            fecha = datetime.strptime(fila[0].strip(), "%d/%m/%Y").date()
            monto = Decimal(fila[2].strip().replace(",", ""))
        except (ValueError, ArithmeticError):
            continue
        LineaExtractoBancario.objects.create(
            empresa=request.empresa, cuenta=cuenta, fecha=fecha, descripcion=fila[1].strip()[:255],
            monto=monto, referencia=(fila[3].strip() if len(fila) > 3 else "")[:120],
        )
        creadas += 1
    return creadas
