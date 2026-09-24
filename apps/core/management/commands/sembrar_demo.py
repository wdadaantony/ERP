"""Carga una empresa de ejemplo con todo lo mínimo para operar.

    python manage.py sembrar_demo

Deja lista una empresa, su plan de cuentas básico, almacén con ubicaciones,
productos, clientes, series de comprobantes y el servicio de OSE en modo simulado.
"""
import os
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.catalogo.models import AfectacionIGV, ListaPrecios, PrecioProducto, Producto, UnidadMedida
from apps.contabilidad.models import CuentaContable, ReglaContable
from apps.core.models import Empresa, Serie, Usuario
from apps.integraciones.models import ServicioExterno
from apps.inventario.models import Almacen, MovimientoStock, Ubicacion
from apps.terceros.models import CondicionPago, Tercero
from apps.tesoreria.models import CuentaBancaria

CUENTAS = [
    ("676", "Diferencias de cambio (demostración)", "deudora", True),
    ("776", "Diferencias de cambio (demostración)", "acreedora", True),
    ("639", "Comisiones de cobro (demostración)", "deudora", True),
    ("10", "Efectivo y equivalentes de efectivo", "deudora", False),
    ("1041", "Cuentas corrientes operativas", "deudora", True),
    ("12", "Cuentas por cobrar comerciales", "deudora", False),
    ("1212", "Facturas por cobrar - emitidas", "deudora", True),
    ("20", "Mercaderías", "deudora", False),
    ("2011", "Mercaderías manufacturadas", "deudora", True),
    ("40", "Tributos por pagar", "acreedora", False),
    ("4011", "IGV - cuenta propia", "acreedora", True),
    ("42", "Cuentas por pagar comerciales", "acreedora", False),
    ("4212", "Facturas por pagar - emitidas", "acreedora", True),
    ("69", "Costo de ventas", "deudora", False),
    ("6911", "Mercaderías manufacturadas", "deudora", True),
    ("70", "Ventas", "acreedora", False),
    ("7011", "Mercaderías manufacturadas", "acreedora", True),
]

UNIDADES = [("NIU", "Unidad"), ("ZZ", "Servicio"), ("KGM", "Kilogramo"), ("MTR", "Metro")]

PRODUCTOS = [
    ("P-001", "Laptop 14 pulgadas", "bien", "NIU", "3200.00", "2400.00", True),
    ("P-002", "Mouse inalámbrico", "bien", "NIU", "89.90", "45.00", True),
    ("P-003", "Monitor 24 pulgadas", "bien", "NIU", "749.00", "520.00", True),
    ("S-001", "Instalación y configuración", "servicio", "ZZ", "250.00", "0.00", False),
    ("S-002", "Soporte técnico mensual", "servicio", "ZZ", "400.00", "0.00", False),
]

CLIENTES = [
    ("6", "20601234567", "Comercial Andina S.A.C.", True, False),
    ("6", "20509876543", "Distribuidora del Sur E.I.R.L.", True, False),
    ("1", "45678912", "Rosa Quispe Mamani", True, False),
    ("6", "20112233445", "Importaciones Tech S.A.", False, True),
]


class Command(BaseCommand):
    help = "Crea una empresa de demostración con datos maestros listos para operar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ruc", default="20123456789", help="RUC de la empresa de demostración"
        )
        parser.add_argument(
            "--stock-inicial",
            type=int,
            default=20,
            help="Unidades de stock inicial por producto físico",
        )

    @transaction.atomic
    def handle(self, *args, **opciones):
        if not opciones.get("verbosity", 1):
            # Con --verbosity 0 el comando trabaja en silencio (útil en pruebas).
            self.stdout = open(os.devnull, "w")

        empresa, creada = Empresa.objects.get_or_create(
            ruc=opciones["ruc"],
            defaults={
                "razon_social": "Empresa Demo S.A.C.",
                "nombre_comercial": "Demo",
                "direccion_fiscal": "Av. Javier Prado Este 123, San Isidro, Lima",
                "moneda_base": "PEN",
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f"Empresa {'creada' if creada else 'reutilizada'}: {empresa}")
        )

        self._sembrar_cuentas(empresa)
        unidades = self._sembrar_unidades(empresa)
        self._sembrar_series(empresa)
        almacen, ubicaciones = self._sembrar_almacen(empresa)
        productos = self._sembrar_productos(empresa, unidades)
        self._sembrar_stock_inicial(empresa, productos, ubicaciones, opciones["stock_inicial"])
        self._sembrar_terceros(empresa)
        self._sembrar_tesoreria(empresa)
        self._sembrar_servicios(empresa)
        self._asociar_usuarios(empresa)

        self.stdout.write(self.style.SUCCESS("Listo. Datos de demostración cargados."))
        self.stdout.write("Crea un usuario si aún no tienes: python manage.py createsuperuser")

    # -- Piezas -----------------------------------------------------------

    def _sembrar_cuentas(self, empresa):
        creadas = 0
        for codigo, nombre, naturaleza, movimiento in CUENTAS:
            _, nueva = CuentaContable.objects.get_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "nombre": nombre,
                    "naturaleza": naturaleza,
                    "acepta_movimiento": movimiento,
                },
            )
            creadas += nueva
        # Reglas que hacen automático el asiento de venta y el de cobro.
        cuentas = {c.codigo: c for c in CuentaContable.objects.filter(empresa=empresa)}
        for tipo, debe, haber in (
            ("venta_factura", "1212", "7011"),
            ("venta_igv", "1212", "4011"),
            ("cobro", "1041", "1212"),
            ("ganancia_cambio", "1041", "776"),
            ("perdida_cambio", "676", "1212"),
            ("comision_cobro", "639", "1041"),
            ("compra", "2011", "4212"),
        ):
            ReglaContable.objects.get_or_create(
                empresa=empresa,
                tipo_documento=tipo,
                defaults={"cuenta_debe": cuentas[debe], "cuenta_haber": cuentas[haber]},
            )
        self.stdout.write(f"  Plan contable: {creadas} cuentas nuevas, 7 reglas contables")

    def _sembrar_unidades(self, empresa):
        unidades = {}
        for codigo, nombre in UNIDADES:
            unidad, _ = UnidadMedida.objects.get_or_create(
                empresa=empresa, codigo=codigo, defaults={"nombre": nombre}
            )
            unidades[codigo] = unidad
        self.stdout.write(f"  Unidades de medida: {len(unidades)}")
        return unidades

    def _sembrar_series(self, empresa):
        for tipo, serie in (("01", "F001"), ("03", "B001"), ("07", "FC01"), ("08", "FD01"), ("09", "T001")):
            Serie.objects.get_or_create(empresa=empresa, tipo_documento=tipo, serie=serie)
        self.stdout.write("  Series de comprobantes: F001, B001, FC01, FD01, T001")

    def _sembrar_almacen(self, empresa):
        almacen, _ = Almacen.objects.get_or_create(
            empresa=empresa,
            codigo="ALM-01",
            defaults={
                "nombre": "Almacén central",
                "direccion": "Av. Argentina 4500, Callao",
            },
        )
        ubicaciones = {}
        definiciones = [
            ("ALM-01/STOCK", "Zona de stock", Ubicacion.Tipo.INTERNA, almacen),
            ("VIRT/PROVEEDOR", "Proveedores", Ubicacion.Tipo.PROVEEDOR, None),
            ("VIRT/CLIENTE", "Clientes", Ubicacion.Tipo.CLIENTE, None),
            ("VIRT/AJUSTE", "Ajustes de inventario", Ubicacion.Tipo.AJUSTE, None),
        ]
        for codigo, nombre, tipo, alm in definiciones:
            ubicacion, _ = Ubicacion.objects.get_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={"nombre": nombre, "tipo": tipo, "almacen": alm},
            )
            ubicaciones[codigo] = ubicacion
        self.stdout.write(f"  Almacén {almacen.codigo} con {len(ubicaciones)} ubicaciones")
        return almacen, ubicaciones

    def _sembrar_productos(self, empresa, unidades):
        lista, _ = ListaPrecios.objects.get_or_create(
            empresa=empresa, nombre="Lista general", defaults={"moneda": "PEN"}
        )
        productos = []
        for codigo, nombre, tipo, unidad, precio, costo, stock in PRODUCTOS:
            producto, _ = Producto.objects.get_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "nombre": nombre,
                    "tipo": tipo,
                    "unidad_medida": unidades[unidad],
                    "precio_lista": Decimal(precio),
                    "costo_promedio": Decimal(costo),
                    "controla_stock": stock,
                    "afectacion_igv": AfectacionIGV.GRAVADO,
                },
            )
            PrecioProducto.objects.get_or_create(
                empresa=empresa,
                lista=lista,
                producto=producto,
                cantidad_minima=Decimal("1"),
                defaults={"precio": Decimal(precio)},
            )
            productos.append(producto)
        self.stdout.write(f"  Productos: {len(productos)}")
        return productos

    def _sembrar_stock_inicial(self, empresa, productos, ubicaciones, cantidad):
        origen = ubicaciones["VIRT/AJUSTE"]
        destino = ubicaciones["ALM-01/STOCK"]
        creados = 0
        for producto in productos:
            if not producto.controla_stock:
                continue
            if MovimientoStock.objects.filter(
                empresa=empresa, producto=producto, documento_origen="INV-INICIAL"
            ).exists():
                continue
            MovimientoStock.objects.create(
                empresa=empresa,
                producto=producto,
                cantidad=Decimal(cantidad),
                origen=origen,
                destino=destino,
                costo_unitario=producto.costo_promedio,
                fecha=timezone.now(),
                documento_origen="INV-INICIAL",
                documento_tipo="inventario.CargaInicial",
            )
            creados += 1
        self.stdout.write(f"  Stock inicial: {creados} productos con {cantidad} unidades")

    def _sembrar_terceros(self, empresa):
        contado, _ = CondicionPago.objects.get_or_create(
            empresa=empresa, nombre="Contado", defaults={"dias": 0}
        )
        CondicionPago.objects.get_or_create(
            empresa=empresa, nombre="Crédito 30 días", defaults={"dias": 30}
        )
        for tipo_doc, numero, razon, es_cliente, es_proveedor in CLIENTES:
            Tercero.objects.get_or_create(
                empresa=empresa,
                tipo_documento=tipo_doc,
                numero_documento=numero,
                defaults={
                    "razon_social": razon,
                    "es_cliente": es_cliente,
                    "es_proveedor": es_proveedor,
                    "condicion_pago": contado,
                },
            )
        self.stdout.write(f"  Terceros: {len(CLIENTES)}")

    def _sembrar_tesoreria(self, empresa):
        CuentaBancaria.objects.get_or_create(
            empresa=empresa,
            numero="194-1234567-0-89",
            defaults={
                "banco": "BCP",
                "cci": "00219400123456708912",
                "moneda": "PEN",
                "cuenta_contable": "1041",
            },
        )
        self.stdout.write("  Cuenta bancaria: BCP soles")

    def _sembrar_servicios(self, empresa):
        """Los conectores existen desde el primer día, aunque estén simulados."""
        definiciones = [
            (ServicioExterno.Codigo.OSE, "OSE de pruebas", "simulado"),
            (ServicioExterno.Codigo.PASARELA, "Pasarela de pruebas", "simulado"),
            (ServicioExterno.Codigo.CRM, "CRM de marketing", "simulado"),
            (ServicioExterno.Codigo.WHATSAPP, "WhatsApp Business", "Meta Cloud API"),
            (ServicioExterno.Codigo.ADS, "Meta Ads", "Meta"),
            (ServicioExterno.Codigo.AUTOMATIZACION, "Automatizaciones", "n8n, Make o Zapier"),
        ]
        for codigo, nombre, proveedor in definiciones:
            ServicioExterno.objects.get_or_create(
                empresa=empresa,
                codigo=codigo,
                nombre=nombre,
                defaults={
                    "proveedor": proveedor,
                    "modo_simulado": True,
                    "secreto_webhook": "secreto-de-pruebas",
                },
            )
        self.stdout.write(f"  Servicios externos: {len(definiciones)} (todos en modo simulado)")

    def _asociar_usuarios(self, empresa):
        """A los usuarios existentes sin empresa se les asigna la de demostración."""
        for usuario in Usuario.objects.filter(empresa_actual__isnull=True):
            usuario.empresas.add(empresa)
            usuario.empresa_actual = empresa
            usuario.save(update_fields=["empresa_actual"])
