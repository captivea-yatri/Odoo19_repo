from odoo import models, api

class ResPartner(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """Override confirm button to also create an invoice."""
        res = super().action_confirm()

        # Automatically create invoice(s) after confirmation
        for order in self:
            if order.invoice_status in ('to invoice', 'no'):  # only if invoicing allowed
                # Create invoice
                invoice = order._create_invoices()
                # Optionally post the invoice immediately
                invoice.action_post()

                # Optional: open invoice after creation
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Customer Invoice',
                    'res_model': 'account.move',
                    'view_mode': 'form',
                    'res_id': invoice.id,
                    'target': 'current',
                }

        return res