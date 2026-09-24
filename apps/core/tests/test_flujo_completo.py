"""El proceso completo del diagrama de carriles, de lead a asiento contable.

Estas pruebas recorren el flujo real usando los servicios, no tocando modelos a
mano: confirmar, reservar, despachar, facturar, procesar la cola, cobrar y
contabilizar.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.catalogo.models import Producto
from apps.contabilidad.models import Asiento
from apps.core.models import Empresa, Usuario
from apps.facturacion.models import Comprobante, EstadoComprobante, TipoComprobante
from apps.facturacion.servicios import emitir_comprobante
from apps.integraciones.models import ServicioExterno, TrabajoIntegracion
from apps.integraciones.trabajador import procesar_cola
from apps.inventario.models import Almacen, ReservaStock, stock_disponible, stock_en_mano
from apps.inventario.servicios import despachar_pedido
from apps.terceros.models import Tercero
from apps.tesoreria.models import Movimiento
from apps.tesoreria.servicios import registrar_cobro
from apps.ventas.models import EstadoPedido
from apps.ventas.servicios import (
    cancelar_pedido,
    confirmar_pedido,
    crear_pedido,
    generar_orden_compra,
    reintentar_reserva,
)


class BaseFlujo(TestCase):
    """Usa el mismo comando de siembra que se corre en una instalación real."""

    @classmethod
    def setUpTestData(cls):
        call_command("sembrar_demo", stock_inicial=10, verbosity=0)
        cls.empresa = Empresa.objects.get(ruc="20123456789")
        cls.almacen = Almacen.objects.get(empresa=cls.empresa, codigo="ALM-01")
        cls.laptop = Producto.objects.get(empresa=cls.empresa, codigo="P-001")
        cls.servicio_soporte = Producto.objects.get(empresa=cls.empresa, codigo="S-001")
        cls.cliente = Tercero.objects.get(empresa=cls.empresa, numero_documento="20601234567")
        cls.persona = Tercero.objects.get(empresa=cls.empresa, numero_documento="45678912")
        cls.proveedor = Tercero.objects.get(empresa=cls.empresa, numero_documento="20112233445")
        cls.usuario = Usuario.objects.create_user(
            username="vendedor", password="x", empresa_actual=cls.empresa
        )
        cls.usuario.empresas.add(cls.empresa)

    def _pedido(self, cantidad=2, tercero=None, producto=None):
        return crear_pedido(
            self.empresa,
            tercero or self.cliente,
            self.almacen,
            [{"producto": producto or self.laptop, "cantidad": cantidad}],
            usuario=self.usuario,
        )


class FlujoDeVentaCompleto(BaseFlujo):
    def test_de_pedido_a_asiento_contable(self):
        pedido = self._pedido(cantidad=2)
        self.assertEqual(pedido.total, Decimal("7552.00"))

        # 1. Confirmar reserva el stock.
        confirmar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.RESERVADO)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), Decimal("10"))
        self.assertEqual(stock_disponible(self.laptop, empresa=self.empresa), Decimal("8"))

        # 2. Despachar saca la mercadería y consume la reserva.
        movimientos = despachar_pedido(pedido, self.usuario)
        self.assertEqual(len(movimientos), 1)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), Decimal("8"))
        self.assertFalse(
            ReservaStock.objects.filter(
                linea_pedido__pedido=pedido, estado=ReservaStock.Estado.ACTIVA
            ).exists()
        )
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)

        # 3. Facturar encola: no llama al OSE mientras el usuario espera.
        comprobante, trabajo = emitir_comprobante(pedido, self.usuario)
        self.assertEqual(comprobante.tipo, TipoComprobante.FACTURA)
        self.assertEqual(comprobante.estado, EstadoComprobante.POR_ENVIAR)
        self.assertEqual(comprobante.total, Decimal("7552.00"))
        self.assertEqual(trabajo.estado, TrabajoIntegracion.Estado.EN_COLA)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.FACTURADO)

        # 4. El trabajador vacía la cola y aplica el CDR.
        resumen = procesar_cola(self.empresa)
        self.assertEqual(resumen, {TrabajoIntegracion.Estado.HECHO: 1})
        comprobante.refresh_from_db()
        self.assertEqual(comprobante.estado, EstadoComprobante.ACEPTADO)
        self.assertTrue(comprobante.id_externo)

        # 5. La aceptación genera el asiento de venta, cuadrado.
        asiento = Asiento.objects.get(comprobante=comprobante)
        self.assertTrue(asiento.cuadra)
        self.assertEqual(asiento.total_debe, Decimal("7552.00"))

        # 6. El cobro cierra el círculo: asiento propio y pedido cerrado.
        registrar_cobro(comprobante, comprobante.total, referencia_externa="pasarela-001")
        comprobante.refresh_from_db()
        pedido.refresh_from_db()
        self.assertTrue(comprobante.esta_pagado)
        self.assertEqual(pedido.estado, EstadoPedido.CERRADO)
        self.assertEqual(Asiento.objects.filter(empresa=self.empresa).count(), 2)

    def test_a_una_persona_natural_se_le_emite_boleta(self):
        pedido = self._pedido(cantidad=1, tercero=self.persona)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        self.assertEqual(comprobante.tipo, TipoComprobante.BOLETA)
        self.assertTrue(comprobante.numero_completo.startswith("B001-"))

    def test_un_servicio_no_mueve_stock_pero_si_se_factura(self):
        pedido = self._pedido(cantidad=1, producto=self.servicio_soporte)
        confirmar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.RESERVADO)
        self.assertEqual(despachar_pedido(pedido, self.usuario), [])
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        self.assertEqual(comprobante.total, Decimal("295.00"))


class CuandoFaltaStock(BaseFlujo):
    def test_el_pedido_queda_en_espera_y_se_genera_la_orden_de_compra(self):
        pedido = self._pedido(cantidad=25)  # solo hay 10
        confirmar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.EN_ESPERA)
        # No se quedó con reservas parciales bloqueando a otros.
        self.assertEqual(stock_disponible(self.laptop, empresa=self.empresa), Decimal("10"))

        orden = generar_orden_compra(pedido, self.proveedor, self.usuario)
        self.assertEqual(orden.lineas.count(), 1)
        self.assertEqual(orden.lineas.first().cantidad, Decimal("15"))
        self.assertEqual(orden.pedido_origen, pedido)

    def test_al_llegar_la_mercaderia_el_pedido_vuelve_a_avanzar(self):
        from datetime import date

        from apps.compras.models import LineaRecepcion, Recepcion
        from apps.inventario.servicios import ingresar_recepcion

        pedido = self._pedido(cantidad=25)
        confirmar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.EN_ESPERA)

        recepcion = Recepcion.objects.create(
            empresa=self.empresa, numero="REC-0001", almacen=self.almacen, fecha=date.today()
        )
        LineaRecepcion.objects.create(
            empresa=self.empresa,
            recepcion=recepcion,
            producto=self.laptop,
            cantidad=Decimal("20"),
            costo_unitario=Decimal("2500"),
        )
        ingresar_recepcion(recepcion, self.usuario)

        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), Decimal("30"))
        reintentar_reserva(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.RESERVADO)

    def test_el_ingreso_recalcula_el_costo_promedio_ponderado(self):
        from datetime import date

        from apps.compras.models import LineaRecepcion, Recepcion
        from apps.inventario.servicios import ingresar_recepcion

        # Hay 10 a 2400. Entran 10 a 2600 → promedio 2500.
        recepcion = Recepcion.objects.create(
            empresa=self.empresa, numero="REC-0002", almacen=self.almacen, fecha=date.today()
        )
        LineaRecepcion.objects.create(
            empresa=self.empresa,
            recepcion=recepcion,
            producto=self.laptop,
            cantidad=Decimal("10"),
            costo_unitario=Decimal("2600"),
        )
        ingresar_recepcion(recepcion)
        self.laptop.refresh_from_db()
        self.assertEqual(self.laptop.costo_promedio, Decimal("2500.0000"))


class CancelacionYDuplicados(BaseFlujo):
    def test_cancelar_libera_lo_reservado(self):
        pedido = self._pedido(cantidad=4)
        confirmar_pedido(pedido, self.usuario)
        self.assertEqual(stock_disponible(self.laptop, empresa=self.empresa), Decimal("6"))

        cancelar_pedido(pedido, self.usuario, motivo="El cliente se arrepintió")
        self.assertEqual(pedido.estado, EstadoPedido.CANCELADO)
        self.assertEqual(stock_disponible(self.laptop, empresa=self.empresa), Decimal("10"))

    def test_no_se_factura_dos_veces_el_mismo_pedido(self):
        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        emitir_comprobante(pedido, self.usuario)
        with self.assertRaises(ValidationError):
            emitir_comprobante(pedido, self.usuario)

    def test_el_mismo_aviso_de_cobro_dos_veces_no_duplica_el_movimiento(self):
        """El caso «el pago se aprueba pero el aviso llega dos veces»."""
        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        procesar_cola(self.empresa)
        comprobante.refresh_from_db()

        primero = registrar_cobro(comprobante, comprobante.total, referencia_externa="pg-99")
        segundo = registrar_cobro(comprobante, comprobante.total, referencia_externa="pg-99")
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(Movimiento.objects.filter(comprobante=comprobante).count(), 1)

    def test_no_se_puede_cobrar_mas_que_el_saldo(self):
        from apps.tesoreria.servicios import CobroExcedeSaldo

        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        procesar_cola(self.empresa)
        comprobante.refresh_from_db()

        with self.assertRaises(CobroExcedeSaldo):
            registrar_cobro(comprobante, comprobante.total + Decimal("1"))

    def test_admite_cobros_parciales(self):
        pedido = self._pedido(cantidad=2)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        procesar_cola(self.empresa)
        comprobante.refresh_from_db()

        mitad = (comprobante.total / 2).quantize(Decimal("0.01"))
        registrar_cobro(comprobante, mitad, referencia_externa="pg-1")
        comprobante.refresh_from_db()
        self.assertFalse(comprobante.esta_pagado)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, EstadoPedido.FACTURADO)

        registrar_cobro(comprobante, comprobante.saldo, referencia_externa="pg-2")
        comprobante.refresh_from_db()
        pedido.refresh_from_db()
        self.assertTrue(comprobante.esta_pagado)
        self.assertEqual(pedido.estado, EstadoPedido.CERRADO)


class ColaDeIntegraciones(BaseFlujo):
    def test_un_servicio_caido_manda_el_trabajo_a_la_cola_de_errores(self):
        """Simula el «servicio externo caído» del punto 7 del diseño."""
        from unittest.mock import patch

        from apps.integraciones.conectores import ErrorConector
        from apps.integraciones.conectores.ose import ConectorOSE

        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        _, trabajo = emitir_comprobante(pedido, self.usuario)

        caido = ErrorConector("502 Bad Gateway", codigo_http=502, recuperable=True)
        with patch.object(ConectorOSE, "_ejecutar", side_effect=caido):
            for _ in range(3):
                # Adelantamos el reloj del reintento para no esperar 1, 5 y 15 minutos.
                trabajo.refresh_from_db()
                trabajo.ejecutar_despues_de = timezone.now()
                trabajo.save(update_fields=["ejecutar_despues_de"])
                procesar_cola(self.empresa)

        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, TrabajoIntegracion.Estado.FALLIDO)
        self.assertEqual(trabajo.intentos, 3)
        self.assertIn("502", trabajo.ultimo_error)

    def test_el_comprobante_rechazado_conserva_su_correlativo(self):
        """SUNAT rechaza: el número no se pierde y queda el motivo a la vista."""
        from apps.facturacion.servicios import aplicar_respuesta

        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        numero = comprobante.numero_completo

        aplicar_respuesta(
            comprobante,
            {
                "estado": "rechazado",
                "codigo_respuesta": "2335",
                "mensaje": "El dato ingresado en el tipo de documento del receptor no es válido",
            },
        )
        comprobante.refresh_from_db()
        self.assertEqual(comprobante.estado, EstadoComprobante.RECHAZADO)
        self.assertEqual(comprobante.numero_completo, numero)
        self.assertIn("receptor", comprobante.mensaje_respuesta)
        # Un rechazo no genera asiento.
        self.assertFalse(Asiento.objects.filter(comprobante=comprobante).exists())

    def test_los_registros_de_integracion_documentan_cada_envio(self):
        from apps.integraciones.models import RegistroIntegracion

        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        procesar_cola(self.empresa)

        registro = RegistroIntegracion.objects.get(objeto_id=str(comprobante.pk))
        self.assertEqual(registro.estado, RegistroIntegracion.Estado.EXITO)
        self.assertEqual(registro.contenido_enviado["numero_completo"], comprobante.numero_completo)
        self.assertIsNotNone(registro.duracion_ms)


class ConciliacionBancaria(BaseFlujo):
    def test_empareja_el_extracto_con_los_cobros_por_referencia(self):
        from datetime import date

        from apps.tesoreria.models import CuentaBancaria, LineaExtractoBancario
        from apps.tesoreria.servicios import conciliar_extracto

        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        procesar_cola(self.empresa)
        comprobante.refresh_from_db()
        cuenta = CuentaBancaria.objects.get(empresa=self.empresa)
        cobro = registrar_cobro(comprobante, comprobante.total, cuenta=cuenta, referencia_externa="OP-4451")
        LineaExtractoBancario.objects.create(
            empresa=self.empresa,
            cuenta=cuenta,
            fecha=date.today(),
            descripcion="Abono en cuenta",
            monto=comprobante.total,
            referencia="OP-4451",
        )
        self.assertEqual(conciliar_extracto(cuenta), 1)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, Movimiento.Estado.CONCILIADO)
