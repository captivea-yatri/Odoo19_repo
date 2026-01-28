from odoo import models, fields, api, _
from odoo.exceptions import UserError


class BackorderPurchaseUpdateWizard(models.TransientModel):
    _name = 'backorder.purchase.update.wizard'
    _description = 'Wizard to update Backorder Purchase Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    order_id = fields.Many2one('backorder.purchase.order', string='Backorder Order', required=True)
    vendor_id = fields.Many2one('res.partner', string='Vendor')
    line_ids = fields.One2many('backorder.purchase.update.wizard.line', 'wizard_id', string='Order Lines')

    # ------------------------------------------------------------
    # Default load
    # ------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if not active_id:
            return res

        order = self.env['backorder.purchase.order'].browse(active_id)
        res['order_id'] = order.id
        res['vendor_id'] = order.vendor_id.id
        lines = []
        for line in order.order_line.filtered(lambda l: l.active):
            lines.append((0, 0, {
                'line_id': line.id,
                'product_id': line.product_id.id,
                'vendor_id': line.vendor_id.id,
                'quantity': line.quantity,
                'price': line.price,
            }))
        res['line_ids'] = lines
        return res

    # ------------------------------------------------------------
    # Apply Updates
    # ------------------------------------------------------------
    def action_apply_updates(self):
        """Safely update a confirmed order — temporarily sets to draft, updates, then reconfirms."""
        self.ensure_one()
        order = self.order_id
        ctx = dict(self.env.context, allow_wizard_write=True)

        # 1️⃣ Temporarily set to draft to allow modifications
        if order.state == 'confirm':
            order.with_context(ctx).write({'state': 'draft'})

        old_vendor = order.vendor_id

        # 2️⃣ Update Vendor if changed
        if self.vendor_id and self.vendor_id != old_vendor:
            order.with_context(ctx).write({'vendor_id': self.vendor_id.id})
            if order.account_move_id:
                move = order.account_move_id
                if move.state == 'posted':
                    move.button_draft()
                move.write({'partner_id': self.vendor_id.id})
                move.line_ids.write({'partner_id': self.vendor_id.id})
                move.action_post()

        # 3️⃣ Update or create order lines

        existing_line_ids = set(order.order_line.ids)
        wizard_line_ids = set([l.line_id.id for l in self.line_ids if l.line_id])
        removed_line_ids = existing_line_ids - wizard_line_ids

        if removed_line_ids:
            removed_lines = order.order_line.browse(removed_line_ids)
            for line in removed_lines:
                line.unlink()

        # 🟩 Apply or update remaining lines (existing or new)
        total_qty = 0
        total_value = 0
        for wiz_line in self.line_ids:
            vals = {
                'product_id': wiz_line.product_id.id,
                'vendor_id': wiz_line.vendor_id.id,
                'quantity': wiz_line.quantity,
                'price': wiz_line.price,
            }

            if wiz_line.line_id:
                wiz_line.line_id.with_context(ctx).write(vals)
                line = wiz_line.line_id
            else:
                vals['order_id'] = order.id
                line = self.env['backorder.purchase.order.line'].with_context(ctx).create(vals)

            total_qty += line.quantity
            total_value += line.total
        # 4️⃣ Update stock moves (with reuse logic)
        self._update_or_create_stock_moves(order)

        # 5️⃣ Recreate journal entry
        self._recreate_journal_entry(order)

        # 6️⃣ Update product cost/value
        for line in order.order_line.filtered('active'):
            self._recompute_product_cost(line.product_id)

        # 7️⃣ Reconfirm the order
        order.with_context(ctx).write({'state': 'confirm'})

        # 8️⃣ Log chatter
        order.message_post(
            body=_("Vendor changed from <b>%s</b> to <b>%s</b> via Update Wizard.") %
                 (old_vendor.display_name, self.vendor_id.display_name),
            subtype_xmlid="mail.mt_note",
            body_is_html=True
        )
        if order.account_move_id:
            order.account_move_id.message_post(
                body=_("Vendor updated from <b>%s</b> to <b>%s</b> (Backorder: %s)") %
                     (old_vendor.display_name, self.vendor_id.display_name, order.name),
                subtype_xmlid="mail.mt_note",
                body_is_html=True
            )

        return {'type': 'ir.actions.act_window_close'}

    # ------------------------------------------------------------
    # Stock move handling
    # ------------------------------------------------------------
    def _update_or_create_stock_moves(self, order):
        """Update or create stock moves for order lines with correct value computation."""
        StockMove = self.env['stock.move']
        StockMoveLine = self.env['stock.move.line']

        vendor_location = order.vendor_id.property_stock_supplier or self.env.ref('stock.stock_location_suppliers')
        stock_location = self.env.ref('stock.stock_location_stock')

        updated_moves = []


        for line in order.order_line.filtered('active'):

            product = line.product_id
            qty = line.quantity
            price_unit = line.price
            total_value = qty*price_unit



            # 🔍 Fetch move linked to this order line
            move = self.env['stock.move'].search([('backorder_line_id', '=', line.id)], limit=1)

            if move:
                move.with_context(manual_move_update=True).sudo().write({
                    'price_unit': price_unit,
                    'product_id': product.id,
                    'product_uom_qty': qty,
                    'reference': order.name,
                })

                move.with_context(manual_move_update=True).sudo().write({'value': qty * price_unit, })

                # ✅ Update move line or create if missing
                if move.move_line_ids:
                    move.move_line_ids.write({
                        'quantity': qty,
                        'qty_done': qty,
                        'product_uom_id': product.uom_id.id,
                        'location_id': vendor_location.id,
                        'location_dest_id': stock_location.id,
                    })
                else:
                    StockMoveLine.create({
                        # 'move_id': move.id,
                        'product_id': product.id,
                        'product_uom_id': product.uom_id.id,
                        'quantity': qty,
                        'qty_done': qty,
                        'location_id': vendor_location.id,
                        'location_dest_id': stock_location.id,
                        'company_id': order.company_id.id,
                    })


            else:
                # 🆕 Create new move for new order line
                move = StockMove.with_context(manual_move_update=True).create({
                    'product_id': product.id,
                    'product_uom': product.uom_id.id,
                    'location_id': vendor_location.id,
                    'location_dest_id': stock_location.id,
                    'product_uom_qty': qty,
                    'price_unit': price_unit,
                    'value': total_value,
                    'state': 'draft',
                    'origin': order.name,
                    'reference': order.name,
                    'company_id': order.company_id.id,
                    'partner_id': line.vendor_id.id,
                    'backorder_line_id': line.id,
                })

                StockMoveLine.create({
                    # 'move_id': move.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'quantity': qty,
                    'qty_done': qty,
                     'location_id': vendor_location.id,
                    'location_dest_id': stock_location.id,
                    'company_id': order.company_id.id,
                })


            move._action_confirm()
            move._action_assign()
            move.with_context(manual_move_update=True)._action_done()

            # self._recompute_product_cost(move.product_id)


            updated_moves.append(move.id)
            # move.sudo().write({
            #     'product_id': product.id,
            #     'product_uom_qty': qty,
            #     'price_unit': price_unit,
            #     'reference': order.name,
            # })
            move.with_context(manual_move_update=True).sudo().write({'value':qty*price_unit,})
        if updated_moves:
            order.write({'move_ids': [(4, mid) for mid in updated_moves]})
            # order.write({'move_ids': [(6, 0, list(set(updated_moves)))]})






    def _create_return_move(self, move):
        """Reverse a done stock move safely and keep proper reference."""
        StockMove = self.env['stock.move']


        return_move = StockMove.create({
            'product_id': move.product_id.id,
            'product_uom': move.product_uom.id,
            'product_uom_qty': move.product_uom_qty,
            'price_unit': move.price_unit,
            'value': move.value,
            'location_id': move.location_dest_id.id,  # reverse direction
            'location_dest_id': move.location_id.id,
            'origin': f"Return of {move.origin or move.reference}",
            'reference': f"{move.reference or move.origin} (Reversal)",
            'company_id': move.company_id.id,
            'partner_id': move.partner_id.id,
            'state': 'draft',
        })

        # Reverse quantities
        self.env['stock.move.line'].create({
            'move_id': return_move.id,
            'product_id': move.product_id.id,
            'product_uom_id': move.product_uom.id,
            'qty_done': move.product_uom_qty,
            'location_id': move.location_dest_id.id,
            'location_dest_id': move.location_id.id,
            'company_id': move.company_id.id,
        })

        return_move._action_confirm()
        return_move._action_assign()
        return_move._action_done()

        # Update reference for traceability
        move.write({
            'reference': f"{move.reference or ''} → Reversed by {return_move.reference}",
        })

        return return_move



    def _recreate_journal_entry(self, order):
        """Recreate accounting entry for the purchase order and log chatter."""
        # Delete old move if exists
        if order.account_move_id:
            old_move = order.account_move_id
            old_move.button_draft()
            old_move.unlink()

        # Fallback accounts
        expense_account = self.env['account.account'].search([('account_type', '=', 'expense')], limit=1)
        payable_account = order.bank_account_id
        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)

        if not (expense_account and payable_account and journal):
            raise UserError(_("Please configure Expense, Payable accounts, and a General Journal."))

        # Build journal move
        move_lines = []
        for line in order.order_line.filtered('active'):
            move_lines.append((0, 0, {
                'name': f"{line.product_id.display_name} ({order.name})",
                'account_id': line.product_id.categ_id.property_account_expense_categ_id.id or expense_account.id,
                'debit': line.total,
                'credit': 0.0,
                'partner_id': line.vendor_id.id,

            }))

            move_lines.append((0, 0, {
                'name': order.vendor_id.name,
                'account_id': payable_account.id,
                'credit': line.total,
                'debit': 0.0,
                'partner_id': line.vendor_id.id,

            }))

        move_vals = {
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': fields.Date.context_today(self),
            'ref': f'Backorder Purchase: {order.name}',
            'line_ids': move_lines,
        }

        move = self.env['account.move'].create(move_vals)
        move.action_post()

        order.account_move_id = move

        # ✅ Post chatter message on the Journal Entry
        move.message_post(
            body=_(
                "Journal Entry created/updated via Backorder Purchase Update Wizard for order <b>%s</b>.") % order.name,
            subtype_xmlid="mail.mt_note",
            body_is_html=True
        )

        # ✅ Also post to the related order chatter (if enabled)
        if hasattr(order, 'message_post'):
            order.message_post(
                body=_("New Journal Entry <b>%s</b> was created and linked.") % move.name,
                subtype_xmlid="mail.mt_note",
                body_is_html=True
            )

    # ------------------------------------------------------------
    # Product cost updates
    # ------------------------------------------------------------

    def _recompute_product_cost(self, product):
        """
        Recalculate product standard_price as weighted average of all done incoming moves
        (supplier → internal). Does NOT change past move values like PO2.
        """

        StockMove = self.env['stock.move']

        # ✅ Only consider done incoming moves for this product
        done_in_moves = StockMove.search([
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('location_id.usage', '=', 'supplier'),
            ('location_dest_id.usage', '=', 'internal')
        ])

        total_qty = 0.0
        total_value = 0.0
        for move in done_in_moves:
            total_qty += move.product_uom_qty
            total_value += move.value  # use stored move.value (not recomputed)

        if total_qty <= 0:
            return

        avg_cost = total_value / total_qty

        # ✅ Update product cost only
        product.sudo().write({'standard_price': avg_cost})




# ------------------------------------------------------------
# Wizard Line Model
# ------------------------------------------------------------
class BackorderPurchaseUpdateWizardLine(models.TransientModel):
    _name = 'backorder.purchase.update.wizard.line'
    _description = 'Wizard Lines for Backorder Update'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    wizard_id = fields.Many2one('backorder.purchase.update.wizard', string='Wizard')
    line_id = fields.Many2one('backorder.purchase.order.line', string='Original Line')
    product_id = fields.Many2one('product.product', string='Product', required=True)
    vendor_id = fields.Many2one('res.partner', string='Vendor', required=True)
    quantity = fields.Float(string='Quantity', required=True)
    price = fields.Float(string='Unit Price', required=True)
    total = fields.Monetary(string='Total', compute='_compute_total', currency_field='currency_id', store=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    @api.depends('quantity', 'price')
    def _compute_total(self):
        for rec in self:
            rec.total = rec.quantity * rec.price

