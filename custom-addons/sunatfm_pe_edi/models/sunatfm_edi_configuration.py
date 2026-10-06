import base64

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .sunatfm_signing import load_pkcs12


class SunatFmEdiConfiguration(models.Model):
    """Per-company configuration for the direct SUNAT implementation.

    Certificate and SOL fields are restricted to system administrators. They
    are provided for local/Beta testing and must be replaced by a secret
    manager before production deployment.
    """

    _name = "sunatfm.pe.edi.configuration"
    _description = "Configuración EDI SUNAT"
    _rec_name = "company_id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        ondelete="cascade",
        index=True,
    )
    environment = fields.Selection(
        [
            ("test", "Pruebas"),
            ("production", "Producción"),
        ],
        required=True,
        default="test",
        help="El entorno se aplicará cuando se implemente el transporte directo a SUNAT.",
    )
    cpe_enabled = fields.Boolean(string="Habilitar CPE", default=False)
    gre_enabled = fields.Boolean(string="Habilitar GRE remitente", default=False)
    establishment_code = fields.Char(
        string="Código de establecimiento SUNAT",
        help="Código declarado ante SUNAT para el establecimiento emisor.",
    )
    detraction_account = fields.Char(
        string="Cuenta de detracciones",
        help="Número de cuenta del Banco de la Nación que se informará en el XML.",
    )
    detraction_payment_means_code = fields.Selection(
        [("001", "Depósito en cuenta")],
        string="Medio de pago detracción",
        default="001",
    )
    certificate_p12 = fields.Binary(
        string="Certificado digital (.p12/.pfx)",
        attachment=True,
        copy=False,
        groups="base.group_system",
    )
    certificate_filename = fields.Char(copy=False, groups="base.group_system")
    certificate_password = fields.Char(
        string="Contraseña del certificado",
        copy=False,
        groups="base.group_system",
    )
    sol_user = fields.Char(string="Usuario SOL", copy=False, groups="base.group_system")
    sol_password = fields.Char(string="Clave SOL", copy=False, groups="base.group_system")
    service_endpoint = fields.Char(
        string="Endpoint SUNAT", compute="_compute_service_endpoint"
    )
    active = fields.Boolean(default=True)

    _company_uniq = models.Constraint(
        "UNIQUE(company_id)",
        "Solo puede existir una configuración EDI SUNAT por empresa.",
    )

    @api.constrains("environment", "cpe_enabled", "gre_enabled")
    def _check_production_enablement(self):
        for configuration in self:
            if (
                configuration.environment == "production"
                and (configuration.cpe_enabled or configuration.gre_enabled)
            ):
                raise ValidationError(
                    _(
                        "La conexión directa a SUNAT aún no está implementada. "
                        "No active CPE ni GRE en producción."
                    )
                )

    @api.depends("environment")
    def _compute_service_endpoint(self):
        endpoints = {
            "test": "https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService",
            "production": "https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService",
        }
        for configuration in self:
            configuration.service_endpoint = endpoints.get(configuration.environment, "")

    def action_validate_certificate(self):
        self.ensure_one()
        if not self.certificate_p12:
            raise ValidationError(_("Cargue primero un certificado .p12 o .pfx."))
        try:
            load_pkcs12(
                base64.b64decode(self.certificate_p12),
                self.certificate_password or "",
            )
        except Exception as error:
            raise ValidationError(_("El certificado no pudo validarse: %s") % error) from error
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Certificado válido"),
                "message": _("El certificado PKCS#12 se pudo abrir correctamente."),
                "type": "success",
                "sticky": False,
            },
        }
