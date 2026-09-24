"""Tareas programadas de rescate.

    python manage.py rescatar              # cobros y comprobantes
    python manage.py rescatar --solo cobros

Pensado para un cron cada 15 minutos. No depende de que los webhooks lleguen.
"""
from django.core.management.base import BaseCommand

from apps.core.models import Empresa
from apps.integraciones.rescate import rescatar_cobros, rescatar_comprobantes
from apps.integraciones.webhooks import reprocesar_pendientes


class Command(BaseCommand):
    help = "Consulta a los servicios externos por lo que quedó sin respuesta."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", type=int, help="Id de empresa; por defecto, todas")
        parser.add_argument(
            "--solo",
            choices=("cobros", "comprobantes", "webhooks"),
            help="Corre una sola tarea en vez de las tres",
        )

    def handle(self, *args, **opciones):
        empresa = Empresa.objects.get(pk=opciones["empresa"]) if opciones["empresa"] else None
        solo = opciones["solo"]

        if solo in (None, "cobros"):
            consultados, registrados = rescatar_cobros(empresa)
            self.stdout.write(
                f"Cobros: {consultados} comprobante(s) consultado(s), "
                f"{registrados} cobro(s) rescatado(s)."
            )

        if solo in (None, "comprobantes"):
            consultados, resueltos = rescatar_comprobantes(empresa)
            self.stdout.write(
                f"Comprobantes: {consultados} consultado(s), {resueltos} resuelto(s)."
            )

        if solo in (None, "webhooks"):
            hechos, fallidos = reprocesar_pendientes(empresa)
            estilo = self.style.WARNING if fallidos else self.style.SUCCESS
            self.stdout.write(
                estilo(f"Webhooks pendientes: {hechos} aplicado(s), {fallidos} con error.")
            )
