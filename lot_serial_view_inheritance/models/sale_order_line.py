from odoo import models, fields, api

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    lot_id = fields.Many2one(
        'stock.lot',
        string='Lot/Serial Number',
        domain="[('product_id', '=', product_id)]",
        help="Select a specific Lot/Serial Number for this product."
    )

    @api.onchange('product_id')
    def _onchange_product_id_clear_lot(self):
        """Reset lot_id when product changes."""
        for line in self:
            line.lot_id = False

    def _prepare_invoice_line(self, **optional_values):
        """Propagate the Lot/Serial Number to the invoice line."""
        res = super()._prepare_invoice_line(**optional_values)
        if self.lot_id:
            res['lot_id'] = self.lot_id.id
        return res
