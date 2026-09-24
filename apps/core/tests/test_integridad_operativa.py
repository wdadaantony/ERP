"""Regresiones de los siete pendientes: permisos, dinero, bancos y atomicidad."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse

from apps.core.models import Empresa, Serie, Usuario
from apps.core.permisos import RUTAS, crear_roles
from apps.core.tests.test_flujo_completo import BaseFlujo
from apps.facturacion.models import Comprobante, EstadoComprobante as EC, TipoComprobante as TC
from apps.facturacion.servicios import aplicar_respuesta, emitir_comprobante, emitir_nota_credito
from apps.inventario.models import MovimientoStock, ReservaStock, stock_en_mano
from apps.inventario.servicios import despachar_pedido
from apps.tesoreria.models import CuentaBancaria, LineaExtractoBancario, Movimiento
from apps.tesoreria.servicios import registrar_cobro, confirmar_cobro, anular_cobro, conciliar_extracto
from apps.ventas.models import Pedido, EstadoPedido as EP
from apps.ventas.servicios import confirmar_pedido


class IntegridadOperativa(BaseFlujo):
    def factura(self, moneda="PEN", tasa="1"):
        pedido = self._pedido(cantidad=1)
        pedido.moneda, pedido.tipo_cambio = moneda, Decimal(tasa)
        pedido.save()
        confirmar_pedido(pedido)
        despachar_pedido(pedido)
        c, _ = emitir_comprobante(pedido)
        aplicar_respuesta(c, {"estado": "aceptado"})
        return c

    def test_nota_pendiente_no_reduce_saldo_aceptada_si(self):
        c = self.factura()
        nota, _ = emitir_nota_credito(c, monto="100")
        self.assertEqual(c.saldo, c.total)
        aplicar_respuesta(nota, {"estado": "aceptado"})
        self.assertEqual(c.saldo, c.total - 100)
        self.assertEqual(nota.asientos.count(), 1)
        aplicar_respuesta(nota, {"estado": "aceptado"})
        self.assertEqual(nota.asientos.count(), 1)

    def test_notas_por_tipo_y_estado(self):
        c = self.factura()
        for numero, (tipo, estado, impacto) in enumerate([
            (TC.NOTA_CREDITO, EC.RECHAZADO, 0), (TC.NOTA_CREDITO, EC.ANULADO, 0),
            (TC.NOTA_DEBITO, EC.POR_ENVIAR, 0), (TC.NOTA_DEBITO, EC.ACEPTADO, 100),
            (TC.NOTA_CREDITO, EC.OBSERVADO, -100),
        ], 1):
            Comprobante.objects.create(empresa=self.empresa, tercero=self.cliente,
                tipo=tipo, estado=estado, serie="N001", correlativo=numero,
                fecha_emision=date.today(), total=100, comprobante_afectado=c)
            self.assertEqual(c.saldo, c.total + (100 if numero == 4 else 0))

    def test_reserva_notas_pendientes_contra_sobreacreditacion(self):
        c = self.factura()
        emitir_nota_credito(c, monto=c.total)
        with self.assertRaises(ValidationError):
            emitir_nota_credito(c, monto="0.01")
        self.assertEqual(c.saldo, c.total)

    def test_descripcion_no_puede_reducir_deuda(self):
        with self.assertRaises(ValidationError):
            emitir_nota_credito(self.factura(), motivo="03", monto="100")

    def test_documento_contabilizado_no_retrocede_por_respuesta_tardia(self):
        c = self.factura()
        with self.assertRaises(ValidationError):
            aplicar_respuesta(c, {"estado": "rechazado"})
        c.refresh_from_db()
        self.assertEqual(c.estado, EC.ACEPTADO)

    def test_despacho_revierte_todo_si_falla_estado(self):
        p = self._pedido(cantidad=2)
        confirmar_pedido(p)
        with patch.object(Pedido, "transicionar", side_effect=ValidationError("Fallo de auditoría")):
            with self.assertRaises(ValidationError):
                despachar_pedido(p)
        p.refresh_from_db()
        self.assertEqual(p.estado, EP.RESERVADO)
        self.assertEqual(p.lineas.get().cantidad_entregada, 0)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 10)
        self.assertEqual(p.lineas.get().reservas.get().estado, ReservaStock.Estado.ACTIVA)
        self.assertFalse(MovimientoStock.objects.filter(documento_tipo="ventas.Pedido").exists())

    def test_despacho_con_objeto_obsoleto_no_duplica(self):
        p = self._pedido(cantidad=2)
        confirmar_pedido(p)
        copia = Pedido.objects.get(pk=p.pk)
        despachar_pedido(p)
        with self.assertRaises(ValidationError):
            despachar_pedido(copia)
        self.assertEqual(stock_en_mano(self.laptop, empresa=self.empresa), 8)

    def test_cobros_pendientes_revalidan_saldo_al_confirmar(self):
        c = self.factura()
        primero = registrar_cobro(c, c.total, confirmar=False)
        segundo = registrar_cobro(c, c.total, confirmar=False)
        confirmar_cobro(primero)
        with self.assertRaises(ValidationError):
            confirmar_cobro(segundo)
        c.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(c.total_cobrado, c.total)
        self.assertEqual(segundo.estado, Movimiento.Estado.PENDIENTE)

    def test_anular_pendiente_no_resta_cobros_confirmados(self):
        c = self.factura()
        registrar_cobro(c, 100)
        pendiente = registrar_cobro(c, 20, confirmar=False)
        anular_cobro(pendiente)
        c.refresh_from_db()
        self.assertEqual(c.total_cobrado, 100)

    def test_referencia_no_se_puede_reutilizar_con_otro_importe(self):
        c = self.factura()
        mov = registrar_cobro(c, 100, referencia_externa="UNICA")
        self.assertEqual(registrar_cobro(c, 100, referencia_externa="UNICA").pk, mov.pk)
        with self.assertRaises(ValidationError):
            registrar_cobro(c, 101, referencia_externa="UNICA")

    def test_cobro_extranjero_exige_tasa_positiva(self):
        c = self.factura("USD", "3.5")
        for tasa in (None, 0, -1, "NaN"):
            with self.subTest(tasa=tasa), self.assertRaises(ValidationError):
                registrar_cobro(c, 100, tipo_cambio=tasa)

    def test_ganancia_perdida_y_comision_cuadran(self):
        c = self.factura("USD", "3.5")
        m = registrar_cobro(c, 100, tipo_cambio="3.8", comision=2)
        asiento = m.asientos.get()
        self.assertTrue(asiento.cuadra)
        self.assertEqual(asiento.lineas.get(cuenta__codigo="776").haber, Decimal("30"))
        self.assertEqual(asiento.lineas.get(cuenta__codigo="639").debe, Decimal("7.60"))
        m = registrar_cobro(c, 100, tipo_cambio="3.2")
        self.assertEqual(m.asientos.get().lineas.get(cuenta__codigo="676").debe, Decimal("30"))
        self.assertTrue(m.asientos.get().cuadra)

    def test_regla_faltante_revierte_cobro_y_saldo(self):
        from apps.contabilidad.models import ReglaContable
        c = self.factura("USD", "3.5")
        ReglaContable.objects.filter(empresa=self.empresa, tipo_documento="ganancia_cambio").delete()
        with self.assertRaises(ValidationError):
            registrar_cobro(c, 100, tipo_cambio="3.8")
        c.refresh_from_db()
        self.assertEqual(c.total_cobrado, 0)
        self.assertFalse(c.cobros.exists())

    def test_redondeo_por_linea_coincide_con_factura(self):
        p = self._pedido(cantidad=1)
        linea = p.lineas.get()
        linea.precio_unitario = Decimal("0.025")
        linea.save()
        confirmar_pedido(p)
        despachar_pedido(p)
        c, _ = emitir_comprobante(p)
        self.assertEqual(p.total, c.total)
        self.assertEqual(c.subtotal, Decimal("0.03"))

    def test_nota_parcial_reparte_centavos_sin_desbalance(self):
        from apps.core.monedas import repartir_importe
        partes = repartir_importe(Decimal("0.03"), [Decimal("1")] * 5)
        self.assertEqual(sum(partes), Decimal("0.03"))
        self.assertTrue(all(x >= 0 for x in partes))
        c = self.factura()
        nota, _ = emitir_nota_credito(c, monto="0.07")
        self.assertEqual(sum(l.total for l in nota.lineas.all()), nota.total)
        self.assertEqual(sum(l.igv for l in nota.lineas.all()), nota.igv)

    def test_pagos_parciales_cierran_al_centavo_en_moneda_base(self):
        c = self.factura("USD", "3.333333")
        for monto in (Decimal("0.01"), Decimal("0.02"), c.total - Decimal("0.03")):
            registrar_cobro(c, monto, tipo_cambio="3.6")
        from django.db.models import Sum
        from apps.contabilidad.models import LineaAsiento
        cobrado = LineaAsiento.objects.filter(asiento__movimiento_tesoreria__comprobante=c,
            cuenta__codigo="1212").aggregate(s=Sum("haber"))["s"]
        self.assertEqual(cobrado, c.asientos.get().lineas.get(cuenta__codigo="1212").debe)
        c.refresh_from_db()
        self.assertEqual(c.saldo, 0)


class ConciliacionEstricta(BaseFlujo):
    def setUp(self):
        self.cuenta = CuentaBancaria.objects.get(empresa=self.empresa)

    def movimiento(self, **cambios):
        datos = dict(empresa=self.empresa, cuenta=self.cuenta, tercero=self.cliente,
            sentido=Movimiento.Sentido.COBRO, metodo="transferencia", moneda="PEN",
            monto=100, fecha=date.today(), estado=Movimiento.Estado.CONFIRMADO)
        datos.update(cambios)
        return Movimiento.objects.create(**datos)

    def linea(self, **cambios):
        datos = dict(empresa=self.empresa, cuenta=self.cuenta, fecha=date.today(), monto=100)
        datos.update(cambios)
        return LineaExtractoBancario.objects.create(**datos)

    def test_referencia_no_sustituye_importe_sentido_cuenta_moneda_fecha(self):
        for cambios in (dict(monto=99), dict(sentido="pago"), dict(cuenta=None),
                        dict(moneda="USD"), dict(fecha=date.today()-timedelta(days=10)),
                        dict(estado="pendiente"), dict(comision=1)):
            with self.subTest(cambios=cambios):
                m = self.movimiento(referencia_externa="REF", **cambios)
                linea = self.linea(referencia="REF")
                self.assertEqual(conciliar_extracto(self.cuenta), 0)
                linea.refresh_from_db()
                self.assertFalse(linea.conciliada)
                linea.delete()
                m.delete()

    def test_ambiguedad_de_movimientos_no_elige_primero(self):
        self.movimiento()
        self.movimiento()
        self.linea()
        self.assertEqual(conciliar_extracto(self.cuenta), 0)

    def test_ambiguedad_de_extracto_no_elige_primero(self):
        self.movimiento(referencia_externa="REF")
        self.linea(referencia="REF")
        self.linea(referencia="REF")
        self.assertEqual(conciliar_extracto(self.cuenta), 0)

    def test_referencia_incorrecta_no_cae_en_monto(self):
        self.movimiento(referencia_externa="REF")
        self.linea(referencia="DISTINTA")
        self.assertEqual(conciliar_extracto(self.cuenta), 0)

    def test_coincidencia_sin_referencia_es_unica_e_idempotente(self):
        m = self.movimiento()
        l = self.linea()
        self.assertEqual(conciliar_extracto(self.cuenta), 1)
        self.assertEqual(conciliar_extracto(self.cuenta), 0)
        l.refresh_from_db()
        self.assertEqual(l.movimiento_id, m.pk)

    def test_cargo_solo_con_pago(self):
        m = self.movimiento(sentido="pago")
        l = self.linea(monto=-100)
        self.assertEqual(conciliar_extracto(self.cuenta), 1)
        l.refresh_from_db()
        self.assertEqual(l.movimiento_id, m.pk)


class RolesYAdministracion(BaseFlujo):
    factura = IntegridadOperativa.factura
    @classmethod
    def setUpTestData(cls):
        BaseFlujo.setUpTestData.__func__(cls)
        crear_roles()
        cls.usuario.empresas.add(cls.empresa)

    def rol(self, nombre):
        self.usuario.groups.set([Group.objects.get(name=f"ERP · {nombre}")])
        self.client.force_login(self.usuario)

    def test_sin_rol_no_puede_operar_ni_ver_modulos(self):
        self.client.force_login(self.usuario)
        for ruta in ("ventas:lista", "compras:lista", "tesoreria:movimientos", "contabilidad:balance"):
            self.assertEqual(self.client.get(reverse(ruta)).status_code, 403)
        respuesta = self.client.get(reverse("core:inicio"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'href="/ventas/"')

    def test_roles_separan_funciones(self):
        self.rol("Ventas")
        self.assertEqual(self.client.get(reverse("ventas:lista")).status_code, 200)
        self.assertEqual(self.client.get(reverse("compras:lista")).status_code, 403)
        p = self._pedido()
        self.assertEqual(self.client.post(reverse("ventas:despachar", args=[p.pk])).status_code, 403)
        self.rol("Almacén")
        self.assertEqual(self.client.get(reverse("inventario:stock")).status_code, 200)
        self.assertEqual(self.client.post(reverse("ventas:facturar", args=[p.pk])).status_code, 403)
        self.rol("Caja")
        self.assertEqual(self.client.get(reverse("tesoreria:cobranza")).status_code, 200)
        self.assertEqual(self.client.get(reverse("compras:nueva")).status_code, 403)

    def test_compras_no_aprueba_sus_ordenes_por_tener_acceso(self):
        self.rol("Compras")
        self.assertEqual(self.client.post(reverse("compras:aprobar", args=[1])).status_code, 403)

    def test_otra_empresa_no_es_visible_con_rol_gerencia(self):
        p = self._pedido()
        otra = Empresa.objects.create(ruc="20999999999", razon_social="Otra")
        self.usuario.empresas.set([otra])
        self.usuario.empresa_actual = otra
        self.usuario.save()
        self.rol("Gerencia")
        self.assertEqual(self.client.get(reverse("ventas:detalle", args=[p.pk])).status_code, 404)

    def test_admin_protege_documentos_incluso_superusuario(self):
        c = self.factura()
        m = registrar_cobro(c, 100)
        self.usuario.is_superuser = self.usuario.is_staff = True
        self.usuario.save()
        self.client.force_login(self.usuario)
        for obj in (c, c.pedido, m, m.asientos.get(), MovimientoStock.objects.first()):
            label = f"admin:{obj._meta.app_label}_{obj._meta.model_name}"
            self.assertEqual(self.client.post(reverse(label + "_change", args=[obj.pk]), {}).status_code, 403)
            self.assertEqual(self.client.post(reverse(label + "_delete", args=[obj.pk]), {"post": "yes"}).status_code, 403)
            self.assertEqual(self.client.get(reverse(label + "_change", args=[obj.pk])).status_code, 200)

    def test_todas_las_rutas_operativas_tienen_politica(self):
        from importlib import import_module
        for app in ("ventas", "compras", "catalogo", "terceros", "crm", "inventario",
                    "facturacion", "tesoreria", "contabilidad", "integraciones"):
            for url in import_module(f"apps.{app}.urls").urlpatterns:
                if app == "integraciones" and url.name == "webhook":
                    continue
                self.assertIn(f"{app}:{url.name}", RUTAS)

    def test_indicadores_multimoneda_en_base(self):
        pen = self.factura()
        usd = self.factura("USD", "3.5")
        self.rol("Gerencia")
        r = self.client.get(reverse("core:inicio"))
        self.assertEqual(r.context["ventas_mes"], pen.total + usd.total * Decimal("3.5"))
        r = self.client.get(reverse("tesoreria:cobranza"))
        self.assertEqual(r.context["total"], pen.total + usd.total * Decimal("3.5"))

    def test_todas_las_pantallas_principales_renderizan(self):
        from django.conf import settings
        from django.template.loader import get_template
        for archivo in (settings.BASE_DIR / "templates").rglob("*.html"):
            get_template(archivo.relative_to(settings.BASE_DIR / "templates").as_posix())
        self.rol("Gerencia")
        for ruta in ("core:inicio", "ventas:lista", "ventas:nuevo", "compras:lista",
                     "compras:nueva", "catalogo:lista", "terceros:lista", "crm:embudo",
                     "crm:lista", "inventario:stock", "inventario:movimientos", "inventario:guias",
                     "facturacion:lista", "tesoreria:cobranza", "tesoreria:conciliacion",
                     "tesoreria:movimientos", "contabilidad:asientos", "contabilidad:balance"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(reverse(ruta)).status_code, 200)
