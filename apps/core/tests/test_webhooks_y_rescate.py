"""Lo que entra de afuera y lo que se rescata cuando nada entra.

Cubre el receptor de webhooks (firma, duplicados, procesamiento), la tarea de
rescate de cobros y las notas de crédito.
"""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.contabilidad.models import Asiento
from apps.core.tests.test_flujo_completo import BaseFlujo
from apps.facturacion.models import EstadoComprobante, TipoComprobante
from apps.facturacion.servicios import emitir_comprobante, emitir_nota_credito
from apps.integraciones.conectores import Respuesta
from apps.integraciones.conectores.pasarela import ConectorPasarela
from apps.integraciones.models import EventoWebhook, ServicioExterno
from apps.integraciones.rescate import rescatar_cobros
from apps.integraciones.trabajador import procesar_cola
from apps.integraciones.webhooks import reprocesar_pendientes
from apps.inventario.servicios import despachar_pedido
from apps.tesoreria.models import Movimiento
from apps.ventas.models import EstadoPedido
from apps.ventas.servicios import confirmar_pedido


class BaseConComprobante(BaseFlujo):
    """Deja un comprobante aceptado y sin cobrar, que es el punto de partida."""

    def _comprobante_aceptado(self, cantidad=1):
        pedido = self._pedido(cantidad=cantidad)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        procesar_cola(self.empresa)
        comprobante.refresh_from_db()
        return comprobante

    def _enviar(self, servicio, cuerpo, firmar=True, secreto=None):
        crudo = json.dumps(cuerpo).encode()
        firma = ""
        if firmar:
            secreto = secreto or servicio.secreto_webhook
            firma = hmac.new(secreto.encode(), crudo, hashlib.sha256).hexdigest()
        return self.client.post(
            reverse("integraciones:webhook", args=[servicio.pk]),
            data=crudo,
            content_type="application/json",
            HTTP_X_FIRMA=firma,
        )


class ReceptorDeWebhooks(BaseConComprobante):
    def setUp(self):
        self.pasarela = ServicioExterno.objects.get(
            empresa=self.empresa, codigo=ServicioExterno.Codigo.PASARELA
        )
        self.ose = ServicioExterno.objects.get(
            empresa=self.empresa, codigo=ServicioExterno.Codigo.OSE
        )

    def test_un_aviso_de_pago_firmado_registra_el_cobro(self):
        comprobante = self._comprobante_aceptado()
        respuesta = self._enviar(
            self.pasarela,
            {
                "id": "evt-001",
                "evento": "pago.aprobado",
                "estado": "pagado",
                "numero_completo": comprobante.numero_completo,
                "monto": str(comprobante.total),
                "referencia": "OP-8891",
                "comision": "12.50",
            },
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["estado"], "procesado")

        comprobante.refresh_from_db()
        self.assertTrue(comprobante.esta_pagado)
        cobro = Movimiento.objects.get(comprobante=comprobante)
        self.assertEqual(cobro.referencia_externa, "OP-8891")
        self.assertEqual(cobro.comision, Decimal("12.50"))
        comprobante.pedido.refresh_from_db()
        self.assertEqual(comprobante.pedido.estado, EstadoPedido.CERRADO)

    def test_rechaza_el_aviso_con_firma_invalida_pero_lo_guarda(self):
        comprobante = self._comprobante_aceptado()
        respuesta = self._enviar(
            self.pasarela,
            {
                "id": "evt-falso",
                "estado": "pagado",
                "numero_completo": comprobante.numero_completo,
            },
            secreto="secreto-equivocado",
        )
        self.assertEqual(respuesta.status_code, 401)

        evento = EventoWebhook.objects.get(id_evento_externo="evt-falso")
        self.assertFalse(evento.firma_valida)
        self.assertFalse(evento.procesado)
        # Lo importante: no tocó la plata.
        comprobante.refresh_from_db()
        self.assertFalse(comprobante.esta_pagado)

    def test_el_mismo_aviso_dos_veces_no_cobra_dos_veces(self):
        comprobante = self._comprobante_aceptado()
        cuerpo = {
            "id": "evt-repetido",
            "estado": "pagado",
            "numero_completo": comprobante.numero_completo,
            "monto": str(comprobante.total),
            "referencia": "OP-1",
        }
        primera = self._enviar(self.pasarela, cuerpo)
        segunda = self._enviar(self.pasarela, cuerpo)

        self.assertEqual(primera.json()["estado"], "procesado")
        self.assertEqual(segunda.json()["estado"], "duplicado")
        self.assertEqual(Movimiento.objects.filter(comprobante=comprobante).count(), 1)
        self.assertEqual(EventoWebhook.objects.filter(id_evento_externo="evt-repetido").count(), 1)

    def test_un_evento_sin_firmar_no_bloquea_al_aviso_real_con_el_mismo_id(self):
        """Si contara para deduplicar, bastaría adivinar un id para anular un cobro."""
        comprobante = self._comprobante_aceptado()
        cuerpo = {
            "id": "evt-suplantado",
            "estado": "pagado",
            "numero_completo": comprobante.numero_completo,
            "monto": str(comprobante.total),
            "referencia": "OP-real",
        }
        falso = self._enviar(self.pasarela, cuerpo, secreto="secreto-equivocado")
        self.assertEqual(falso.status_code, 401)

        legitimo = self._enviar(self.pasarela, cuerpo)
        self.assertEqual(legitimo.status_code, 200)
        self.assertEqual(legitimo.json()["estado"], "procesado")

        comprobante.refresh_from_db()
        self.assertTrue(comprobante.esta_pagado)

    def test_un_aviso_sin_comprobante_conocido_se_guarda_para_revisar(self):
        respuesta = self._enviar(
            self.pasarela,
            {"id": "evt-huerfano", "estado": "pagado", "numero_completo": "F001-00099999"},
        )
        self.assertEqual(respuesta.status_code, 202)
        evento = EventoWebhook.objects.get(id_evento_externo="evt-huerfano")
        self.assertFalse(evento.procesado)
        self.assertIn("no existe", evento.mensaje_error)

    def test_el_ose_puede_avisar_el_rechazo_por_webhook(self):
        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)

        self._enviar(
            self.ose,
            {
                "id": "cdr-001",
                "evento": "comprobante.rechazado",
                "numero_completo": comprobante.numero_completo,
                "estado": "rechazado",
                "codigo_respuesta": "2335",
                "mensaje": "Tipo de documento del receptor no válido",
            },
        )
        comprobante.refresh_from_db()
        self.assertEqual(comprobante.estado, EstadoComprobante.RECHAZADO)
        self.assertFalse(Asiento.objects.filter(comprobante=comprobante).exists())

    def test_un_cuerpo_que_no_es_json_se_rechaza(self):
        respuesta = self.client.post(
            reverse("integraciones:webhook", args=[self.pasarela.pk]),
            data=b"esto no es json",
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_un_servicio_inexistente_devuelve_404(self):
        respuesta = self.client.post(
            reverse("integraciones:webhook", args=[99999]),
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_los_eventos_pendientes_se_pueden_reprocesar(self):
        """Un evento que llegó antes que su comprobante se aplica al reintentar."""
        comprobante = self._comprobante_aceptado()
        evento = EventoWebhook.objects.create(
            empresa=self.empresa,
            servicio=self.pasarela,
            evento="pago.aprobado",
            id_evento_externo="evt-tarde",
            cuerpo={
                "estado": "pagado",
                "numero_completo": comprobante.numero_completo,
                "monto": str(comprobante.total),
                "referencia": "OP-tarde",
            },
            firma_valida=True,
            procesado=False,
        )
        hechos, fallidos = reprocesar_pendientes(self.empresa)
        self.assertEqual((hechos, fallidos), (1, 0))
        evento.refresh_from_db()
        self.assertTrue(evento.procesado)
        comprobante.refresh_from_db()
        self.assertTrue(comprobante.esta_pagado)


class RescateDeCobros(BaseConComprobante):
    def test_sin_aviso_la_tarea_encuentra_el_pago_igual(self):
        """El caso «el pago se aprueba pero no llega el aviso»."""
        comprobante = self._comprobante_aceptado()

        pagado = Respuesta(
            exito=True,
            codigo_http=200,
            datos={
                "estado": "pagado",
                "referencia": "OP-rescate",
                "monto": str(comprobante.total),
            },
        )
        with patch.object(ConectorPasarela, "_ejecutar", return_value=pagado):
            consultados, registrados = rescatar_cobros(self.empresa)

        self.assertEqual((consultados, registrados), (1, 1))
        comprobante.refresh_from_db()
        self.assertTrue(comprobante.esta_pagado)
        self.assertEqual(
            Movimiento.objects.get(comprobante=comprobante).referencia_externa, "OP-rescate"
        )

    def test_si_sigue_pendiente_no_inventa_un_cobro(self):
        comprobante = self._comprobante_aceptado()
        consultados, registrados = rescatar_cobros(self.empresa)
        self.assertEqual((consultados, registrados), (1, 0))
        comprobante.refresh_from_db()
        self.assertFalse(comprobante.esta_pagado)

    def test_no_vuelve_a_cobrar_lo_ya_cobrado(self):
        comprobante = self._comprobante_aceptado()
        self._enviar(
            ServicioExterno.objects.get(
                empresa=self.empresa, codigo=ServicioExterno.Codigo.PASARELA
            ),
            {
                "id": "evt-ok",
                "estado": "pagado",
                "numero_completo": comprobante.numero_completo,
                "monto": str(comprobante.total),
                "referencia": "OP-x",
            },
        )
        consultados, registrados = rescatar_cobros(self.empresa)
        self.assertEqual(registrados, 0)
        self.assertEqual(Movimiento.objects.filter(comprobante=comprobante).count(), 1)

    def test_importe_incompatible_se_deja_para_revision_sin_cobrar(self):
        comprobante = self._comprobante_aceptado()
        exagerado = Respuesta(
            exito=True,
            codigo_http=200,
            datos={"estado": "pagado", "referencia": "OP-raro", "monto": "999999.00"},
        )
        with patch.object(ConectorPasarela, "_ejecutar", return_value=exagerado):
            _, registrados = rescatar_cobros(self.empresa)

        comprobante.refresh_from_db()
        self.assertEqual(comprobante.total_cobrado, 0)
        self.assertEqual(registrados, 0)
        self.assertFalse(comprobante.cobros.exists())


class NotasDeCredito(BaseConComprobante):
    def test_la_nota_total_extorna_la_venta(self):
        comprobante = self._comprobante_aceptado(cantidad=2)
        nota, trabajo = emitir_nota_credito(comprobante, motivo="06", usuario=self.usuario)

        self.assertEqual(nota.tipo, TipoComprobante.NOTA_CREDITO)
        self.assertEqual(nota.total, comprobante.total)
        self.assertEqual(nota.comprobante_afectado, comprobante)
        self.assertEqual(nota.estado, EstadoComprobante.POR_ENVIAR)

        procesar_cola(self.empresa)
        nota.refresh_from_db()
        self.assertEqual(nota.estado, EstadoComprobante.ACEPTADO)

        asiento = Asiento.objects.get(comprobante=nota)
        self.assertTrue(asiento.cuadra)
        # Es el asiento de la venta al revés: la cuenta por cobrar va al haber.
        cobrar = asiento.lineas.get(cuenta__codigo="1212")
        self.assertEqual(cobrar.haber, comprobante.total)

    def test_admite_una_nota_parcial_y_no_deja_acreditar_de_mas(self):
        from django.core.exceptions import ValidationError

        comprobante = self._comprobante_aceptado(cantidad=2)
        mitad = (comprobante.total / 2).quantize(Decimal("0.01"))
        nota, _ = emitir_nota_credito(comprobante, motivo="07", monto=mitad)
        self.assertEqual(nota.total, mitad)
        self.assertEqual(nota.subtotal + nota.igv, mitad)

        with self.assertRaises(ValidationError):
            emitir_nota_credito(comprobante, motivo="07", monto=comprobante.total)

    def test_no_se_acredita_un_comprobante_que_sunat_no_acepto(self):
        from django.core.exceptions import ValidationError

        pedido = self._pedido(cantidad=1)
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        self.assertEqual(pedido.estado, EstadoPedido.ENTREGADO)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)  # queda por_enviar

        with self.assertRaises(ValidationError):
            emitir_nota_credito(comprobante)

    def test_el_motivo_debe_estar_en_el_catalogo_de_sunat(self):
        from django.core.exceptions import ValidationError

        comprobante = self._comprobante_aceptado()
        with self.assertRaises(ValidationError):
            emitir_nota_credito(comprobante, motivo="99")
