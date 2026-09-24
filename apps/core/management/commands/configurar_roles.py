from django.core.management.base import BaseCommand
from apps.core.permisos import crear_roles


class Command(BaseCommand):
    help = "Crea/actualiza los roles ERP sin asignarlos a usuarios."

    def handle(self, *args, **options):
        roles = crear_roles()
        self.stdout.write(self.style.SUCCESS("Roles configurados: " + ", ".join(roles)))
