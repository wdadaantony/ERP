"""Leads que entran de WhatsApp, Meta y automatizaciones.

Cubre la traducción de cada formato, la deduplicación entre canales, el reparto
al vendedor y la conversión que vuelve a la plataforma de anuncios.
"""
import hashlib
import hmac
import json
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.core.tests.test_flujo_completo import BaseFlujo
from apps.crm.entrada import (
    desde_generico,
    desde_meta,
    desde_whatsapp,
    normalizar_telefono,
    registrar_lead,
)
from apps.crm.models import Actividad, EstadoLead, Lead
from apps.crm.servicios import asignar_vendedor, enviar_conversion, vendedores_de
from apps.integraciones.models import ServicioExterno, TrabajoIntegracion
from apps.integraciones.trabajador import procesar_cola


def _meta_leadgen(nombre="Ana Torres", email="ana@correo.pe", telefono="987654321", leadgen="lg-1"):
    return {
        "object": "page",
        "entry": [
            {
                "id": "pagina-1",
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": leadgen,
                            "campaign_name": "Setiembre laptops",
                            "ad_id": "ad-99",
                            "form_id": "form-7",
                            "field_data": [
                                {"name": "full_name", "values": [nombre]},
                                {"name": "email", "values": [email]},
                                {"name": "phone_number", "values": [telefono]},
                                {"name": "mensaje", "values": ["Quiero 3 laptops"]},
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _whatsapp_mensaje(texto="Hola, ¿tienen stock?", wa_id="51987654321", nombre="Ana Torres"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": nombre}}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": f"wamid.{texto[:6]}",
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


class TraduccionDeFormatos(TestCase):
    def test_traduce_un_formulario_de_meta(self):
        (lead,) = desde_meta(_meta_leadgen())
        self.assertEqual(lead["nombre"], "Ana Torres")
        self.assertEqual(lead["email"], "ana@correo.pe")
        self.assertEqual(lead["telefono"], "51987654321")
        self.assertEqual(lead["origen"], "meta_ads")
        self.assertEqual(lead["campania"], "Setiembre laptops")
        self.assertEqual(lead["id_externo"], "lg-1")
        self.assertEqual(lead["utm"]["ad_id"], "ad-99")

    def test_traduce_un_mensaje_de_whatsapp(self):
        (lead,) = desde_whatsapp(_whatsapp_mensaje())
        self.assertEqual(lead["telefono"], "51987654321")
        self.assertEqual(lead["origen"], "whatsapp")
        self.assertIn("stock", lead["interes"])
        self.assertTrue(lead["es_mensaje"])

    def test_un_acuse_de_whatsapp_no_es_un_lead(self):
        acuse = {
            "entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]
        }
        self.assertEqual(desde_whatsapp(acuse), [])

    def test_la_carga_generica_acepta_los_alias_habituales(self):
        (lead,) = desde_generico(
            {
                "name": "Luis Paz",
                "company": "Paz SAC",
                "correo": "luis@paz.pe",
                "celular": "+51 999 888 777",
                "mensaje": "Cotización por favor",
                "budget": "5,000.50",
                "utm_source": "google",
                "utm_campaign": "search-laptops",
            }
        )
        self.assertEqual(lead["nombre"], "Luis Paz")
        self.assertEqual(lead["empresa_lead"], "Paz SAC")
        self.assertEqual(lead["telefono"], "51999888777")
        self.assertEqual(lead["valor_estimado"], 5000.5)
        self.assertEqual(lead["utm"]["utm_campaign"], "search-laptops")

    def test_acepta_una_lista_de_leads(self):
        salidas = desde_generico({"leads": [{"name": "A", "email": "a@a.pe"}, {"name": "B", "phone": "999111222"}]})
        self.assertEqual(len(salidas), 2)

    def test_descarta_lo_que_no_tiene_con_que_contactar(self):
        self.assertEqual(desde_generico({"comentario": "hola"}), [])

    def test_normaliza_telefonos_peruanos(self):
        self.assertEqual(normalizar_telefono("987 654 321"), "51987654321")
        self.assertEqual(normalizar_telefono("+51 987-654-321"), "51987654321")
        self.assertEqual(normalizar_telefono("(01) 4251234"), "014251234")


class AltaYDeduplicacion(BaseFlujo):
    def test_crea_el_lead_y_lo_asigna(self):
        lead, nuevo = registrar_lead(self.empresa, desde_meta(_meta_leadgen())[0])
        self.assertTrue(nuevo)
        self.assertIsNotNone(lead.vendedor)
        self.assertEqual(lead.estado, EstadoLead.NUEVO)
        self.assertTrue(lead.actividades.exists())

    def test_el_mismo_cliente_por_dos_canales_es_un_solo_lead(self):
        """Escribe por WhatsApp y además llena el formulario de Meta."""
        primero, _ = registrar_lead(self.empresa, desde_whatsapp(_whatsapp_mensaje())[0])
        segundo, nuevo = registrar_lead(self.empresa, desde_meta(_meta_leadgen())[0])

        self.assertFalse(nuevo)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(Lead.objects.filter(empresa=self.empresa).count(), 1)
        # El segundo canal completó lo que faltaba.
        segundo.refresh_from_db()
        self.assertEqual(segundo.email, "ana@correo.pe")
        self.assertEqual(segundo.id_externo, "lg-1")
        # Y quedaron las dos conversaciones en el hilo.
        self.assertEqual(segundo.actividades.count(), 2)

    def test_un_lead_perdido_que_vuelve_a_escribir_se_reabre(self):
        lead, _ = registrar_lead(self.empresa, desde_whatsapp(_whatsapp_mensaje())[0])
        lead.estado = EstadoLead.PERDIDO
        lead.motivo_perdida = "No contestó"
        lead.save()

        registrar_lead(self.empresa, desde_whatsapp(_whatsapp_mensaje(texto="Sigo interesado"))[0])
        lead.refresh_from_db()
        self.assertEqual(lead.estado, EstadoLead.CONTACTADO)
        self.assertEqual(lead.motivo_perdida, "")

    def test_no_pisa_lo_que_el_vendedor_ya_puso(self):
        lead, _ = registrar_lead(
            self.empresa, {"nombre": "Ana", "email": "ana@correo.pe", "empresa_lead": "Mi nota"}
        )
        registrar_lead(
            self.empresa, {"nombre": "Otro nombre", "email": "ana@correo.pe", "empresa_lead": "Meta dice otra"}
        )
        lead.refresh_from_db()
        self.assertEqual(lead.empresa_lead, "Mi nota")
        self.assertEqual(lead.nombre, "Ana")

    def test_el_mensaje_entrante_queda_marcado_como_del_cliente(self):
        lead, _ = registrar_lead(self.empresa, desde_whatsapp(_whatsapp_mensaje())[0])
        actividad = lead.actividades.first()
        self.assertTrue(actividad.es_entrante)
        self.assertEqual(actividad.tipo, Actividad.Tipo.WHATSAPP)


class RepartoDeLeads(BaseFlujo):
    def test_solo_reparte_entre_quienes_tienen_el_permiso(self):
        from apps.core.models import Usuario

        contable = Usuario.objects.create_user(username="contable", password="x")
        contable.empresas.add(self.empresa)
        permiso = Permission.objects.get(codename="recibir_leads", content_type__app_label="crm")
        self.usuario.user_permissions.add(permiso)

        self.assertIn(self.usuario, vendedores_de(self.empresa))
        self.assertNotIn(contable, vendedores_de(self.empresa))

        lead = Lead.objects.create(empresa=self.empresa, nombre="Prueba")
        asignar_vendedor(lead)
        self.assertEqual(lead.vendedor, self.usuario)

    def test_sin_nadie_marcado_reparte_entre_todos(self):
        """Antes que dejar leads huérfanos, se reparten a quien haya."""
        lead = Lead.objects.create(empresa=self.empresa, nombre="Prueba")
        asignar_vendedor(lead)
        self.assertIsNotNone(lead.vendedor)


class WebhooksDeLeads(BaseFlujo):
    def setUp(self):
        # `sembrar_demo` ya deja estos tres servicios; solo se fija el secreto
        # para saber con qué firmar en cada prueba.
        self.ads = ServicioExterno.objects.get(empresa=self.empresa, codigo=ServicioExterno.Codigo.ADS)
        self.ads.secreto_webhook = "secreto-ads"
        self.ads.save(update_fields=["secreto_webhook"])
        self.whatsapp = ServicioExterno.objects.get(
            empresa=self.empresa, codigo=ServicioExterno.Codigo.WHATSAPP
        )
        self.whatsapp.secreto_webhook = "secreto-wa"
        self.whatsapp.save(update_fields=["secreto_webhook"])
        self.zap = ServicioExterno.objects.get(
            empresa=self.empresa, codigo=ServicioExterno.Codigo.AUTOMATIZACION
        )
        self.zap.secreto_webhook = "secreto-zap"
        self.zap.save(update_fields=["secreto_webhook"])

    def _enviar(self, servicio, cuerpo, secreto=None):
        crudo = json.dumps(cuerpo).encode()
        firma = hmac.new(
            (secreto or servicio.secreto_webhook).encode(), crudo, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            reverse("integraciones:webhook", args=[servicio.pk]),
            data=crudo, content_type="application/json", HTTP_X_FIRMA=firma,
        )

    def test_un_formulario_de_meta_crea_el_lead(self):
        r = self._enviar(self.ads, _meta_leadgen())
        self.assertEqual(r.status_code, 200)
        self.assertIn("1 lead nuevo", r.json()["detalle"])
        lead = Lead.objects.get(empresa=self.empresa)
        self.assertEqual(lead.origen, "meta_ads")
        self.assertEqual(lead.campania, "Setiembre laptops")

    def test_un_mensaje_de_whatsapp_crea_el_lead(self):
        r = self._enviar(self.whatsapp, _whatsapp_mensaje())
        self.assertEqual(r.status_code, 200)
        lead = Lead.objects.get(empresa=self.empresa)
        self.assertEqual(lead.origen, "whatsapp")
        self.assertEqual(lead.telefono, "51987654321")

    def test_una_automatizacion_puede_mandar_varios_leads(self):
        r = self._enviar(self.zap, {"leads": [
            {"name": "Uno", "email": "uno@x.pe"}, {"name": "Dos", "phone": "999000111"},
        ]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Lead.objects.filter(empresa=self.empresa).count(), 2)

    def test_un_aviso_sin_firma_no_crea_nada(self):
        r = self._enviar(self.ads, _meta_leadgen(), secreto="secreto-equivocado")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(Lead.objects.filter(empresa=self.empresa).exists())

    def test_un_acuse_de_estado_se_guarda_pero_no_crea_lead(self):
        r = self._enviar(self.whatsapp, {"entry": [{"changes": [{"value": {"statuses": []}}]}]})
        self.assertEqual(r.status_code, 202)
        self.assertFalse(Lead.objects.filter(empresa=self.empresa).exists())

    def test_meta_puede_verificar_la_suscripcion(self):
        """El «hub challenge» que Meta exige al registrar la URL."""
        url = reverse("integraciones:webhook", args=[self.ads.pk])
        r = self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "secreto-ads",
                                 "hub.challenge": "1234567890"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode(), "1234567890")

    def test_rechaza_una_verificacion_con_token_falso(self):
        url = reverse("integraciones:webhook", args=[self.ads.pk])
        r = self.client.get(url, {"hub.verify_token": "inventado", "hub.challenge": "123"})
        self.assertEqual(r.status_code, 403)


class ConversionDeVuelta(BaseFlujo):
    """El paso 17: la venta real vuelve a la plataforma de anuncios."""

    def setUp(self):
        self.ads = ServicioExterno.objects.get(empresa=self.empresa, codigo=ServicioExterno.Codigo.ADS)

    def _lead_con_pedido(self):
        from apps.ventas.servicios import confirmar_pedido

        lead, _ = registrar_lead(self.empresa, desde_meta(_meta_leadgen())[0])
        lead.tercero = self.cliente
        pedido = self._pedido(cantidad=1)
        lead.pedido = pedido
        lead.save()
        return lead, pedido

    def test_al_cobrarse_la_venta_se_encola_la_conversion(self):
        from apps.facturacion.servicios import emitir_comprobante
        from apps.inventario.servicios import despachar_pedido
        from apps.tesoreria.servicios import registrar_cobro
        from apps.ventas.servicios import confirmar_pedido

        lead, pedido = self._lead_con_pedido()
        confirmar_pedido(pedido, self.usuario)
        despachar_pedido(pedido, self.usuario)
        comprobante, _ = emitir_comprobante(pedido, self.usuario)
        procesar_cola(self.empresa)
        comprobante.refresh_from_db()

        self.assertFalse(lead.conversion_enviada)
        registrar_cobro(comprobante, comprobante.total, referencia_externa="OP-conv")

        trabajo = TrabajoIntegracion.objects.get(operacion="enviar_conversion")
        self.assertEqual(str(trabajo.objeto_id), str(lead.pk))
        self.assertEqual(Decimal(trabajo.carga["valor"]), comprobante.total)

        procesar_cola(self.empresa)
        lead.refresh_from_db()
        self.assertTrue(lead.conversion_enviada)

    def test_no_se_envia_dos_veces(self):
        lead, pedido = self._lead_con_pedido()
        pedido.total = Decimal("1000")
        pedido.save()
        primero = enviar_conversion(lead)
        self.assertIsNotNone(primero)
        lead.conversion_enviada = True
        lead.save()
        self.assertIsNone(enviar_conversion(lead))

    def test_un_lead_sin_campana_no_tiene_a_donde_volver(self):
        lead = Lead.objects.create(empresa=self.empresa, nombre="De mostrador", origen="")
        self.assertIsNone(enviar_conversion(lead, Decimal("500")))

    def test_los_datos_personales_viajan_hasheados(self):
        from apps.integraciones.conectores.meta import construir_conversion

        lead, _ = registrar_lead(self.empresa, desde_meta(_meta_leadgen())[0])
        carga = construir_conversion(lead, Decimal("1000"))
        self.assertNotIn("ana@correo.pe", json.dumps(carga))
        self.assertEqual(
            carga["usuario"]["em"], hashlib.sha256(b"ana@correo.pe").hexdigest()
        )
