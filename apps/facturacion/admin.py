from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.facturacion.models import Comprobante, LineaComprobante


class LineaComprobanteInline(DocumentoProtegidoMixin, admin.TabularInline):
    model = LineaComprobante
    extra = 0
    autocomplete_fields = ("producto",)


@admin.register(Comprobante)
class ComprobanteAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = (
        "numero_completo",
        "tipo",
        "tercero",
        "fecha_emision",
        "total",
        "total_cobrado",
        "estado",
    )
    list_filter = ("empresa", "tipo", "estado", "moneda")
    search_fields = ("serie", "correlativo", "tercero__razon_social", "id_externo")
    date_hierarchy = "fecha_emision"
    inlines = (LineaComprobanteInline,)
    autocomplete_fields = ("tercero",)
    readonly_fields = ("hash_cpe", "codigo_respuesta", "mensaje_respuesta", "id_externo", "cdr")
    fieldsets = (
        (None, {"fields": ("empresa", "tipo", "serie", "correlativo", "estado")}),
        ("Partes", {"fields": ("tercero", "pedido")}),
        (
            "Fechas e importes",
            {
                "fields": (
                    "fecha_emision",
                    "fecha_vencimiento",
                    "moneda",
                    "tipo_cambio",
                    "subtotal",
                    "igv",
                    "total",
                    "total_cobrado",
                )
            },
        ),
        (
            "Respuesta de SUNAT",
            {
                "fields": (
                    "hash_cpe",
                    "xml_firmado",
                    "cdr",
                    "pdf",
                    "codigo_respuesta",
                    "mensaje_respuesta",
                    "id_externo",
                )
            },
        ),
        ("Notas de crédito y débito", {"fields": ("comprobante_afectado", "motivo_nota")}),
    )
