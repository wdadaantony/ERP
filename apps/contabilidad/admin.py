from django.contrib import admin
from apps.core.admin_seguro import EmpresaAdminMixin, DocumentoProtegidoMixin, SoloSuperusuarioMixin

from apps.contabilidad.models import (
    Asiento,
    CuentaContable,
    LineaAsiento,
    PeriodoContable,
    ReglaContable,
    TipoCambioCierre,
)


@admin.register(CuentaContable)
class CuentaContableAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("codigo", "nombre", "naturaleza", "acepta_movimiento", "activa", "empresa")
    list_filter = ("empresa", "naturaleza", "acepta_movimiento", "activa")
    search_fields = ("codigo", "nombre")


class LineaAsientoInline(DocumentoProtegidoMixin, admin.TabularInline):
    model = LineaAsiento
    extra = 0
    autocomplete_fields = ("cuenta", "tercero")


@admin.register(Asiento)
class AsientoAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("numero", "fecha", "glosa", "total_debe", "total_haber", "esta_cuadrado")
    list_filter = ("empresa", "generado_automaticamente", "periodo")
    search_fields = ("numero", "glosa")
    date_hierarchy = "fecha"
    inlines = (LineaAsientoInline,)

    @admin.display(boolean=True, description="Cuadra")
    def esta_cuadrado(self, obj):
        return obj.cuadra


@admin.register(PeriodoContable)
class PeriodoContableAdmin(EmpresaAdminMixin, admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        return not (obj and obj.cerrado) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(TipoCambioCierre)
class TipoCambioCierreAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    list_display = ("periodo", "moneda", "tasa", "empresa")
    list_filter = ("empresa", "moneda", "periodo")


@admin.register(ReglaContable)
class ReglaContableAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    list_display = ("tipo_documento", "cuenta_debe", "cuenta_haber", "empresa")
    list_filter = ("empresa",)
    autocomplete_fields = ("cuenta_debe", "cuenta_haber")
