from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SunatfmWithholding(models.Model):
    _name = "sunatfm.pe.withholding"
    _description = "Retención IGV recibida"
    _order = "payment_date desc, id desc"

    name = fields.Char(string="N° CRE", required=True, copy=False)
    invoice_id = fields.Many2one(
        "account.move", string="Factura relacionada", required=True,
        ondelete="cascade", index=True,
        domain="[('move_type', '=', 'out_invoice')]",
    )
    partner_id = fields.Many2one(
        "res.partner", string="Agente de retención",
        related="invoice_id.partner_id", store=True, readonly=True,
    )
    payment_date = fields.Date(string="Fecha de pago", required=True)
    cre_date = fields.Date(string="Fecha del CRE", required=True)
    payment_amount = fields.Monetary(string="Importe pagado", required=True)
    retention_amount = fields.Monetary(string="Importe retenido", required=True)
    retention_base = fields.Monetary(
        string="Base de retención", compute="_compute_retention_data",
        currency_field="currency_id", store=True, readonly=True,
    )
    retention_rate = fields.Float(
        string="Porcentaje de retención", compute="_compute_retention_data",
        digits=(16, 2), store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="invoice_id.currency_id", store=True, readonly=True,
    )
    attachment = fields.Binary(string="CRE adjunto", attachment=True)
    attachment_filename = fields.Char(string="Nombre del archivo")
    note = fields.Text(string="Observaciones")

    @api.depends("payment_amount", "retention_amount")
    def _compute_retention_data(self):
        for record in self:
            record.retention_base = record.payment_amount or 0.0
            record.retention_rate = (
                (record.retention_amount / record.payment_amount) * 100
                if record.payment_amount else 0.0
            )

    @api.constrains("invoice_id", "payment_amount", "retention_amount")
    def _check_amounts(self):
        for record in self:
            if record.payment_amount <= 0:
                raise ValidationError(_("El importe pagado debe ser mayor que cero."))
            if record.retention_amount <= 0:
                raise ValidationError(_("El importe retenido debe ser mayor que cero."))
            if record.retention_amount > record.payment_amount:
                raise ValidationError(_("La retención no puede ser mayor que el importe pagado."))


class AccountMove(models.Model):
    _inherit = "account.move"

    sunatfm_withholding_ids = fields.One2many(
        "sunatfm.pe.withholding", "invoice_id", string="CRE recibidos",
    )
    sunatfm_withholding_received = fields.Monetary(
        string="Retenciones recibidas", compute="_compute_sunatfm_withholding_received",
        currency_field="currency_id",
    )

    @api.depends("sunatfm_withholding_ids.retention_amount")
    def _compute_sunatfm_withholding_received(self):
        for move in self:
            move.sunatfm_withholding_received = sum(
                move.sunatfm_withholding_ids.mapped("retention_amount")
            )
