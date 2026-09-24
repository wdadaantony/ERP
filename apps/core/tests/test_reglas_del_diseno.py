"""Pruebas de las reglas que el diseño declara innegociables.

Cada prueba corresponde a un punto concreto del documento de arquitectura:
la máquina de estados, el stock como suma de movimientos, el correlativo que no
se salta, la idempotencia de los envíos y el reintento escalonado.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.catalogo.models import Producto, UnidadMedida
from apps.core.models import Empresa, RegistroAuditoria, Serie
from apps.integraciones.conectores import obtener_conector
from apps.integraciones.models import RegistroIntegracion, ServicioExterno, TrabajoIntegracion
from apps.inventario.models import (
    Almacen,
    MovimientoStock,
    ReservaStock,
    Ubicacion,
    stock_disponible,
    stock_en_mano,
)
from apps.terceros.models import Tercero
from apps.ventas.models import EstadoPedido, LineaPedido, Pedido, TransicionInvalida


class BaseERP(TestCase):
    """Monta lo mínimo: empresa, almacén, ubicaciones, producto y cliente."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(razon_social="Demo S.A.C.", ruc="20123456789")
        cls.almacen = Almacen.objects.create(
            empresa=cls.empresa, codigo="ALM-01", nombre="Central"
        )
        cls.stock = Ubicacion.objects.create(
            empresa=cls.empresa,
            codigo="ALM/STOCK",
            nombre="Stock",
            tipo=Ubicacion.Tipo.INTERNA,
            almacen=cls.almacen,
        )
        cls.proveedor_virtual = Ubicacion.objects.create(
            empresa=cls.empresa,
            codigo="VIRT/PROV",
            nombre="Proveedores",
            tipo=Ubicacion.Tipo.PROVEEDOR,
        )
        cls.cliente_virtual = Ubicacion.objects.create(
            empresa=cls.empresa,
            codigo="VIRT/CLI",
            nombre="Clientes",
            tipo=Ubicacion.Tipo.CLIENTE,
        )
        unidad = UnidadMedida.objects.create(empresa=cls.empresa, codigo="NIU", nombre="Unidad")
        cls.producto = Producto.objects.create(
            empresa=cls.empresa,
            codigo="P-001",
            nombre="Laptop",
            unidad_medida=unidad,
            precio_lista=Decimal("1000"),
        )
        cls.cliente = Tercero.objects.create(
            empresa=cls.empresa,
            tipo_documento="6",
            numero_documento="20601234567",
            razon_social="Cliente S.A.C.",
        )

    def _ingresar(self, cantidad):
        return MovimientoStock.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            cantidad=Decimal(cantidad),
            origen=self.proveedor_virtual,
            destino=self.stock,
            fecha=timezone.now(),
            documento_origen="OC-0001",
        )

    def _pedido(self, numero="PED-0001"):
        return Pedido.objects.create(
            empresa=self.empresa,
            numero=numero,
            tercero=self.cliente,
            almacen=self.almacen,
            fecha=date.today(),
        )


class EstadosDelPedido(BaseERP):
    def test_recorre_el_camino_feliz_completo(self):
        pedido = self._pedido()
        camino = [
            EstadoPedido.ENVIADA,
            EstadoPedido.CONFIRMADO,
            EstadoPedido.RESERVADO,
            EstadoPedido.ENTREGADO,
            EstadoPedido.FACTURADO,
            EstadoPedido.PAGADO,
            EstadoPedido.CERRADO,
        ]
        for estado in camino:
            pedido.transicionar(estado)
        self.assertEqual(pedido.estado, EstadoPedido.CERRADO)

    def test_no_permite_saltarse_estados(self):
        pedido = self._pedido()
        with self.assertRaises(TransicionInvalida):
            pedido.transicionar(EstadoPedido.FACTURADO)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.BORRADOR)

    def test_un_pedido_cerrado_es_final(self):
        pedido = self._pedido()
        for estado in (
            EstadoPedido.CONFIRMADO,
            EstadoPedido.RESERVADO,
            EstadoPedido.ENTREGADO,
            EstadoPedido.FACTURADO,
            EstadoPedido.PAGADO,
            EstadoPedido.CERRADO,
        ):
            pedido.transicionar(estado)
        with self.assertRaises(TransicionInvalida):
            pedido.transicionar(EstadoPedido.CANCELADO)

    def test_sin_stock_el_pedido_queda_en_espera_y_luego_avanza(self):
        pedido = self._pedido()
        pedido.transicionar(EstadoPedido.CONFIRMADO)
        pedido.transicionar(EstadoPedido.EN_ESPERA)
        # Llega la compra y ya se puede reservar.
        pedido.transicionar(EstadoPedido.RESERVADO)
        self.assertEqual(pedido.estado, EstadoPedido.RESERVADO)

    def test_cada_transicion_deja_rastro_en_la_auditoria(self):
        pedido = self._pedido()
        pedido.transicionar(EstadoPedido.ENVIADA)
        registro = RegistroAuditoria.objects.get(
            modelo="ventas.Pedido", objeto_id=str(pedido.pk)
        )
        self.assertEqual(registro.valores_antes["estado"], EstadoPedido.BORRADOR)
        self.assertEqual(registro.valores_despues["estado"], EstadoPedido.ENVIADA)


class TotalesDelPedido(BaseERP):
    def test_calcula_subtotal_igv_y_total_con_descuento(self):
        pedido = self._pedido()
        LineaPedido.objects.create(
            empresa=self.empresa,
            pedido=pedido,
            producto=self.producto,
            cantidad=Decimal("2"),
            precio_unitario=Decimal("1000"),
            descuento_pct=Decimal("10"),
        )
        pedido.recalcular_totales()
        self.assertEqual(pedido.subtotal, Decimal("1800.00"))
        self.assertEqual(pedido.impuestos, Decimal("324.00"))
        self.assertEqual(pedido.total, Decimal("2124.00"))


class StockComoSumaDeMovimientos(BaseERP):
    def test_el_stock_es_entradas_menos_salidas(self):
        self._ingresar(20)
        MovimientoStock.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            cantidad=Decimal("3"),
            origen=self.stock,
            destino=self.cliente_virtual,
            fecha=timezone.now(),
            documento_origen="PED-0001",
        )
        self.assertEqual(stock_en_mano(self.producto, empresa=self.empresa), Decimal("17"))

    def test_lo_reservado_no_cuenta_como_disponible(self):
        self._ingresar(20)
        ReservaStock.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            ubicacion=self.stock,
            cantidad=Decimal("5"),
            documento_origen="PED-0001",
        )
        self.assertEqual(stock_en_mano(self.producto, empresa=self.empresa), Decimal("20"))
        self.assertEqual(stock_disponible(self.producto, empresa=self.empresa), Decimal("15"))

    def test_una_reserva_liberada_vuelve_a_estar_disponible(self):
        self._ingresar(10)
        reserva = ReservaStock.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            ubicacion=self.stock,
            cantidad=Decimal("4"),
        )
        reserva.estado = ReservaStock.Estado.LIBERADA
        reserva.save()
        self.assertEqual(stock_disponible(self.producto, empresa=self.empresa), Decimal("10"))

    def test_dos_vendedores_no_pueden_comprometer_la_misma_unidad(self):
        """El caso «dos vendedores venden el último producto» del diseño."""
        self._ingresar(1)
        ReservaStock.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            ubicacion=self.stock,
            cantidad=Decimal("1"),
            documento_origen="PED-0001",
        )
        # El segundo pedido ya no encuentra nada disponible: va a «en espera».
        self.assertEqual(stock_disponible(self.producto, empresa=self.empresa), Decimal("0"))


class CorrelativosDeSerie(BaseERP):
    def test_el_correlativo_avanza_de_uno_en_uno(self):
        serie = Serie.objects.create(empresa=self.empresa, tipo_documento="01", serie="F001")
        numeros = [serie.siguiente_numero() for _ in range(3)]
        self.assertEqual(numeros, [1, 2, 3])

    def test_dos_instancias_de_la_misma_serie_no_repiten_numero(self):
        Serie.objects.create(empresa=self.empresa, tipo_documento="01", serie="F001")
        primera = Serie.objects.get(serie="F001")
        segunda = Serie.objects.get(serie="F001")
        self.assertNotEqual(primera.siguiente_numero(), segunda.siguiente_numero())


class CapaDeConectores(BaseERP):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.servicio = ServicioExterno.objects.create(
            empresa=cls.empresa,
            codigo=ServicioExterno.Codigo.OSE,
            nombre="OSE de pruebas",
            modo_simulado=True,
            secreto_webhook="secreto",
        )

    def test_el_conector_simulado_acepta_el_comprobante_y_deja_bitacora(self):
        conector = obtener_conector(self.servicio)
        respuesta = conector.ejecutar(
            "emitir_comprobante",
            {"numero_completo": "F001-00000001", "tipo_nombre": "factura"},
            objeto_id="1",
            entidad="facturacion.Comprobante",
        )
        self.assertTrue(respuesta.exito)
        self.assertTrue(respuesta.id_externo)
        registro = RegistroIntegracion.objects.get(objeto_id="1")
        self.assertEqual(registro.estado, RegistroIntegracion.Estado.EXITO)
        self.assertEqual(registro.contenido_recibido["estado"], "aceptado")

    def test_encolar_dos_veces_la_misma_llave_no_duplica_el_trabajo(self):
        """La llave de idempotencia: «el mismo pedido se envía dos veces»."""
        conector = obtener_conector(self.servicio)
        carga = {"numero_completo": "F001-00000002"}
        primero = conector.encolar("emitir_comprobante", carga, llave="cpe:F001-00000002")
        segundo = conector.encolar("emitir_comprobante", carga, llave="cpe:F001-00000002")
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(TrabajoIntegracion.objects.count(), 1)

    def test_los_reintentos_siguen_1_5_y_15_minutos_y_luego_fallan(self):
        trabajo = TrabajoIntegracion.objects.create(
            empresa=self.empresa,
            servicio=self.servicio,
            operacion="emitir_comprobante",
            llave_idempotencia="cpe:F001-00000003",
        )
        self.assertEqual(
            trabajo.programar_reintento("timeout"), TrabajoIntegracion.Estado.REINTENTAR
        )
        self.assertEqual(
            trabajo.programar_reintento("timeout"), TrabajoIntegracion.Estado.REINTENTAR
        )
        # Al tercer fallo pasa a la cola de errores, como dice el diseño.
        self.assertEqual(trabajo.programar_reintento("timeout"), TrabajoIntegracion.Estado.FALLIDO)
        self.assertEqual(trabajo.intentos, 3)

    def test_valida_la_firma_de_los_webhooks(self):
        import hashlib
        import hmac

        conector = obtener_conector(self.servicio)
        cuerpo = b'{"estado":"aceptado"}'
        firma = hmac.new(b"secreto", cuerpo, hashlib.sha256).hexdigest()
        self.assertTrue(conector.validar_firma(cuerpo, firma))
        self.assertFalse(conector.validar_firma(cuerpo, "firma-inventada"))
