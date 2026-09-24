from django import forms
from django.forms import inlineformset_factory

from apps.terceros.models import CondicionPago, Contacto, DireccionEntrega, Tercero


class FormularioTercero(forms.ModelForm):
    class Meta:
        model = Tercero
        fields = (
            "es_cliente", "es_proveedor", "tipo_documento", "numero_documento",
            "razon_social", "nombre_comercial", "direccion_fiscal", "ubigeo",
            "email", "telefono", "condicion_pago", "linea_credito", "vendedor_asignado",
        )

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["condicion_pago"].queryset = CondicionPago.objects.filter(empresa=empresa)
        self.fields["vendedor_asignado"].queryset = empresa.usuarios.filter(is_active=True)
        self.fields["vendedor_asignado"].required = False


FormsetContactos = inlineformset_factory(
    Tercero, Contacto, fields=("nombre", "cargo", "email", "telefono"), extra=1, can_delete=True
)
FormsetDirecciones = inlineformset_factory(
    Tercero, DireccionEntrega, fields=("etiqueta", "direccion", "ubigeo", "referencia", "es_principal"),
    extra=1, can_delete=True,
)
