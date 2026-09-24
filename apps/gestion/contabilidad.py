from decimal import Decimal
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.forms import formset_factory
from django.shortcuts import render, redirect
from django.utils import timezone
from apps.contabilidad.models import CuentaContable, LineaAsiento, Asiento
from apps.contabilidad.servicios import _crear_asiento
from apps.core.transacciones import vista_serializada
from apps.core.auditoria import registrar


class Cabecera(forms.Form):
    fecha = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    glosa = forms.CharField(min_length=5, max_length=255)


class LineaManual(forms.Form):
    cuenta = forms.ModelChoiceField(queryset=CuentaContable.objects.none())
    debe = forms.DecimalField(min_value=0, max_digits=14, decimal_places=2, required=False)
    haber = forms.DecimalField(min_value=0, max_digits=14, decimal_places=2, required=False)

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = CuentaContable.objects.filter(empresa=empresa, activa=True, acepta_movimiento=True)

    def clean(self):
        datos = super().clean()
        debe, haber = datos.get('debe') or Decimal('0'), datos.get('haber') or Decimal('0')
        if not (debe or haber) or (debe and haber):
            raise ValidationError('Indica un importe positivo en debe o haber, no en ambos.')
        datos.update(debe=debe, haber=haber)
        return datos


@login_required
@vista_serializada
def ajuste(request):
    form = Cabecera(request.POST or None)
    Lineas = formset_factory(LineaManual, extra=6, max_num=20, validate_max=True)
    lineas = Lineas(request.POST or None, prefix='lineas', form_kwargs={'empresa': request.empresa})
    if request.method == 'POST' and form.is_valid() and lineas.is_valid():
        datos = [f.cleaned_data for f in lineas if f.cleaned_data]
        if len(datos) < 2 or sum(f['debe'] for f in datos) != sum(f['haber'] for f in datos):
            form.add_error(None, 'El asiento debe tener al menos dos líneas y cuadrar exactamente.')
        else:
            from django.db import transaction
            try:
                with transaction.atomic():
                    asiento = _crear_asiento(request.empresa, form.cleaned_data['fecha'], form.cleaned_data['glosa'],
                        [(f['cuenta'], None, form.cleaned_data['glosa'], f['debe'], f['haber']) for f in datos], creado_por=request.user)
                    asiento.generado_automaticamente = False
                    asiento.save(update_fields=['generado_automaticamente'])
                    registrar(asiento, request.user, 'crear', despues={'numero': asiento.numero, 'glosa': asiento.glosa})
                return redirect('contabilidad:asiento', pk=asiento.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
    return render(request, 'gestion/ajuste.html', {'form': form, 'lineas': lineas})


@login_required
def flujo_caja(request):
    from apps.tesoreria.models import Movimiento
    from apps.core.monedas import a_moneda_base
    from apps.contabilidad.vistas import FechasBalance
    form = FechasBalance(request.GET)
    grupos = {}
    if form.is_valid():
        qs = Movimiento.objects.filter(empresa=request.empresa, estado__in=['confirmado', 'conciliado']).select_related('empresa', 'cuenta')
        if form.cleaned_data.get('desde'):
            qs = qs.filter(fecha__gte=form.cleaned_data['desde'])
        if form.cleaned_data.get('hasta'):
            qs = qs.filter(fecha__lte=form.cleaned_data['hasta'])
        for m in qs:
            key = str(m.cuenta) if m.cuenta_id else 'Sin cuenta bancaria'
            fila = grupos.setdefault(key, {'cuenta': key, 'entradas': Decimal('0'), 'salidas': Decimal('0'), 'neto': Decimal('0')})
            monto = a_moneda_base(m.monto - m.comision, m)
            campo = 'entradas' if m.sentido == 'cobro' else 'salidas'
            fila[campo] += monto
            fila['neto'] += monto if campo == 'entradas' else -monto
    return render(request, 'gestion/flujo_caja.html', {'form': form, 'filas': grupos.values()})
