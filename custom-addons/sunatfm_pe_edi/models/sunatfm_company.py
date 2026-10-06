import base64

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .sunatfm_signing import load_pkcs12


class ResCompany(models.Model):
    _inherit = "res.company"

    sunatfm_edi_environment = fields.Selection(
        [("test", "Pruebas"), ("production", "Producción")],
        string="Ambiente SUNAT", default="test",
    )
    sunatfm_edi_cpe_enabled = fields.Boolean(string="Habilitar CPE SUNAT")
    sunatfm_edi_gre_enabled = fields.Boolean(string="Habilitar GRE SUNAT")
    sunatfm_edi_gre_series = fields.Char(
        string="Serie GRE remitente", default="T001", copy=False,
        help="Serie exclusiva para guías de remisión remitente. No reutiliza series de facturas.",
    )
    sunatfm_edi_establishment_code = fields.Char(
        string="Código de establecimiento SUNAT",
        default="0000",
        help="Código de local anexo declarado ante SUNAT. Use 0000 para el establecimiento principal.",
    )
    sunatfm_edi_detraction_account = fields.Char(string="Cuenta de detracciones")
    sunatfm_edi_detraction_bank_account_id = fields.Many2one(
        "res.partner.bank", string="Cuenta bancaria de detracciones",
        domain="[('partner_id', '=', partner_id), ('sunatfm_edi_detraction_account', '=', True)]",
        check_company=True,
    )
    sunatfm_edi_detraction_payment_means_code = fields.Selection(
        [("001", "Depósito en cuenta")],
        string="Medio de pago detracción", default="001",
    )
    sunatfm_edi_certificate_p12 = fields.Binary(
        string="Certificado digital (.p12/.pfx)", attachment=True,
        copy=False, groups="base.group_system",
    )
    sunatfm_edi_certificate_filename = fields.Char(copy=False, groups="base.group_system")
    sunatfm_edi_certificate_password = fields.Char(
        string="Contraseña del certificado", copy=False, groups="base.group_system",
    )
    sunatfm_edi_sol_user = fields.Char(string="Usuario SOL", copy=False, groups="base.group_system")
    sunatfm_edi_sol_password = fields.Char(string="Clave SOL", copy=False, groups="base.group_system")
    sunatfm_edi_gre_api_client_id = fields.Char(
        string="Client ID API SUNAT (GRE)", copy=False, groups="base.group_system",
        help="Identificador de la aplicación generado en SOL para el servicio REST de GRE.",
    )
    sunatfm_edi_gre_api_client_secret = fields.Char(
        string="Client Secret API SUNAT (GRE)", copy=False, groups="base.group_system",
        help="Secreto de la aplicación generado en SOL para el servicio REST de GRE.",
    )
    sunatfm_edi_certificate_subject = fields.Char(
        string="Titular del certificado", compute="_compute_sunatfm_certificate_info",
        groups="base.group_system",
    )
    sunatfm_edi_certificate_serial = fields.Char(
        string="Número de serie", compute="_compute_sunatfm_certificate_info",
        groups="base.group_system",
    )
    sunatfm_edi_certificate_expiration = fields.Datetime(
        string="Vencimiento del certificado", compute="_compute_sunatfm_certificate_info",
        groups="base.group_system",
    )
    sunatfm_edi_service_endpoint = fields.Char(
        string="Endpoint SUNAT", compute="_compute_sunatfm_edi_endpoint"
    )
    sunatfm_edi_gre_service_endpoint = fields.Char(
        string="Endpoint GRE SUNAT", compute="_compute_sunatfm_edi_endpoint"
    )
    sunatfm_edi_gre_api_endpoint = fields.Char(
        string="Endpoint API REST GRE", compute="_compute_sunatfm_edi_endpoint"
    )
    sunatfm_edi_gre_token_endpoint = fields.Char(
        string="Endpoint token GRE", compute="_compute_sunatfm_edi_endpoint"
    )

    def _sunatfm_get_detraction_bank_account(self):
        """Return the bank account marked as SUNAT detraction account.

        The explicit relation remains for backward compatibility, while the
        standard bank-account screen is now the source of configuration.
        """
        self.ensure_one()
        return self.sunatfm_edi_detraction_bank_account_id or self.env["res.partner.bank"].search(
            [
                ("partner_id", "=", self.partner_id.id),
                ("sunatfm_edi_detraction_account", "=", True),
            ],
            limit=1,
        )

    @api.depends("sunatfm_edi_environment", "sunatfm_edi_gre_api_client_id")
    def _compute_sunatfm_edi_endpoint(self):
        endpoints = {
            "test": (
                "https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService",
                "https://e-beta.sunat.gob.pe/ol-ti-itemision-guia-gem-beta/billService",
            ),
            "production": (
                "https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService",
                "https://e-guiaremision.sunat.gob.pe/ol-ti-itemision-guia-gem/billService",
            ),
        }
        for company in self:
            cpe_endpoint, gre_endpoint = endpoints.get(company.sunatfm_edi_environment, ("", ""))
            company.sunatfm_edi_service_endpoint = cpe_endpoint
            company.sunatfm_edi_gre_service_endpoint = gre_endpoint
            environment = company.sunatfm_edi_environment or "test"
            company.sunatfm_edi_gre_api_endpoint = (
                "https://api-cpe.sunat.gob.pe/v1/contribuyente/gem"
                if environment in ("test", "production") else ""
            )
            company.sunatfm_edi_gre_token_endpoint = (
                "https://api-seguridad.sunat.gob.pe/v1/clientessol/%s/oauth2/token/"
                % (company.sunatfm_edi_gre_api_client_id or "<client_id>")
                if environment in ("test", "production") else ""
            )

    def action_sunatfm_edi_validate_certificate(self):
        self.ensure_one()
        if not self.sunatfm_edi_certificate_p12:
            raise ValidationError(_("Cargue primero un certificado .p12 o .pfx."))
        try:
            load_pkcs12(
                base64.b64decode(self.sunatfm_edi_certificate_p12),
                self.sunatfm_edi_certificate_password or "",
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

    @api.depends("sunatfm_edi_certificate_p12", "sunatfm_edi_certificate_password")
    def _compute_sunatfm_certificate_info(self):
        for company in self:
            company.sunatfm_edi_certificate_subject = False
            company.sunatfm_edi_certificate_serial = False
            company.sunatfm_edi_certificate_expiration = False
            if not company.sunatfm_edi_certificate_p12:
                continue
            try:
                _, certificate, _ = load_pkcs12(
                    base64.b64decode(company.sunatfm_edi_certificate_p12),
                    company.sunatfm_edi_certificate_password or "",
                )
            except Exception:
                continue
            company.sunatfm_edi_certificate_subject = certificate.subject.rfc4514_string()
            company.sunatfm_edi_certificate_serial = str(certificate.serial_number)
            company.sunatfm_edi_certificate_expiration = certificate.not_valid_after
