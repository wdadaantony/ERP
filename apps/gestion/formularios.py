from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.contabilidad.models import CuentaContable
from apps.terceros.models import Tercero
from apps.compras.models import OrdenCompra
from apps.catalogo.models import Producto
from apps.inventario.models import Ubicacion, MovimientoStock
from apps.tesoreria.models import CuentaBancaria
from .models import FacturaProveedor, OperacionInventario


class FormularioFactura(forms.ModelForm):
    class Meta:
        model = FacturaProveedor
        fields = ('proveedor', 'numero', 'orden', 'fecha', 'vencimiento', 'moneda', 'tipo_cambio', 'subtotal', 'impuestos', 'cuenta_destino', 'concepto')
        widgets = {n: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}) for n in ('fecha', 'vencimiento')}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.empresa = empresa
        self.fields['proveedor'].queryset = Tercero.objects.filter(empresa=empresa, es_proveedor=True, activo=True)
        self.fields['orden'].queryset = OrdenCompra.objects.filter(empresa=empresa).exclude(estado__in=['borrador', 'cancelada'])
        self.fields['cuenta_destino'].queryset = CuentaContable.objects.filter(empresa=empresa, activa=True, acepta_movimiento=True)
        self.fields['cuenta_destino'].help_text = 'Cuenta de mercadería o gasto aprobada por tu responsable contable.'
        self.fields['subtotal'].min_value = 0
        self.fields['impuestos'].min_value = 0
        self.initial.update(fecha=timezone.localdate(), vencimiento=timezone.localdate(), moneda=empresa.moneda_base)

    def clean(self):
        datos = super().clean()
        if datos.get('fecha') and datos.get('vencimiento') and datos['fecha'] > datos['vencimiento']:
            self.add_error('vencimiento', 'No puede ser anterior a la fecha de emisión.')
        if datos.get('numero'):
            datos['numero'] = datos['numero'].strip().upper()
            if FacturaProveedor.objects.filter(empresa=self.instance.empresa, proveedor=datos.get('proveedor'), numero=datos['numero']).exists():
                self.add_error('numero', 'Esta factura del proveedor ya está registrada.')
        return datos


class FormularioPago(forms.Form):
    monto = forms.DecimalField(min_value=.01, max_digits=14, decimal_places=2)
    cuenta = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none())
    referencia = forms.CharField(max_length=120, label='Referencia bancaria única')
    fecha = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    tipo_cambio = forms.DecimalField(min_value=.000001, max_digits=10, decimal_places=6, required=False)

    def __init__(self, *args, factura, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = CuentaBancaria.objects.filter(empresa=factura.empresa, moneda=factura.moneda, activa=True)
        self.fields['monto'].initial = factura.saldo
        self.fields['tipo_cambio'].required = factura.moneda != factura.empresa.moneda_base


class FormularioInventario(forms.ModelForm):
    class Meta:
        model = OperacionInventario
        fields = ('tipo', 'producto', 'origen', 'destino', 'cantidad', 'movimiento_original', 'motivo')

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(empresa=empresa, activo=True, controla_stock=True)
        self.fields['origen'].queryset = Ubicacion.objects.filter(empresa=empresa, tipo='interna')
        self.fields['destino'].queryset = self.fields['origen'].queryset
        self.fields['movimiento_original'].queryset = MovimientoStock.objects.filter(empresa=empresa).select_related('producto', 'destino')
        self.fields['cantidad'].help_text = 'Conteo: existencia física observada. Transferencia/devolución: unidades a mover. No modifica saldos de facturas.'
        self.fields['movimiento_original'].help_text = 'Solo para devolución. Se invierten las ubicaciones del movimiento original.'
        self.fields['motivo'].min_length = 5

    def clean(self):
        datos = super().clean()
        cantidad = datos.get('cantidad')
        if cantidad is not None and (cantidad < 0 or (cantidad == 0 and datos.get('tipo') != 'conteo')):
            self.add_error('cantidad', 'Debe ser positiva; el conteo puede ser cero.')
        if datos.get('tipo') == 'transferencia' and not datos.get('destino'):
            self.add_error('destino', 'Selecciona el destino.')
        if datos.get('tipo') == 'devolucion' and not datos.get('movimiento_original'):
            self.add_error('movimiento_original', 'Selecciona el movimiento a devolver.')
        return datos


class FormularioPeriodo(forms.Form):
    periodo = forms.ModelChoiceField(queryset=None)
    usd = forms.DecimalField(label='Tipo de cambio USD al cierre', decimal_places=6, max_digits=10, min_value=.000001, required=False)
    eur = forms.DecimalField(label='Tipo de cambio EUR al cierre', decimal_places=6, max_digits=10, min_value=.000001, required=False)
    pen = forms.DecimalField(label='Tipo de cambio PEN al cierre', decimal_places=6, max_digits=10, min_value=.000001, required=False)
    confirmar = forms.BooleanField(label='Confirmo que se revisaron los movimientos. El cierre bloquea nuevos asientos en este periodo.')

    def __init__(self, *args, empresa, **kwargs):
        from apps.contabilidad.models import PeriodoContable
        super().__init__(*args, **kwargs)
        self.fields['periodo'].queryset = PeriodoContable.objects.filter(empresa=empresa, cerrado=False)
