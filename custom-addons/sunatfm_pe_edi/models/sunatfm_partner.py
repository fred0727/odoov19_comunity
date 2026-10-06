from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    sunatfm_edi_withholding_agent = fields.Boolean(
        string="Agente de retención IGV",
        help="El cliente aplica la retención del IGV al efectuar pagos sujetos al régimen.",
    )
