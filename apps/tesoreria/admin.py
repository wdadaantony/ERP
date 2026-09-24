from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.tesoreria.models import CuentaBancaria, LineaExtractoBancario, Movimiento


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("banco", "numero", "moneda", "cuenta_contable", "activa", "empresa")
    list_filter = ("empresa", "moneda", "activa")


@admin.register(Movimiento)
class MovimientoAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = (
        "fecha",
        "sentido",
        "tercero",
        "monto",
        "moneda",
        "metodo",
        "estado",
        "referencia_externa",
    )
    list_filter = ("empresa", "sentido", "estado", "metodo", "moneda")
    search_fields = ("referencia_externa", "tercero__razon_social")
    date_hierarchy = "fecha"
    autocomplete_fields = ("tercero",)


@admin.register(LineaExtractoBancario)
class LineaExtractoBancarioAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("fecha", "cuenta", "descripcion", "monto", "referencia", "conciliada")
    list_filter = ("empresa", "cuenta", "conciliada")
    search_fields = ("descripcion", "referencia")
    date_hierarchy = "fecha"
