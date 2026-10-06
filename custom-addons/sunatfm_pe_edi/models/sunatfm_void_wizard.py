from odoo import _, fields, models
from odoo.exceptions import ValidationError


class SunatFmEdiVoidWizard(models.TransientModel):
    _name = "sunatfm.pe.edi.void.wizard"
    _description = "Solicitud de baja SUNAT"

    account_move_id = fields.Many2one("account.move", required=True, readonly=True)
    reason = fields.Char(string="Motivo de anulación", required=True, default="Anulación de comprobante")

    def action_confirm(self):
        self.ensure_one()
        if len(self.reason.strip()) < 3:
            raise ValidationError(_("Ingrese un motivo de anulación de al menos 3 caracteres."))
        return self.account_move_id._sunatfm_edi_submit_void(self.reason.strip())
