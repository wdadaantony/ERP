"""Lista verificable de configuración comercial por empresa."""
from django.conf import settings
from apps.contabilidad.models import ReglaContable
from apps.integraciones.models import ServicioExterno
from apps.integraciones.configuracion import diagnostico
from apps.tesoreria.models import CuentaBancaria
from .models import Membresia, PerfilEmpresa


def revisar(empresa):
    reglas = set(ReglaContable.objects.filter(empresa=empresa).values_list('tipo_documento', flat=True))
    necesarias = {'venta_factura', 'venta_igv', 'cobro', 'compra', 'compra_igv', 'ganancia_cambio', 'perdida_cambio'}
    perfil = PerfilEmpresa.objects.filter(empresa=empresa).first()
    if perfil and perfil.contabilizar_inventario:
        necesarias.update({'costo_venta', 'ajuste_inventario'})
    faltantes = sorted(necesarias - reglas)
    controles = [
        ('Reglas contables', not faltantes, 'Faltan: ' + ', '.join(faltantes) if faltantes else 'Configuradas; deben ser validadas por el responsable contable.'),
        ('Cuenta bancaria', CuentaBancaria.objects.filter(empresa=empresa, activa=True).exclude(cuenta_contable='').exists(), 'Asociar banco, moneda y cuenta contable.'),
        ('Roles por empresa', Membresia.objects.filter(empresa=empresa).exists(), 'Crear membresías para evitar roles globales compartidos.'),
        ('Correo de recuperación', bool(settings.EMAIL_HOST), 'Configurar SMTP y probar entrega.'),
        ('Contabilización de inventario', bool(perfil and perfil.contabilizar_inventario), 'Activar en Perfil de empresa tras revisar reglas y saldos iniciales.'),
        ('Modo producción', not settings.DEBUG, 'DEBUG debe estar desactivado en el servidor público.'),
    ]
    return controles, [(s.nombre, diagnostico(s)) for s in ServicioExterno.objects.filter(empresa=empresa)]
