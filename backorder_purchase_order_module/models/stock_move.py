
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
    company_currency_id = fields.Many2one(
        'res.currency',
        string='Company Currency',
        related='company_id.currency_id',
        readonly=True,
    )

    cost_price = fields.Monetary('Cost Price', compute='_compute_total', store=True, currency_field='company_currency_id')

    @api.depends('quantity', 'value')
    def _compute_total(self):
        for record in self:
            if record.quantity:  # ✅ safe check
                record.cost_price = record.value / record.quantity
            else:
                record.cost_price = 0.0  # ✅ fallback

    def _run_valuation(self, quantity=None):
        """
        Prevent automatic valuation recalculation when:
         - context contains manual_move_update=True OR
         - system config 'backorder_purchase.disable_auto_valuation' is True
        """
        # check explicit context first (wizard sets this when intended)
        if self.env.context.get('manual_move_update'):
            return

        # check system-wide config toggle
        try:
            disable = self.env['ir.config_parameter'].sudo().get_param(
                'backorder_purchase.disable_auto_valuation', default='False')
            if str(disable).lower() in ('1', 'true', 'yes'):
                return
        except Exception:
            # fail-safe: if config not available, continue normal behaviour
            pass

        return super()._run_valuation(quantity)

    def _create_valuation_layer(self, quantity=None):
        """Skip creating valuation layer if manual override active."""
        if self.env.context.get('manual_move_update'):
            return self.env['stock.valuation.layer']
        try:
            disable = self.env['ir.config_parameter'].sudo().get_param(
                'backorder_purchase.disable_auto_valuation', default='False')
            if str(disable).lower() in ('1', 'true', 'yes'):
                return self.env['stock.valuation.layer']
        except Exception:
            pass
        return super()._create_valuation_layer(quantity)

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
    company_currency_id = fields.Many2one(
        'res.currency',
        string='Company Currency',
        related='company_id.currency_id',
        readonly=True,
    )

    cost_price = fields.Monetary('Cost Price', compute='_compute_total', store=True, currency_field='company_currency_id')

    @api.depends('quantity', 'value')
    def _compute_total(self):
        for record in self:
            if record.quantity:  # ✅ safe check
                record.cost_price = record.value / record.quantity
            else:
                record.cost_price = 0.0  # ✅ fallback

    def _get_value_data(
        self,
        forced_std_price=False,
        at_date=False,
        ignore_manual_update=False,
        add_extra_value=True,
    ):
        """Fix: prevent revaluing moves (like PO2) when product standard_price changes.
        Only recompute value if this move has no fixed manual or PO value.
        """
        self.ensure_one()

        # ✅ If move already has a manual price/value, do not recompute
        if self.env.context.get("manual_move_update") is not True and self.value:
            # This move already valued (like PO2), so return existing info
            return {
                "value": self.value,
                "quantity": self._get_valued_qty(),
                "description": "Existing move value retained (manual update not forced)",
            }

        # 🔁 Otherwise, fallback to Odoo’s original behavior
        return super(StockMove, self)._get_value_data(
            forced_std_price=forced_std_price,
            at_date=at_date,
            ignore_manual_update=ignore_manual_update,
            add_extra_value=add_extra_value,
        )

    def _set_value(self, correction_quantity=None):
        """
        Normal Odoo value computation, but isolated for active Backorder Purchase Order context.
        Ensures quant + standard_price are updated correctly.
        """
        res = super()._set_value(correction_quantity)

        active_backorder_order = self.env.context.get('active_backorder_order')
        if active_backorder_order:
            order = self.env['backorder.purchase.order'].browse(active_backorder_order)
            _logger.info("[PO %s] Post-_set_value hook triggered for %d move(s)", order.name, len(self))
            for move in self.filtered(lambda m: m.product_id and m.state == 'done'):
                order._update_standard_price_from_move(move)
        return res
    def _set_value(self, correction_quantity=None):
        """
        Normal Odoo value computation, but isolated for active Backorder Purchase Order context.
        Ensures quant + standard_price are updated correctly.
        """
        res = super()._set_value(correction_quantity)

        active_backorder_order = self.env.context.get('active_backorder_order')
        if active_backorder_order:
            order = self.env['backorder.purchase.order'].browse(active_backorder_order)
            _logger.info("[PO %s] Post-_set_value hook triggered for %d move(s)", order.name, len(self))
            for move in self.filtered(lambda m: m.product_id and m.state == 'done'):
                order._update_standard_price_from_move(move)
        return res