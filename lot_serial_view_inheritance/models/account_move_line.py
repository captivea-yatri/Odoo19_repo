from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    lot_id = fields.Many2one(
        'stock.lot',
        string='Lot/Serial Number',
        help="Lot/Serial Number linked from the sale order line.",
        domain="[('product_id', '=', product_id)]",
    )
