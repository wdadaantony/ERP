"""Alta sin datos de demostración, stock ni cuentas contables inventadas."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.core.models import Empresa, Usuario, Serie
from apps.core.permisos import crear_roles
from apps.gestion.models import PerfilEmpresa, Membresia
from apps.inventario.models import Almacen, Ubicacion
from apps.terceros.models import CondicionPago
from django.contrib.auth.models import Group


class Command(BaseCommand):
    help = 'Crea una empresa vacía y asigna un usuario existente como gerente solo en esa empresa.'

    def add_arguments(self, parser):
        parser.add_argument('--ruc', required=True)
        parser.add_argument('--razon-social', required=True)
        parser.add_argument('--usuario', required=True)
        parser.add_argument('--moneda', choices=['PEN', 'USD', 'EUR'], default='PEN')
        parser.add_argument('--rubro', default='')

    @transaction.atomic
    def handle(self, *args, **options):
        if len(options['ruc']) != 11 or not options['ruc'].isdigit():
            raise CommandError('El RUC debe contener 11 dígitos.')
        if Empresa.objects.filter(ruc=options['ruc']).exists():
            raise CommandError('La empresa ya existe; no se modificó nada.')
        try:
            usuario = Usuario.objects.get(username=options['usuario'], is_active=True)
        except Usuario.DoesNotExist:
            raise CommandError('Crea primero un usuario activo en Administración.')
        crear_roles()
        # Conservar explícitamente sus roles previos antes de activar membresías.
        if not usuario.membresias.exists():
            for anterior in usuario.empresas.all():
                miembro = Membresia.objects.create(empresa=anterior, usuario=usuario)
                miembro.roles.set(usuario.groups.all())
                if usuario.user_permissions.exists():
                    grupo, _ = Group.objects.get_or_create(name=f'ERP · Usuario {usuario.pk} · Empresa {anterior.pk}')
                    grupo.permissions.set(usuario.user_permissions.all())
                    miembro.roles.add(grupo)
        empresa = Empresa.objects.create(ruc=options['ruc'], razon_social=options['razon_social'], moneda_base=options['moneda'])
        PerfilEmpresa.objects.create(empresa=empresa, rubro=options['rubro'])
        usuario.empresas.add(empresa)
        miembro = Membresia.objects.create(empresa=empresa, usuario=usuario)
        miembro.roles.add(Group.objects.get(name='ERP · Gerencia'))
        almacen = Almacen.objects.create(empresa=empresa, codigo='ALM-01', nombre='Almacén principal')
        Ubicacion.objects.create(empresa=empresa, almacen=almacen, codigo='ALM-01/STOCK', nombre='Stock', tipo='interna')
        for tipo in ['proveedor', 'cliente', 'ajuste', 'transito']:
            Ubicacion.objects.create(empresa=empresa, codigo=f'VIRT/{tipo.upper()}', nombre=tipo.title(), tipo=tipo)
        CondicionPago.objects.create(empresa=empresa, nombre='Contado', dias=0)
        for tipo, serie in [('PED', 'PED'), ('01', 'F001'), ('03', 'B001'), ('07', 'FC01'), ('08', 'FD01')]:
            Serie.objects.create(empresa=empresa, tipo_documento=tipo, serie=serie)
        self.stdout.write(f'Empresa {empresa.pk} creada. Configura plan contable, bancos, series autorizadas e integraciones antes de operar.')
