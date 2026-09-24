from django.apps import AppConfig


class CrmConfig(AppConfig):
    name = "apps.crm"
    verbose_name = "CRM"

    def ready(self):
        # Registra los manejadores de webhook que traen leads.
        from apps.crm import webhooks  # noqa: F401
