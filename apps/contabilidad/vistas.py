"""Asientos y plan de cuentas, solo lectura: nacen de los documentos."""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django import forms
from django.shortcuts import get_object_or_404, render

from apps.contabilidad.models import Asiento, CuentaContable, LineaAsiento


class FechasBalance(forms.Form):
    desde = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    hasta = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['desde'] > datos['hasta']:
            raise forms.ValidationError('La fecha inicial debe ser anterior a la final.')
        return datos


@login_required
def asientos(request):
    qs = Asiento.objects.filter(empresa=request.empresa).select_related("comprobante", "periodo")
    periodo = request.GET.get("periodo", "")
    if periodo:
        from datetime import datetime
        try:
            fecha = datetime.strptime(periodo, '%Y-%m')
            qs = qs.filter(fecha__year=fecha.year, fecha__month=fecha.month)
        except ValueError:
            qs = qs.none()
    pagina = Paginator(qs, 40).get_page(request.GET.get("pagina"))
    for a in pagina:
        a.debe = a.total_debe
    return render(request, "contabilidad/asientos.html", {
        "page_obj": pagina, "periodo": periodo, "consulta": f"periodo={periodo}",
    })


@login_required
def asiento(request, pk):
    a = get_object_or_404(Asiento, pk=pk, empresa=request.empresa)
    return render(request, "contabilidad/asiento.html", {
        "a": a, "lineas": a.lineas.select_related("cuenta", "tercero"),
    })


@login_required
def balance(request):
    """Saldos por cuenta: debe, haber y saldo según naturaleza."""
    cuentas = CuentaContable.objects.filter(empresa=request.empresa, acepta_movimiento=True)
    form = FechasBalance(request.GET)
    lineas = LineaAsiento.objects.filter(empresa=request.empresa)
    if form.is_valid():
        if form.cleaned_data.get('desde'):
            lineas = lineas.filter(asiento__fecha__gte=form.cleaned_data['desde'])
        if form.cleaned_data.get('hasta'):
            lineas = lineas.filter(asiento__fecha__lte=form.cleaned_data['hasta'])
    else:
        lineas = lineas.none()
    agregados = {r['cuenta_id']: r for r in lineas.values('cuenta_id').annotate(d=Sum('debe'), h=Sum('haber'))}
    filas = []
    for c in cuentas:
        agregado = agregados.get(c.pk, {'d': 0, 'h': 0})
        debe = agregado["d"] or Decimal("0")
        haber = agregado["h"] or Decimal("0")
        if not debe and not haber:
            continue
        saldo = debe - haber if c.naturaleza == "deudora" else haber - debe
        filas.append({"cuenta": c, "debe": debe, "haber": haber, "saldo": saldo})
    if request.GET.get('exportar') == 'csv' and form.is_valid():
        from apps.gestion.vistas import csv_seguro
        return csv_seguro('balance', ['Cuenta', 'Nombre', 'Debe', 'Haber', 'Saldo'],
            [(f['cuenta'].codigo, f['cuenta'].nombre, f['debe'], f['haber'], f['saldo']) for f in filas])
    return render(request, "contabilidad/balance.html", {
        'form': form,
        "filas": filas,
        "total_debe": sum(f["debe"] for f in filas),
        "total_haber": sum(f["haber"] for f in filas),
    })
