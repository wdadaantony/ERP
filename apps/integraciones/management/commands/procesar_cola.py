"""Vacía la cola de integraciones.

    python manage.py procesar_cola            # una pasada
    python manage.py procesar_cola --continuo # se queda corriendo

Mientras no haya Celery, esto va en un cron cada minuto o corriendo en continuo
bajo un supervisor.
"""
import time

from django.core.management.base import BaseCommand

from apps.core.models import Empresa
from apps.integraciones.trabajador import procesar_cola


class Command(BaseCommand):
    help = "Procesa los trabajos pendientes de la cola de integraciones."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", type=int, help="Id de empresa; por defecto, todas")
        parser.add_argument("--limite", type=int, default=50, help="Trabajos por pasada")
        parser.add_argument(
            "--continuo", action="store_true", help="No termina: sigue vaciando la cola"
        )
        parser.add_argument(
            "--intervalo", type=int, default=10, help="Segundos entre pasadas en modo continuo"
        )

    def handle(self, *args, **opciones):
        empresa = None
        if opciones["empresa"]:
            empresa = Empresa.objects.get(pk=opciones["empresa"])

        if not opciones["continuo"]:
            self._una_pasada(empresa, opciones["limite"])
            return

        self.stdout.write("Procesando la cola en continuo. Ctrl+C para detener.")
        try:
            while True:
                self._una_pasada(empresa, opciones["limite"], silencioso=True)
                time.sleep(opciones["intervalo"])
        except KeyboardInterrupt:
            self.stdout.write("\nDetenido.")

    def _una_pasada(self, empresa, limite, silencioso=False):
        resumen = procesar_cola(empresa, limite)
        if not resumen:
            if not silencioso:
                self.stdout.write("No había nada pendiente.")
            return
        for estado, cantidad in sorted(resumen.items()):
            estilo = self.style.SUCCESS if estado == "hecho" else self.style.WARNING
            self.stdout.write(estilo(f"  {estado}: {cantidad}"))
