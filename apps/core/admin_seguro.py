"""Los documentos se modifican por sus servicios, nunca desde el administrador."""
from django.core.exceptions import PermissionDenied


class EmpresaAdminMixin:
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        empresa = getattr(request, "empresa", None)
        qs = qs.filter(empresa=empresa) if empresa else qs.none()
        from apps.core.alcance import ve_todo
        propietarios = {"ventas.pedido": ("ventas", "vendedor"),
                        "facturacion.comprobante": ("ventas", "pedido__vendedor"),
                        "terceros.tercero": ("terceros", "vendedor_asignado"),
                        "crm.lead": ("crm", "vendedor")}
        alcance = propietarios.get(self.model._meta.label_lower)
        if alcance and not ve_todo(request.user, alcance[0]):
            qs = qs.filter(**{alcance[1]: request.user})
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        modelo = db_field.remote_field.model
        if not request.user.is_superuser:
            empresa = getattr(request, "empresa", None)
            if db_field.name == "empresa":
                kwargs["queryset"] = modelo.objects.filter(pk=getattr(empresa, "pk", None))
            elif any(f.name == "empresa" for f in modelo._meta.fields):
                kwargs["queryset"] = modelo.objects.filter(empresa=empresa) if empresa else modelo.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and obj.empresa_id != getattr(request.empresa, "pk", None):
            raise PermissionDenied("No puedes modificar otra empresa.")
        super().save_model(request, obj, form, change)


class DocumentoProtegidoMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}

    def get_readonly_fields(self, request, obj=None):
        return tuple(f.name for f in self.model._meta.fields)


class SoloSuperusuarioMixin:
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission
