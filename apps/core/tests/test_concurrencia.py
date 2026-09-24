"""Conexiones reales paralelas. SQLite requiere ERP_TEST_DB (archivo dedicado)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import skipIf

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from apps.catalogo.models import Producto
from apps.core.models import Empresa, Serie
from apps.facturacion.models import Comprobante
from apps.facturacion.servicios import aplicar_respuesta, emitir_comprobante
from apps.inventario.models import Almacen, MovimientoStock, stock_disponible, stock_en_mano
from apps.inventario.servicios import despachar_pedido
from apps.terceros.models import Tercero
from apps.tesoreria.models import Movimiento
from apps.tesoreria.servicios import registrar_cobro
from apps.ventas.models import Pedido
from apps.ventas.servicios import confirmar_pedido, crear_pedido


@skipIf(connection.vendor == "sqlite" and not connection.settings_dict.get("TEST", {}).get("NAME"),
        "Ejecutar con ERP_TEST_DB apuntando a un archivo de pruebas exclusivo.")
class ConcurrenciaReal(TransactionTestCase):
    def setUp(self):
        call_command("sembrar_demo", stock_inicial=1, verbosity=0)
        self.empresa = Empresa.objects.get(ruc="20123456789")
        self.almacen = Almacen.objects.get(empresa=self.empresa)
        self.producto = Producto.objects.get(empresa=self.empresa, codigo="P-001")
        self.cliente = Tercero.objects.get(empresa=self.empresa, numero_documento="20601234567")

    def pedido(self):
        return crear_pedido(self.empresa, self.cliente, self.almacen,
                            [{"producto": self.producto, "cantidad": 1}])

    def paralelo(self, modelo, ids, accion):
        barrera = Barrier(2)

        def ejecutar(pk):
            close_old_connections()
            try:
                obj = modelo.objects.get(pk=pk)
                barrera.wait(timeout=15)
                try:
                    return accion(obj)
                except ValidationError:
                    return "rechazado"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(ejecutar, ids, timeout=60))
        return resultados

    def factura(self):
        p = self.pedido()
        confirmar_pedido(p)
        despachar_pedido(p)
        c, _ = emitir_comprobante(p)
        aplicar_respuesta(c, {"estado": "aceptado"})
        return c

    def test_dos_reservas_por_la_ultima_unidad(self):
        p1, p2 = self.pedido(), self.pedido()
        r = self.paralelo(Pedido, [p1.pk, p2.pk], lambda p: confirmar_pedido(p).estado)
        self.assertCountEqual(r, ["reservado", "en_espera"])
        self.assertEqual(stock_disponible(self.producto, empresa=self.empresa), 0)

    def test_dos_cobros_no_exceden_saldo(self):
        c = self.factura()
        r = self.paralelo(Comprobante, [c.pk, c.pk], lambda c: registrar_cobro(c, c.total).pk)
        self.assertEqual(r.count("rechazado"), 1)
        c.refresh_from_db()
        self.assertEqual(c.total_cobrado, c.total)
        self.assertEqual(c.cobros.count(), 1)

    def test_referencia_simultanea_crea_un_solo_cobro(self):
        c = self.factura()
        r = self.paralelo(Comprobante, [c.pk, c.pk],
                          lambda c: registrar_cobro(c, c.total, referencia_externa="MISMO-PAGO").pk)
        self.assertEqual(r[0], r[1])
        self.assertNotIn("rechazado", r)
        self.assertEqual(c.cobros.count(), 1)

    def test_dos_despachos_no_duplican_salida(self):
        p = self.pedido()
        confirmar_pedido(p)
        r = self.paralelo(Pedido, [p.pk, p.pk], lambda p: len(despachar_pedido(p)))
        self.assertCountEqual(r, [1, "rechazado"])
        self.assertEqual(stock_en_mano(self.producto, empresa=self.empresa), 0)
        p.refresh_from_db()
        self.assertEqual(p.estado, "entregado")

    def test_series_devuelven_numeros_distintos(self):
        serie = Serie.objects.get(empresa=self.empresa, tipo_documento="01")
        r = self.paralelo(Serie, [serie.pk, serie.pk], lambda s: [s.siguiente_numero() for _ in range(10)])
        self.assertEqual(sorted(r[0] + r[1]), list(range(1, 21)))

    def test_facturacion_simultanea_no_duplica_documento(self):
        p = self.pedido()
        confirmar_pedido(p)
        despachar_pedido(p)
        r = self.paralelo(Pedido, [p.pk, p.pk], lambda p: emitir_comprobante(p)[0].pk)
        self.assertEqual(r.count("rechazado"), 1)
        self.assertEqual(p.comprobantes.count(), 1)
