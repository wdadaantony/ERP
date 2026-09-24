"""Los roles explícitos de una membresía sustituyen los permisos globales."""
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Permission


class PermisosPorEmpresa(ModelBackend):
    def _get_permissions(self, user_obj, obj, from_name):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()
        if user_obj.is_superuser:
            return super()._get_permissions(user_obj, obj, from_name)
        from .models import Membresia
        membresias = Membresia.objects.filter(usuario=user_obj)
        if not membresias.exists():
            return super()._get_permissions(user_obj, obj, from_name)
        if from_name == 'user':
            return set()
        miembro = membresias.filter(empresa_id=user_obj.empresa_actual_id).first()
        if not miembro or not user_obj.empresas.filter(pk=miembro.empresa_id, activa=True).exists():
            return set()
        permisos = Permission.objects.filter(group__in=miembro.roles.all()).values_list('content_type__app_label', 'codename')
        return {f'{app}.{codigo}' for app, codigo in permisos}

    def get_all_permissions(self, user_obj, obj=None):
        return self.get_user_permissions(user_obj, obj) | self.get_group_permissions(user_obj, obj)
