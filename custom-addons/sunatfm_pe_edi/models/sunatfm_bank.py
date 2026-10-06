from odoo import fields, models


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    sunatfm_edi_detraction_account = fields.Boolean(
        string="Cuenta de detracciones SUNAT",
        help="Identifica la cuenta del Banco de la Nación usada para detracciones.",
    )
    sunatfm_edi_cci = fields.Char(
        string="CCI",
        help="Código de Cuenta Interbancario que se mostrará en documentos comerciales.",
    )
