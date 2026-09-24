from django.contrib import admin
from apps.core.admin_seguro import DocumentoProtegidoMixin, EmpresaAdminMixin, SoloSuperusuarioMixin
from .models import FacturaProveedor, PagoProveedor, OperacionInventario, PerfilEmpresa, Membresia


@admin.register(FacturaProveedor, PagoProveedor, OperacionInventario)
class DocumentoAdmin(DocumentoProtegidoMixin, EmpresaAdminMixin, admin.ModelAdmin):
    pass


@admin.register(PerfilEmpresa, Membresia)
class ConfiguracionAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    pass
