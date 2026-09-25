"""Lista verificable de configuración comercial por empresa."""
from django.conf import settings

from apps.contabilidad.models import ReglaContable
from apps.integraciones.configuracion import diagnostico
from apps.integraciones.models import ServicioExterno
from apps.tesoreria.models import CuentaBancaria
from .models import Membresia, PerfilEmpresa


def _fila(nombre, listo, detalle, grupo="Operación"):
    return {"nombre": nombre, "listo": listo, "detalle": detalle, "grupo": grupo}


def revisar(empresa):
    reglas = set(ReglaContable.objects.filter(empresa=empresa).values_list("tipo_documento", flat=True))
    necesarias = {"venta_factura", "venta_igv", "cobro", "compra", "compra_igv", "ganancia_cambio", "perdida_cambio"}
    perfil = PerfilEmpresa.objects.filter(empresa=empresa).first()
    if perfil and perfil.contabilizar_inventario:
        necesarias.update({"costo_venta", "ajuste_inventario"})
    faltantes = sorted(necesarias - reglas)

    servicios = ServicioExterno.objects.filter(empresa=empresa)
    servicios_reales = servicios.filter(activo=True, modo_simulado=False)
    banco_activo = CuentaBancaria.objects.filter(empresa=empresa, activa=True).exclude(cuenta_contable="").exists()
    miembros = Membresia.objects.filter(empresa=empresa).exists()

    controles = [
        _fila("Reglas contables", not faltantes, "Faltan: " + ", ".join(faltantes) if faltantes else "Configuradas; deben ser validadas por el responsable contable.", "Contabilidad"),
        _fila("Cuenta bancaria", banco_activo, "Asociar banco, moneda y cuenta contable.", "Tesorería"),
        _fila("Roles por empresa", miembros, "Crear membresías para evitar permisos globales compartidos.", "Seguridad"),
        _fila("Correo de recuperación", bool(settings.EMAIL_HOST), "Configurar SMTP transaccional y probar entrega real.", "Seguridad"),
        _fila("Contabilización de inventario", bool(perfil and perfil.contabilizar_inventario), "Activar en Perfil de empresa tras revisar reglas y saldos iniciales.", "Inventario"),
        _fila("Modo producción", not settings.DEBUG, "DEBUG debe estar desactivado en el servidor público.", "Infraestructura"),
        _fila("Base de datos PostgreSQL", settings.DATABASES["default"]["ENGINE"].endswith("postgresql"), "Usar PostgreSQL administrado para datos reales; SQLite queda solo para desarrollo.", "Infraestructura"),
        _fila("Archivos persistentes", not getattr(settings, "DEFAULT_FILE_STORAGE", "django.core.files.storage.FileSystemStorage").endswith("FileSystemStorage"), "Mover XML, CDR, PDF y adjuntos a storage externo antes de usar clientes reales.", "Infraestructura"),
        _fila("Servicios reales", servicios_reales.exists(), "Registrar OSE, pasarela, correo, WhatsApp o bancos con adaptadores reales por cliente.", "Integraciones"),
    ]

    fases = [
        dict(nombre="Piloto confiable", listo=all(c["listo"] for c in controles if c["grupo"] in {"Contabilidad", "Tesorería", "Seguridad"}), detalle="Debe cerrar una compra, venta, entrega, factura, cobro y asiento conciliado con datos de una empresa real."),
        dict(nombre="Producción básica", listo=not settings.DEBUG and banco_activo and servicios_reales.exists(), detalle="Requiere dominio, PostgreSQL, worker permanente, correo transaccional y respaldos probados."),
        dict(nombre="Venta repetible", listo=miembros and bool(perfil), detalle="Faltan planes comerciales, proceso de onboarding, contratos, soporte y métricas de valor por rubro."),
        dict(nombre="Empresa grande", listo=False, detalle="Agregar SSO/MFA avanzado, auditoría inmutable, SLA, pruebas de carga, integraciones certificadas y reportes financieros completos."),
    ]
    conexiones = [(s.nombre, diagnostico(s)) for s in servicios]
    return controles, conexiones, fases

