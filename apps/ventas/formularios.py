from django import forms
from django.forms import inlineformset_factory

from apps.catalogo.models import Producto
from apps.inventario.models import Almacen
from apps.terceros.models import DireccionEntrega, Tercero
from apps.ventas.models import LineaPedido, Pedido
from apps.core.monedas import factor_cambio


class FormularioPedido(forms.ModelForm):
    class Meta:
        model = Pedido
        fields = (
            "tercero",
            "direccion_entrega",
            "almacen",
            "fecha",
            "valido_hasta",
            "moneda",
            "tipo_cambio",
            "origen",
            "campania",
            "notas",
        )
        widgets = {
            "fecha": forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"}),
            "valido_hasta": forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"}),
            "notas": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["tercero"].queryset = Tercero.objects.filter(
            empresa=empresa, es_cliente=True, activo=True
        )
        self.fields["almacen"].queryset = Almacen.objects.filter(empresa=empresa, activo=True)
        self.fields["direccion_entrega"].queryset = DireccionEntrega.objects.filter(
            empresa=empresa
        )
        self.fields["direccion_entrega"].required = False
        if self.fields["almacen"].queryset.count() == 1:
            self.fields["almacen"].initial = self.fields["almacen"].queryset.first()

    def clean(self):
        datos = super().clean()
        if datos.get("moneda") and datos.get("tipo_cambio") is not None:
            datos["tipo_cambio"] = factor_cambio(datos["moneda"], self.empresa, datos["tipo_cambio"])
        return datos


class FormularioLinea(forms.ModelForm):
    class Meta:
        model = LineaPedido
        fields = ("producto", "descripcion", "cantidad", "precio_unitario", "descuento_pct")
        widgets = {
            "cantidad": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "precio_unitario": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "descuento_pct": forms.NumberInput(attrs={"step": "0.5", "min": "0", "max": "100"}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa is not None:
            self.fields["producto"].queryset = Producto.objects.filter(
                empresa=empresa, activo=True
            ).select_related("unidad_medida")
        self.fields["descripcion"].required = False
        # Los precios se rellenan solos al elegir el producto (ver pedido.js).
        self.fields["producto"].widget.attrs["data-precios"] = "1"


class BaseFormsetLineas(forms.BaseInlineFormSet):
    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["empresa"] = self.empresa
        return kwargs

    def clean(self):
        super().clean()
        vivas = [
            f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE", False)
        ]
        if not vivas:
            raise forms.ValidationError("El pedido necesita al menos una línea.")


FormsetLineas = inlineformset_factory(
    Pedido,
    LineaPedido,
    form=FormularioLinea,
    formset=BaseFormsetLineas,
    extra=1,
    can_delete=True,
)


class FormularioCancelar(forms.Form):
    motivo = forms.CharField(
        label="Motivo", widget=forms.Textarea(attrs={"rows": 2}), required=False
    )


class FormularioOrdenCompra(forms.Form):
    proveedor = forms.ModelChoiceField(label="Proveedor", queryset=Tercero.objects.none())

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].queryset = Tercero.objects.filter(
            empresa=empresa, es_proveedor=True, activo=True
        )


class FormularioGuia(forms.Form):
    punto_llegada = forms.CharField(label="Dirección de llegada", max_length=255)
    fecha_traslado = forms.DateField(
        label="Fecha de traslado", widget=forms.DateInput(format='%Y-%m-%d', attrs={"type": "date"})
    )
    transportista = forms.CharField(label="Transportista", max_length=150, required=False)
    placa = forms.CharField(label="Placa", max_length=10, required=False)
    bultos = forms.IntegerField(label="Bultos", min_value=1, initial=1)
    peso_kg = forms.DecimalField(label="Peso (kg)", min_value=0, initial=0, decimal_places=3)
