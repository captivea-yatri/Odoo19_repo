from odoo import models, fields,api

class StockMove(models.Model):
    _inherit = 'stock.move'

    backorder_line_id = fields.Many2one(
        'backorder.purchase.order.line',
        string='Backorder Order Line',
        ondelete='set null',
        index=True,
        help="Links this stock move to its originating Backorder Purchase Order Line."
    )

    qty_done = fields.Float(string='Quantity', help="Quantity of Stock Move",index=True)