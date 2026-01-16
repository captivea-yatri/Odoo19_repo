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

        # 2️⃣ Capture old vendor
        old_vendor = order.vendor_id

        # 3️⃣ Update Vendor if changed
        if self.vendor_id and self.vendor_id != old_vendor:
            order.with_context(ctx).write({'vendor_id': self.vendor_id.id})

            # ✅ Update journal entry partner
            if order.account_move_id:
                move = order.account_move_id
                # If posted, set to draft
                if move.state == 'posted':
                    move.button_draft()
                # Update partner in move & all lines
                move.write({'partner_id': self.vendor_id.id})
                move.line_ids.write({'partner_id': self.vendor_id.id})
                # Repost move
                move.action_post()

        # 3️⃣ Update order lines
        existing_line_ids = [l.line_id.id for l in self.line_ids if l.line_id]
        for line in order.order_line:
            if line.id not in existing_line_ids:
                line.with_context(ctx).write({'active': False})

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
                line = self.env['backorder.purchase.order.line'].create(vals)

            total_qty += line.quantity
            total_value += line.total

        # 4️⃣ Update stock moves
        self._update_or_create_stock_moves(order)

        # 5️⃣ Recreate journal entry
        self._recreate_journal_entry(order)

        # 6️⃣ Update product cost/value
        for line in order.order_line.filtered('active'):
            self._recompute_product_cost(line.product_id)

        # 7️⃣ Reconfirm the order
        order.with_context(ctx).write({'state': 'confirm'})
        if hasattr(order, 'message_post'):
            order.message_post(
                body=_("Vendor changed from <b>%s</b> to <b>%s</b> via Update Wizard.") %
                     (old_vendor.display_name, self.vendor_id.display_name),
                subtype_xmlid="mail.mt_note"
            )
        if order.account_move_id:
            order.account_move_id.message_post(
                body=_("Vendor updated from <b>%s</b> to <b>%s</b> (Backorder: %s)") %
                     (old_vendor.display_name, self.vendor_id.display_name, order.name),
                subtype_xmlid="mail.mt_note"
            )

        return {'type': 'ir.actions.act_window_close'}

    # ------------------------------------------------------------
    # Stock move handling
    # ------------------------------------------------------------
    def _update_or_create_stock_moves(self, order):
        """Keep stock moves in sync with the order lines."""
        StockMove = self.env['stock.move']
        StockMoveLine = self.env['stock.move.line']

        vendor_location = order.vendor_id.property_stock_supplier or self.env.ref('stock.stock_location_suppliers')
        stock_location = self.env.ref('stock.stock_location_stock')

        # Track existing moves by product_id
        existing_moves = {m.product_id.id: m for m in order.move_ids}

        # Track product_ids in current order lines
        current_products = order.order_line.mapped('product_id').ids

        # 1️⃣ Remove moves for deleted lines
        for move in order.move_ids:
            if move.product_id.id not in current_products:
                if move.state == 'done':
                    # Create a return move if already done
                    self._create_return_move(move)
                else:
                    move.unlink()

        # 2️⃣ Process each current order line
        new_moves = []
        for line in order.order_line.filtered('active'):
            product = line.product_id
            qty = line.quantity
            price = line.price
            total_value = line.total

            if product.id in existing_moves:
                move = existing_moves[product.id]

                if move.state == 'done':
                    # Already done → create adjustment
                    diff_qty = qty - move.product_uom_qty
                    if abs(diff_qty) > 0.0001:
                        adj_move = StockMove.create({
                            'description_picking': f"Adjustment for {product.display_name} ({order.name})",
                            'product_id': product.id,
                            'product_uom': product.uom_id.id,
                            'product_uom_qty': abs(diff_qty),
                            'price_unit': price,
                            'value': abs(diff_qty * price),
                            'location_id': vendor_location.id,
                            'location_dest_id': stock_location.id,
                            'origin': order.name,
                            'reference': order.name,
                            'state': 'draft',
                        })
                        StockMoveLine.create({
                            'move_id': adj_move.id,
                            'product_id': product.id,
                            'product_uom_id': product.uom_id.id,
                            'qty_done': abs(diff_qty),
                            'location_id': vendor_location.id,
                            'location_dest_id': stock_location.id,
                        })
                        adj_move._action_confirm()
                        adj_move._action_assign()
                        adj_move._action_done()
                        new_moves.append(adj_move.id)
                else:
                    # Draft move → just update
                    move.write({
                        'product_uom_qty': qty,
                        'price_unit': price,
                        'value': total_value,
                        'description_picking': f"{product.display_name} ({order.name})",
                        'reference': order.name,
                    })

                    # Ensure move lines exist
                    if not move.move_line_ids:
                        StockMoveLine.create({
                            'move_id': move.id,
                            'product_id': product.id,
                            'product_uom_id': product.uom_id.id,
                            'qty_done': qty,
                            'location_id': vendor_location.id,
                            'location_dest_id': stock_location.id,
                        })
                    new_moves.append(move.id)
            else:
                # New product → create move
                move = StockMove.create({
                    'description_picking': f"{product.display_name} ({order.name})",
                    'product_id': product.id,
                    'product_uom': product.uom_id.id,
                    'product_uom_qty': qty,
                    'price_unit': price,
                    'value': total_value,
                    'location_id': vendor_location.id,
                    'location_dest_id': stock_location.id,
                    'origin': order.name,
                    'reference': order.name,
                    'state': 'draft',
                })
                StockMoveLine.create({
                    'move_id': move.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'qty_done': qty,
                    'location_id': vendor_location.id,
                    'location_dest_id': stock_location.id,
                })
                move._action_confirm()
                move._action_assign()
                move._action_done()
                new_moves.append(move.id)

            # Update stock valuation
            self._update_stock_value(order, move)

        # 3️⃣ Link moves to the order
        if new_moves:
            order.write({'move_ids': [(6, 0, list(set(order.move_ids.ids + new_moves)))]})

    def _create_return_move(self, move):
        """Reverse a done stock move instead of cancelling."""
        return_move = self.env['stock.move'].create({
            'product_id': move.product_id.id,
            'product_uom': move.product_uom.id,
            'location_id': move.location_dest_id.id,
            'location_dest_id': move.location_id.id,
            'product_uom_qty': move.product_uom_qty,
            'price_unit': move.price_unit,
            'origin': f"Return of {move.origin or move.reference or move.id}",
            'state': 'draft',
            'partner_id': move.partner_id.id,
            'reference': f"Reversal of {move.reference or move.origin or move.id}",
        })

        self.env['stock.move.line'].create({
            'move_id': return_move.id,
            'product_id': move.product_id.id,
            'product_uom_id': move.product_uom.id,
            'qty_done': move.product_uom_qty,
            'location_id': move.location_dest_id.id,
            'location_dest_id': move.location_id.id,
        })

        return_move._action_confirm()
        return_move._action_assign()
        return_move._action_done()

        move.write({
            'reference': f"{move.reference or ''} | Reversed by {return_move.reference or return_move.origin or 'Return'}"
        })





    def _recreate_journal_entry(self, order):
        """Recreate accounting entry for the purchase order and log chatter."""
        # Delete old move if exists
        if order.account_move_id:
            old_move = order.account_move_id
            old_move.button_draft()
            old_move.unlink()

        # Fallback accounts
        default_expense = self.env['account.account'].search([('account_type', '=', 'expense')], limit=1)
        default_payable = self.env['account.account'].search([('account_type', '=', 'liability_payable')], limit=1)
        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)

        if not (default_expense and default_payable and journal):
            raise UserError(_("Please configure Expense, Payable accounts, and a General Journal."))

        # Build journal move
        move_lines = []
        for line in order.order_line.filtered('active'):
            move_lines.append((0, 0, {
                'name': f"{line.product_id.display_name} ({order.name})",
                'account_id': line.product_id.categ_id.property_account_expense_categ_id.id or default_expense.id,
                'debit': line.total,
                'credit': 0.0,
                'partner_id': order.vendor_id.id,
            }))

        move_lines.append((0, 0, {
            'name': order.vendor_id.name,
            'account_id': order.vendor_id.property_account_payable_id.id or default_payable.id,
            'credit': sum(order.order_line.filtered('active').mapped('total')),
            'debit': 0.0,
            'partner_id': order.vendor_id.id,
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
            subtype_xmlid="mail.mt_note"
        )

        # ✅ Also post to the related order chatter (if enabled)
        if hasattr(order, 'message_post'):
            order.message_post(
                body=_("New Journal Entry <b>%s</b> was created and linked.") % move.name,
                subtype_xmlid="mail.mt_note"
            )

    # ------------------------------------------------------------
    # Product cost updates
    # ------------------------------------------------------------
    def _update_stock_value(self, order, move):
        """Update product's last cost from new move."""
        for line in order.order_line.filtered('active'):
            if move.product_id == line.product_id:
                line.product_id.standard_price = line.price

    def _recompute_product_cost(self, product):
        """Recalculate average cost based on stock quant."""
        quant = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal')
        ], limit=1)
        if quant and quant.quantity:
            product.standard_price = quant.value / quant.quantity


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
